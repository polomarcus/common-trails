"""Load a Layer-3 heatmap fixture into the dev DB.

Symmetric counterpart to ``dump_heatmap_fixture.py``. Reads the JSON
fixture, deletes any heat_edges + osm_road_edges that intersect the
fixture bbox, and re-INSERTS the fixture rows. Idempotent — re-running
on the same DB produces the same final state.

## Usage

    python -m app.cli.load_heatmap_fixture \\
        --fixture e2e/fixtures/clapiers-layer-3.json

Or, when streaming from stdin (the Playwright spec pipes through
``docker compose exec -T backend``):

    cat e2e/fixtures/clapiers-layer-3.json | python -m app.cli.load_heatmap_fixture

## Idempotence

The loader scopes its DELETE to the fixture's bbox + sport: only
``heat_edges`` rows for that sport whose geometry intersects the bbox
are deleted, and only ``osm_road_edges`` whose geometry intersects the
bbox. Other sports + other regions are untouched. heat_edge_contributors
rows are cascade-deleted by the trigger on heat_edges (migration 0046).

## Safety

This CLI is destructive — it WIPES data inside the bbox before insert.
It refuses to run when ``ENVIRONMENT=production`` to prevent an
operator from accidentally smashing prod heatmap rows. Set
``FIXTURE_LOAD_FORCE=1`` to override (the test harness does NOT set
this).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.tile_keys import tile_key_from_legacy

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Mirror dump_heatmap_fixture.FIXTURE_VERSION — kept duplicated rather
# than imported to keep this module independently runnable in case
# future refactors split the CLIs. Version 1 (pre-0056: TEXT tile_key,
# per-row way_geometry) is still accepted — the loader converts on the fly.
SUPPORTED_VERSIONS = {1, 2}


def _refuse_in_production() -> None:
    env = os.environ.get("ENVIRONMENT", "").lower()
    if env == "production" and not os.environ.get("FIXTURE_LOAD_FORCE"):
        raise RuntimeError(
            "load_heatmap_fixture refuses to run in production. "
            "Set FIXTURE_LOAD_FORCE=1 to override (you almost certainly should not)."
        )


def _validate_fixture(fixture: dict[str, Any]) -> None:
    version = fixture.get("version")
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(
            f"fixture version {version!r} unsupported (this loader handles {SUPPORTED_VERSIONS})"
        )
    bbox = fixture.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError("fixture.bbox must be a 4-element [minLon, minLat, maxLon, maxLat] list")
    sport = fixture.get("sport")
    if not isinstance(sport, str) or not sport:
        raise ValueError("fixture.sport must be a non-empty string")
    if not isinstance(fixture.get("heat_edges"), list):
        raise ValueError("fixture.heat_edges must be a list")
    if not isinstance(fixture.get("osm_road_edges"), list):
        raise ValueError("fixture.osm_road_edges must be a list")


def _wipe_bbox(
    db: Any,
    bbox: tuple[float, float, float, float],
    sport: str,
) -> tuple[int, int]:
    """Delete heat_edges for ``sport`` + osm_road_edges in the bbox.

    Returns ``(heat_edges_deleted, osm_road_edges_deleted)``."""
    min_lon, min_lat, max_lon, max_lat = bbox
    params = {
        "sport": sport,
        "min_lon": min_lon,
        "min_lat": min_lat,
        "max_lon": max_lon,
        "max_lat": max_lat,
    }
    # heat_edge_contributors cascade via the trigger on heat_edges
    # (migration 0046). Don't touch them explicitly.
    heat_del = db.execute(
        sa_text(
            """
            DELETE FROM heat_edges
            WHERE sport = :sport
              AND ST_Intersects(
                  geometry,
                  ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
              )
            """
        ),
        params,
    ).rowcount or 0
    osm_del = db.execute(
        sa_text(
            """
            DELETE FROM osm_road_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            """
        ),
        params,
    ).rowcount or 0
    return heat_del, osm_del


_HEAT_INSERT_SQL = sa_text(
    """
    INSERT INTO heat_edges (
        edge_key, sport, user_count, pass_count, forward_count, backward_count,
        ele_delta_m, slope_grade, surface_type, highway_type, tracktype,
        smoothness, trail_network, trail_type, osm_way_id, match_confidence,
        match_source, surface_confidence, geometry
    ) VALUES (
        :edge_key, :sport, :user_count, :pass_count, :forward_count, :backward_count,
        :ele_delta_m, :slope_grade, :surface_type, :highway_type, :tracktype,
        :smoothness, :trail_network, :trail_type, :osm_way_id, :match_confidence,
        :match_source, :surface_confidence, ST_GeomFromGeoJSON(:geometry)
    )
    ON CONFLICT (edge_key, sport) DO NOTHING
    """
)

_OSM_INSERT_SQL = sa_text(
    """
    INSERT INTO osm_road_edges (
        tile_key, osm_way_id, segment_idx, surface, highway, geometry,
        ele_start_m, ele_end_m, ele_delta_m, slope_grade,
        surface_confidence
    ) VALUES (
        :tile_key, :osm_way_id, :segment_idx, :surface, :highway,
        ST_GeomFromGeoJSON(:geometry),
        :ele_start_m, :ele_end_m, :ele_delta_m, :slope_grade,
        :surface_confidence
    )
    """
)

# Fixture rows land in the DEFAULT partition via the column default
# region='adhoc'. Way polylines upsert into the osm_ways side-table.
_OSM_WAY_UPSERT_SQL = sa_text(
    """
    INSERT INTO osm_ways (osm_way_id, way_geometry, region)
    VALUES (:osm_way_id, ST_GeomFromGeoJSON(:way_geometry), 'adhoc')
    ON CONFLICT (osm_way_id) DO UPDATE
    SET way_geometry = EXCLUDED.way_geometry
    """
)


def _insert_heat_edges(db: Any, sport: str, rows: list[dict[str, Any]]) -> int:
    inserted = 0
    for r in rows:
        geom = r.get("geometry")
        if not isinstance(geom, dict):
            continue
        params = {
            "edge_key": r["edge_key"],
            "sport": sport,
            "user_count": int(r.get("user_count", 0)),
            "pass_count": int(r.get("pass_count", 0)),
            "forward_count": int(r.get("forward_count", 0)),
            "backward_count": int(r.get("backward_count", 0)),
            "ele_delta_m": float(r.get("ele_delta_m") or 0.0),
            "slope_grade": float(r.get("slope_grade") or 0.0),
            "surface_type": r.get("surface_type"),
            "highway_type": r.get("highway_type"),
            "tracktype": r.get("tracktype"),
            "smoothness": r.get("smoothness"),
            "trail_network": bool(r.get("trail_network", False)),
            "trail_type": r.get("trail_type"),
            "osm_way_id": r.get("osm_way_id"),
            "match_confidence": r.get("match_confidence"),
            "match_source": r.get("match_source"),
            "surface_confidence": r.get("surface_confidence"),
            "geometry": json.dumps(geom),
        }
        db.execute(_HEAT_INSERT_SQL, params)
        inserted += 1
    return inserted


def _insert_osm_road_edges(db: Any, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Insert segment rows; returns (segments_inserted, legacy_ways_upserted).

    Version-1 fixtures carry a TEXT ``"14/x/y"`` tile_key + a per-row
    ``way_geometry`` — converted on the fly (tile_key re-encoded, the way
    polyline upserted into ``osm_ways``).
    """
    inserted = 0
    legacy_ways = 0
    legacy_way_seen: set[int] = set()
    for r in rows:
        geom = r.get("geometry")
        if not isinstance(geom, dict):
            continue
        params = {
            "tile_key": tile_key_from_legacy(r["tile_key"]),
            "osm_way_id": int(r["osm_way_id"]),
            "segment_idx": int(r["segment_idx"]),
            "surface": r.get("surface"),
            "highway": r.get("highway"),
            "geometry": json.dumps(geom),
            "ele_start_m": r.get("ele_start_m"),
            "ele_end_m": r.get("ele_end_m"),
            "ele_delta_m": r.get("ele_delta_m"),
            "slope_grade": r.get("slope_grade"),
            "surface_confidence": r.get("surface_confidence"),
        }
        db.execute(_OSM_INSERT_SQL, params)
        inserted += 1

        way_geom = r.get("way_geometry")  # version-1 fixtures only
        if isinstance(way_geom, dict) and params["osm_way_id"] not in legacy_way_seen:
            db.execute(_OSM_WAY_UPSERT_SQL, {
                "osm_way_id": params["osm_way_id"],
                "way_geometry": json.dumps(way_geom),
            })
            legacy_way_seen.add(params["osm_way_id"])
            legacy_ways += 1
    return inserted, legacy_ways


def _insert_osm_ways(db: Any, rows: list[dict[str, Any]]) -> int:
    inserted = 0
    for r in rows:
        way_geom = r.get("way_geometry")
        if not isinstance(way_geom, dict):
            continue
        db.execute(_OSM_WAY_UPSERT_SQL, {
            "osm_way_id": int(r["osm_way_id"]),
            "way_geometry": json.dumps(way_geom),
        })
        inserted += 1
    return inserted


def load_fixture(fixture: dict[str, Any]) -> dict[str, int]:
    """Apply the fixture to the DB. Returns row counts as a diagnostic."""
    _validate_fixture(fixture)
    bbox = tuple(float(x) for x in fixture["bbox"])
    sport = fixture["sport"]
    heat_rows = fixture["heat_edges"]
    osm_rows = fixture["osm_road_edges"]
    osm_way_rows = fixture.get("osm_ways", [])  # absent in version-1 fixtures

    db = SessionLocal()
    try:
        heat_del, osm_del = _wipe_bbox(db, bbox, sport)
        heat_ins = _insert_heat_edges(db, sport, heat_rows)
        osm_ins, legacy_ways = _insert_osm_road_edges(db, osm_rows)
        ways_ins = _insert_osm_ways(db, osm_way_rows) + legacy_ways
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {
        "heat_edges_deleted": heat_del,
        "osm_road_edges_deleted": osm_del,
        "heat_edges_inserted": heat_ins,
        "osm_road_edges_inserted": osm_ins,
        "osm_ways_upserted": ways_ins,
    }


# ── CLI entry point ──────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Load a Layer-3 heatmap fixture (heat_edges + osm_road_edges) "
            "into the dev DB."
        ),
    )
    parser.add_argument(
        "--fixture",
        help="path to the fixture JSON (default: read from stdin)",
    )
    args = parser.parse_args(argv)

    _refuse_in_production()

    if args.fixture:
        with open(args.fixture, encoding="utf-8") as fh:
            fixture = json.load(fh)
    else:
        if sys.stdin.isatty():
            parser.error("no --fixture given and stdin is a TTY")
        fixture = json.load(sys.stdin)

    counts = load_fixture(fixture)
    logger.info(
        "Loaded fixture sport=%s bbox=%s: deleted %d heat_edges + %d osm_road_edges, "
        "inserted %d heat_edges + %d osm_road_edges",
        fixture.get("sport"),
        fixture.get("bbox"),
        counts["heat_edges_deleted"],
        counts["osm_road_edges_deleted"],
        counts["heat_edges_inserted"],
        counts["osm_road_edges_inserted"],
    )
    # JSON to stdout so the test harness can parse + assert.
    print(json.dumps({"loaded": True, **counts}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
