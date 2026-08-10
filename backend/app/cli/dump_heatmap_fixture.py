"""Dump heat_edges + osm_road_edges in a bbox to a portable JSON fixture.

Test-only helper for the Layer-3 golden-snapshot drag-edit E2E spec
(``e2e/tests/drag-edit-layer-3-golden.spec.ts``). NOT a prod API.

See ``docs/drag-edit-e2e-testing-strategy.md`` (Layer 3) for the
motivation. The fixture freezes the heat_edges + osm_road_edges state in
a small bbox so the corridor discovery, waypoint placement and drag
target are reproducible across runs and across machines — the Playwright
spec mounts the fixture, runs the gesture, and asserts on the resulting
``route-draft`` GeoJSON against a checked-in golden snapshot.

## Usage

    python -m app.cli.dump_heatmap_fixture \\
        --bbox 3.85,43.63,3.92,43.68 \\
        --sport gravel \\
        --out e2e/fixtures/clapiers-layer-3.json

``--bbox`` is ``minLon,minLat,maxLon,maxLat``. ``--sport`` filters
heat_edges by partition (``osm_road_edges`` is sport-agnostic and is
always dumped for the bbox). ``--out`` is required (we never write to
stdout — fixtures are MB-scale).

## Output shape

```json
{
    "version": 1,
    "bbox": [3.85, 43.63, 3.92, 43.68],
    "sport": "gravel",
    "min_user_count": 1,
    "heat_edges": [
        {
            "edge_key": "...",
            "user_count": 2,
            "pass_count": 13,
            "forward_count": 7,
            "backward_count": 6,
            "ele_delta_m": 1.2,
            "slope_grade": 0.02,
            "surface_type": "asphalt",
            "highway_type": "secondary",
            "tracktype": null,
            "smoothness": null,
            "trail_network": false,
            "trail_type": null,
            "osm_way_id": 12345,
            "match_confidence": 0.9,
            "match_source": "spatial",
            "surface_confidence": 0.8,
            "geometry": {"type": "LineString", "coordinates": [...]}
        }
    ],
    "osm_road_edges": [
        {
            "tile_key": 845205882,
            "osm_way_id": 12345,
            "segment_idx": 0,
            "surface": "asphalt",
            "highway": "secondary",
            "geometry": {"type": "LineString", "coordinates": [...]},
            "ele_start_m": 50.0,
            "ele_end_m": 52.0,
            "ele_delta_m": 2.0,
            "slope_grade": 0.02,
            "surface_confidence": 0.8
        }
    ],
    "osm_ways": [
        {
            "osm_way_id": 12345,
            "way_geometry": {"type": "LineString", "coordinates": [...]}
        }
    ]
}
```

Version 2 (migration 0056): ``tile_key`` is the BIGINT encoding (see
``app.services.tile_keys``), the per-row ``way_geometry`` moved to the
top-level ``osm_ways`` list (stored once per way, mirroring the
``osm_ways`` side-table). The loader still accepts version-1 fixtures.

The companion ``load_heatmap_fixture.py`` CLI consumes this exact shape.

## Determinism

Rows are emitted ordered by ``edge_key`` (heat_edges),
``(tile_key, osm_way_id, segment_idx)`` (osm_road_edges) and
``osm_way_id`` (osm_ways) so two dumps of the same DB state produce
byte-identical JSON. The Playwright spec relies on this for the
snapshot-diff workflow.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Bump when the fixture schema changes incompatibly. The loader rejects
# any version it doesn't recognise.
FIXTURE_VERSION = 2


@dataclass(frozen=True)
class HeatEdgeRow:
    edge_key: str
    user_count: int
    pass_count: int
    forward_count: int
    backward_count: int
    ele_delta_m: float
    slope_grade: float
    surface_type: str | None
    highway_type: str | None
    tracktype: str | None
    smoothness: str | None
    trail_network: bool
    trail_type: str | None
    osm_way_id: int | None
    match_confidence: float | None
    match_source: str | None
    surface_confidence: float | None
    geometry: dict[str, Any]


@dataclass(frozen=True)
class OsmRoadEdgeRow:
    tile_key: int
    osm_way_id: int
    segment_idx: int
    surface: str | None
    highway: str | None
    geometry: dict[str, Any]
    ele_start_m: float | None
    ele_end_m: float | None
    ele_delta_m: float | None
    slope_grade: float | None
    surface_confidence: float | None


@dataclass(frozen=True)
class OsmWayRow:
    osm_way_id: int
    way_geometry: dict[str, Any]


def _parse_geom(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _dump_heat_edges(
    bbox: tuple[float, float, float, float],
    sport: str,
    min_user_count: int,
) -> list[HeatEdgeRow]:
    min_lon, min_lat, max_lon, max_lat = bbox
    db = SessionLocal()
    try:
        rows = db.execute(
            sa_text(
                """
                SELECT
                    edge_key,
                    user_count,
                    pass_count,
                    forward_count,
                    backward_count,
                    ele_delta_m,
                    slope_grade,
                    surface_type,
                    highway_type,
                    tracktype,
                    smoothness,
                    trail_network,
                    trail_type,
                    osm_way_id,
                    match_confidence,
                    match_source,
                    surface_confidence,
                    ST_AsGeoJSON(geometry) AS geom_json
                FROM heat_edges
                WHERE sport = :sport
                  AND user_count >= :min_uc
                  AND ST_Intersects(
                      geometry,
                      ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
                  )
                ORDER BY edge_key
                """
            ),
            {
                "sport": sport,
                "min_uc": min_user_count,
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat,
            },
        ).fetchall()
    finally:
        db.close()

    out: list[HeatEdgeRow] = []
    for row in rows:
        geom = _parse_geom(row.geom_json)
        if geom is None or geom.get("type") != "LineString":
            # Skip rows we couldn't parse — the fixture is best-effort
            # for unparseable geometry, the routing test won't notice a
            # missing minor edge.
            continue
        out.append(
            HeatEdgeRow(
                edge_key=row.edge_key,
                user_count=int(row.user_count or 0),
                pass_count=int(row.pass_count or 0),
                forward_count=int(row.forward_count or 0),
                backward_count=int(row.backward_count or 0),
                ele_delta_m=float(row.ele_delta_m or 0.0),
                slope_grade=float(row.slope_grade or 0.0),
                surface_type=row.surface_type,
                highway_type=row.highway_type,
                tracktype=row.tracktype,
                smoothness=row.smoothness,
                trail_network=bool(row.trail_network),
                trail_type=row.trail_type,
                osm_way_id=int(row.osm_way_id) if row.osm_way_id is not None else None,
                match_confidence=(
                    float(row.match_confidence) if row.match_confidence is not None else None
                ),
                match_source=row.match_source,
                surface_confidence=(
                    float(row.surface_confidence)
                    if row.surface_confidence is not None
                    else None
                ),
                geometry=geom,
            )
        )
    return out


def _dump_osm_road_edges(
    bbox: tuple[float, float, float, float],
) -> list[OsmRoadEdgeRow]:
    min_lon, min_lat, max_lon, max_lat = bbox
    db = SessionLocal()
    try:
        rows = db.execute(
            sa_text(
                """
                SELECT
                    tile_key,
                    osm_way_id,
                    segment_idx,
                    surface,
                    highway,
                    ST_AsGeoJSON(geometry) AS geom_json,
                    ele_start_m,
                    ele_end_m,
                    ele_delta_m,
                    slope_grade,
                    surface_confidence
                FROM osm_road_edges
                WHERE ST_Intersects(
                    geometry,
                    ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
                )
                ORDER BY tile_key, osm_way_id, segment_idx
                """
            ),
            {
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat,
            },
        ).fetchall()
    finally:
        db.close()

    out: list[OsmRoadEdgeRow] = []
    for row in rows:
        geom = _parse_geom(row.geom_json)
        if geom is None or geom.get("type") != "LineString":
            continue
        out.append(
            OsmRoadEdgeRow(
                tile_key=int(row.tile_key),
                osm_way_id=int(row.osm_way_id),
                segment_idx=int(row.segment_idx),
                surface=row.surface,
                highway=row.highway,
                geometry=geom,
                ele_start_m=float(row.ele_start_m) if row.ele_start_m is not None else None,
                ele_end_m=float(row.ele_end_m) if row.ele_end_m is not None else None,
                ele_delta_m=float(row.ele_delta_m) if row.ele_delta_m is not None else None,
                slope_grade=float(row.slope_grade) if row.slope_grade is not None else None,
                surface_confidence=(
                    float(row.surface_confidence)
                    if row.surface_confidence is not None
                    else None
                ),
            )
        )
    return out


def _dump_osm_ways(osm_way_ids: list[int]) -> list[OsmWayRow]:
    """Dump the ``osm_ways`` rows referenced by the dumped segments."""
    if not osm_way_ids:
        return []
    db = SessionLocal()
    try:
        rows = db.execute(
            sa_text(
                """
                SELECT osm_way_id, ST_AsGeoJSON(way_geometry) AS way_geom_json
                FROM osm_ways
                WHERE osm_way_id = ANY(:ids)
                ORDER BY osm_way_id
                """
            ),
            {"ids": osm_way_ids},
        ).fetchall()
    finally:
        db.close()

    out: list[OsmWayRow] = []
    for row in rows:
        way_geom = _parse_geom(row.way_geom_json)
        if way_geom is None or way_geom.get("type") != "LineString":
            continue
        out.append(OsmWayRow(osm_way_id=int(row.osm_way_id), way_geometry=way_geom))
    return out


def dump_fixture(
    bbox: tuple[float, float, float, float],
    sport: str,
    min_user_count: int,
) -> dict[str, Any]:
    """Build the in-memory fixture payload — ``main`` writes it to disk."""
    heat_edges = _dump_heat_edges(bbox, sport, min_user_count)
    osm_road_edges = _dump_osm_road_edges(bbox)
    osm_ways = _dump_osm_ways(sorted({r.osm_way_id for r in osm_road_edges}))
    return {
        "version": FIXTURE_VERSION,
        "bbox": list(bbox),
        "sport": sport,
        "min_user_count": min_user_count,
        "heat_edges": [asdict(r) for r in heat_edges],
        "osm_road_edges": [asdict(r) for r in osm_road_edges],
        "osm_ways": [asdict(r) for r in osm_ways],
    }


# ── CLI entry point ──────────────────────────────────────────────────────────


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "bbox must be 'minLon,minLat,maxLon,maxLat' (4 comma-separated floats)"
        )
    try:
        nums = tuple(float(p) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"bbox values must be floats: {exc}") from exc
    min_lon, min_lat, max_lon, max_lat = nums
    if min_lon >= max_lon or min_lat >= max_lat:
        raise argparse.ArgumentTypeError(
            "bbox must have min < max for both lon and lat"
        )
    return (min_lon, min_lat, max_lon, max_lat)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dump heat_edges + osm_road_edges in a bbox to a portable JSON "
            "fixture (Layer-3 drag-edit E2E test)."
        ),
    )
    parser.add_argument(
        "--bbox",
        required=True,
        type=_parse_bbox,
        help="minLon,minLat,maxLon,maxLat (e.g. '3.85,43.63,3.92,43.68')",
    )
    parser.add_argument(
        "--sport",
        required=True,
        help="heat_edges partition (road/gravel/mtb/offroad/running)",
    )
    parser.add_argument(
        "--min-user-count",
        type=int,
        default=1,
        help="minimum user_count per heat_edge (default 1 = dev K-anonymity)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="write the JSON fixture to this path",
    )
    args = parser.parse_args(argv)

    if args.min_user_count < 1:
        parser.error("--min-user-count must be >= 1")

    fixture = dump_fixture(
        bbox=args.bbox,
        sport=args.sport,
        min_user_count=args.min_user_count,
    )

    # `separators=(',', ':')` produces a compact but still diff-able
    # output; pretty-printing would multiply size by ~4x. The loader
    # accepts either.
    payload = json.dumps(fixture, separators=(",", ":"), sort_keys=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(payload)
    logger.info(
        "Wrote fixture %s (%d heat_edges, %d osm_road_edges, %d osm_ways, %d bytes)",
        args.out,
        len(fixture["heat_edges"]),
        len(fixture["osm_road_edges"]),
        len(fixture["osm_ways"]),
        len(payload),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
