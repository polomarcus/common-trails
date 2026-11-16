"""Build the OSM surface tiles used by the off-road inference.

Runs OFFLINE (a laptop with the ``osmium`` CLI), from one or more Geofabrik
``.osm.pbf`` extracts — never in prod. Writes, into ``--out`` (a local
directory):

* ``t_<lat>_<lon>.bin`` — one 0.5° tile: the grid cells of paved and unpaved
  ways (``app.services.surface_inference`` format);
* ``manifest.json`` — the covered tiles + the sources' OSM timestamps.

Then upload the directory where the jobs read it (default
``gs://<UPLOADS_BUCKET>/osm-surface/v1``, see ``surface_inference.surface_url``)::

    python -m app.cli.build_osm_surface_tiles --pbf occitanie.osm.pbf --out /tmp/osm-surface
    gcloud storage cp -r /tmp/osm-surface/* gs://<bucket>/osm-surface/v1/

Pass ALL the extracts of a run together: a tile is written whole from this
run's ways, so a tile straddling two extracts built in two runs would keep
only the last one. ``--bbox`` limits the build to an area.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import tempfile
from datetime import UTC, datetime

from app.services import surface_inference as si

log = logging.getLogger("build_osm_surface_tiles")


def _osm_timestamp(pbf: str) -> str | None:
    try:
        out = subprocess.run(
            ["osmium", "fileinfo", "-g", "header.option.timestamp", pbf],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return out or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _iter_ways(pbf: str, bbox: str | None, workdir: str):
    """Yield ``(tags, coords)`` for every highway way of ``pbf``."""
    src = pbf
    if bbox:
        src = os.path.join(workdir, "zone.osm.pbf")
        subprocess.run(["osmium", "extract", "-b", bbox, pbf, "-o", src, "--overwrite"], check=True)
    hw = os.path.join(workdir, "highways.osm.pbf")
    subprocess.run(["osmium", "tags-filter", src, "w/highway", "-o", hw, "--overwrite"], check=True)
    seq = os.path.join(workdir, "highways.geojsonseq")
    subprocess.run(
        ["osmium", "export", hw, "-f", "geojsonseq", "--geometry-types=linestring", "-o", seq, "--overwrite"],
        check=True,
    )
    with open(seq, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            feat = json.loads(line)
            geom = feat.get("geometry") or {}
            if geom.get("type") == "LineString":
                yield feat.get("properties") or {}, geom.get("coordinates") or []


def build(pbfs: list[str], out: str, bbox: str | None = None) -> dict:
    tiles: dict[str, tuple[set, set]] = {}
    sources = []
    for pbf in pbfs:
        sources.append({"file": os.path.basename(pbf), "osm_timestamp": _osm_timestamp(pbf)})
        with tempfile.TemporaryDirectory() as wd:
            for name, (paved, unpaved) in si.tiles_from_ways(_iter_ways(pbf, bbox, wd)).items():
                cur = tiles.setdefault(name, (set(), set()))
                cur[0].update(paved)
                cur[1].update(unpaved)
    os.makedirs(out, exist_ok=True)
    for name, (paved, unpaved) in tiles.items():
        with open(os.path.join(out, name), "wb") as fh:
            fh.write(si.encode_tile(paved, unpaved))
    manifest = si.manifest_for(
        tiles,
        sources=sources,
        bbox=bbox,
        built_at=datetime.now(UTC).isoformat(),
    )
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    log.info(
        "%d tiles → %s (paved cells %d, unpaved cells %d)",
        len(tiles),
        out,
        sum(len(p) for p, _ in tiles.values()),
        sum(len(u) for _, u in tiles.values()),
    )
    return manifest


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pbf", nargs="+", required=True, help="One or more .osm.pbf extracts.")
    parser.add_argument("--out", required=True, help="Output directory (tiles + manifest.json).")
    parser.add_argument("--bbox", default=None, help="min_lon,min_lat,max_lon,max_lat (optional).")
    args = parser.parse_args()
    build(args.pbf, args.out, args.bbox)
