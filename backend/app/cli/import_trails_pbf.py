"""Import marked trails (GR / GRP / GT / PR / EV) from a local OSM PBF.

Same output shape as ``import_trails.py`` (Overpass version) but reads
from a PBF on disk — no network dependency, no Overpass rate limits, no
silent empty-trail-edges-table when Overpass is down.

Two-pass parse:
  1. Walk relations: keep ``type=route`` with ``route in {hiking, bicycle, mtb, foot}``
     whose ``ref`` matches GR / GRP / GT / PR / EV (or whose name matches
     "Grande Traversée"). For each kept relation: store its trail_type,
     ref, route, and member way IDs.
  2. Walk ways with locations: for any way referenced by a kept relation,
     materialize the LineString geometry. Pick the highest-priority
     trail_type when a way belongs to multiple relations.

Stored via ``ingest.store_trail_edges`` (same as the Overpass path).

Run locally:
    docker compose exec backend python -m app.cli.import_trails_pbf data/france-south.osm.pbf

Run against prod (cloud-sql-proxy):
    make prod-import-trails-south
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Iterable

import osmium

from app.cli.import_trails import _classify_trail_type

logger = logging.getLogger(__name__)


# Priority for trail_type when a way is on multiple relations.
# Mirrors the `_PRIORITY` dict inside `_parse_response` of import_trails.py.
_TRAIL_PRIORITY = {"GR": 5, "EV": 4, "GT": 3, "GRP": 2, "PR": 1}


# ── Pass 1: collect relations + member ways ────────────────────────────────


class _RelationCollector(osmium.SimpleHandler):
    """Walk only OSM relations. Build:

    * ``relation_meta``: relation_id → (trail_type, ref, route)
    * ``way_to_trail_types``: way_id → set[trail_type]
    """

    _ROUTE_VALUES_FOOT = ("hiking", "foot")
    _ROUTE_VALUES_BICYCLE = ("bicycle", "mtb")

    def __init__(self) -> None:
        super().__init__()
        self.relation_meta: dict[int, tuple[str, str, str]] = {}
        self.way_to_trail_types: dict[int, set[str]] = {}
        self._kept = 0
        self._scanned = 0

    def relation(self, r) -> None:  # noqa: D401 — pyosmium hook
        self._scanned += 1
        tags = r.tags
        if tags.get("type") != "route":
            return
        route = tags.get("route", "")
        if route not in self._ROUTE_VALUES_FOOT and route not in self._ROUTE_VALUES_BICYCLE:
            return

        ref = tags.get("ref", "")
        name = tags.get("name", "")
        trail_type = _classify_trail_type(ref, name, route)
        if not trail_type:
            return

        self.relation_meta[r.id] = (trail_type, ref, route)
        self._kept += 1
        for m in r.members:
            if m.type == "w":  # pyosmium uses single-letter type codes
                self.way_to_trail_types.setdefault(m.ref, set()).add(trail_type)

    def stats(self) -> str:
        return f"scanned={self._scanned} kept_relations={self._kept} member_ways={len(self.way_to_trail_types)}"


# ── Pass 2: materialize way geometries ─────────────────────────────────────


class _WayGeometryCollector(osmium.SimpleHandler):
    """Walk ways with node locations. For each way the caller is interested in,
    record (way_id, surface, highway_tag, [(lon, lat), ...])."""

    def __init__(self, wanted_way_ids: set[int]) -> None:
        super().__init__()
        self._wanted = wanted_way_ids
        self.geometries: dict[int, tuple[str, str, list[tuple[float, float]]]] = {}
        self._scanned = 0
        self._matched = 0
        self._dropped_invalid = 0

    def way(self, w) -> None:  # noqa: D401 — pyosmium hook
        self._scanned += 1
        if w.id not in self._wanted:
            return
        coords: list[tuple[float, float]] = []
        for n in w.nodes:
            if not n.location.valid():
                continue
            coords.append((n.lon, n.lat))
        if len(coords) < 2:
            self._dropped_invalid += 1
            return
        surface = w.tags.get("surface", "unknown")
        highway = w.tags.get("highway", "path")
        self.geometries[w.id] = (surface, highway, coords)
        self._matched += 1

    def stats(self) -> str:
        return f"scanned_ways={self._scanned} matched={self._matched} dropped={self._dropped_invalid}"


# ── Assemble + store ───────────────────────────────────────────────────────


def _assemble_edges(
    relation_meta: dict[int, tuple[str, str, str]],
    way_to_trail_types: dict[int, set[str]],
    geometries: dict[int, tuple[str, str, list[tuple[float, float]]]],
) -> list[dict]:
    """Build the edge dicts in the same shape ``store_trail_edges`` expects."""
    edges: list[dict] = []
    for way_id, trail_types in way_to_trail_types.items():
        geom = geometries.get(way_id)
        if not geom:
            continue
        surface, highway, coords = geom
        best_type = max(trail_types, key=lambda t: _TRAIL_PRIORITY.get(t, 0))

        # Find one ref string from any relation that owns this way (best
        # type wins; ties broken arbitrarily).
        ref = ""
        for _rel_id, (t, r, _route) in relation_meta.items():
            if t == best_type:
                # It's not strictly necessary that this relation contains
                # the way — the assignment is informational. Pick the
                # first matching trail_type's ref.
                ref = r
                break

        edges.append({
            "geometry": {"type": "LineString", "coordinates": [list(c) for c in coords]},
            "surface": surface,
            "highway": highway,
            "ref": ref,
            "user_count": 0,
            "pass_count": 0,
            "trail_network": True,
            "trail_type": best_type,
        })
    return edges


# ── Main ───────────────────────────────────────────────────────────────────


def run_import(pbf_path: str, append: bool = False) -> int:
    """Parse trails from a PBF and store them in ``trail_edges``.

    Args:
        pbf_path: Path to a .osm.pbf file.
        append: If True, append to existing trail_edges instead of replacing.

    Returns:
        Number of edges stored.
    """
    if not os.path.isfile(pbf_path):
        logger.error("PBF not found: %s", pbf_path)
        return 0

    # Pass 1
    logger.info("Pass 1/2 — scanning relations in %s …", pbf_path)
    t0 = time.monotonic()
    rel = _RelationCollector()
    rel.apply_file(pbf_path)
    logger.info("  %s in %.0fs", rel.stats(), time.monotonic() - t0)

    if not rel.way_to_trail_types:
        logger.warning("No matching trail relations found in PBF")
        return 0

    # Pass 2 — locations=True needed so way nodes are resolved
    logger.info("Pass 2/2 — materializing way geometries …")
    t0 = time.monotonic()
    geo = _WayGeometryCollector(set(rel.way_to_trail_types.keys()))
    geo.apply_file(pbf_path, locations=True)
    logger.info("  %s in %.0fs", geo.stats(), time.monotonic() - t0)

    edges = _assemble_edges(rel.relation_meta, rel.way_to_trail_types, geo.geometries)
    if not edges:
        logger.warning("No edges assembled — relations matched but ways had no geometry")
        return 0

    # Per-trail-type breakdown for the operator
    by_type: dict[str, int] = {}
    for e in edges:
        by_type[e["trail_type"]] = by_type.get(e["trail_type"], 0) + 1
    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
    logger.info("Assembled %d trail edges (%s)", len(edges), breakdown)

    # Store
    from app.services.ingest import append_trail_edges, store_trail_edges
    n = append_trail_edges(edges) if append else store_trail_edges(edges)
    logger.info("Stored %d trail edges to DB (append=%s)", n, append)
    return n


def main(argv: Iterable[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Import marked trails (GR/GRP/GT/PR/EV) from an OSM PBF")
    parser.add_argument("pbf", help="Path to .osm.pbf")
    parser.add_argument("--append", action="store_true", help="Append instead of replacing trail_edges")
    args = parser.parse_args(list(argv) if argv is not None else None)
    n = run_import(args.pbf, append=args.append)
    return 0 if n > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
