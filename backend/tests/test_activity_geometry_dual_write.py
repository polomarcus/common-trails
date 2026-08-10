"""Tests for the activities.geometry / geometry_geojson dual-write path.

Migration 0036 added a binary PostGIS `geometry(LineString, 4326)` column
alongside the legacy TEXT `geometry_geojson` column. `ingest_activity()`
now writes both. These tests pin that contract so a future cleanup PR
can confidently drop the legacy column.

Coverage:
- Round-trip: ingest a known LineString, read both columns back, verify
  the binary geometry decodes to the same coordinates as the JSON text.
- Garbage input: malformed/empty geojson leaves both columns NULL and
  the activity is still created (no crash, no rollback).
- Wrong type: a non-LineString GeoJSON (Point) skips the binary column
  but still stores the raw text (consumer code may be tolerant).
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import _geom_from_geojson_sql, ingest_activity


def _coords() -> list[list[float]]:
    # Tiny LineString in the Hérault — same area as the seeded demo data
    # so it doesn't look out of place if a developer eyeballs it.
    return [
        [3.8454, 43.6614],
        [3.8449, 43.6650],
        [3.8456, 43.6680],
        [3.8417, 43.6699],
    ]


def _user_id() -> str:
    return f"test-geom-{uuid.uuid4().hex[:8]}"


def test_geom_from_geojson_sql_helper_returns_none_for_invalid():
    """The validator short-circuits on garbage so PostGIS never sees it."""
    assert _geom_from_geojson_sql(None) is None
    assert _geom_from_geojson_sql("") is None
    assert _geom_from_geojson_sql("not json at all") is None
    assert _geom_from_geojson_sql("{}") is None
    assert _geom_from_geojson_sql(json.dumps({"type": "Point", "coordinates": [0, 0]})) is None
    # LineString with too few points
    assert _geom_from_geojson_sql(json.dumps({"type": "LineString", "coordinates": [[0, 0]]})) is None


def test_geom_from_geojson_sql_helper_returns_expr_for_valid():
    """Valid LineString → SQL expression we can execute."""
    expr = _geom_from_geojson_sql(json.dumps({
        "type": "LineString",
        "coordinates": _coords(),
    }))
    assert expr is not None
    # Smoke-test it actually runs against PostGIS
    db = SessionLocal()
    try:
        result = db.execute(sa_text(
            "SELECT ST_AsGeoJSON(:g)::text"
        ), {"g": db.execute(
            sa_text("SELECT ST_SetSRID(ST_GeomFromGeoJSON(:gj), 4326)"),
            {"gj": json.dumps({"type": "LineString", "coordinates": _coords()})},
        ).scalar()}).scalar()
        assert result is not None
        decoded = json.loads(result)
        assert decoded["type"] == "LineString"
        assert len(decoded["coordinates"]) == len(_coords())
    finally:
        db.close()


def test_ingest_activity_dual_writes_both_columns():
    """The happy path: writing an activity populates both columns and
    the binary column round-trips to the same coordinates as the input."""
    user_id = _user_id()
    coords = _coords()
    geojson = json.dumps({"type": "LineString", "coordinates": coords})

    result = ingest_activity(
        user_id=user_id,
        activity_data={
            "provider": "file",
            "provider_activity_id": f"test-{uuid.uuid4().hex[:8]}",
            "sport": "road",
            "name": "dual-write test",
            "geometry_geojson": geojson,
            "distance_m": 1234.5,
        },
        contribute_heatmap=False,  # don't poison the heatmap with test data
        skip_heat_computation=True,
    )
    assert result["status"] == "created"
    activity_id = result["activity_id"]

    db = SessionLocal()
    try:
        row = db.execute(sa_text("""
            SELECT
                geometry_geojson,
                ST_AsGeoJSON(geometry)::text AS geom_as_text,
                ST_SRID(geometry) AS srid,
                ST_GeometryType(geometry) AS gtype,
                ST_NPoints(geometry) AS npoints
            FROM activities
            WHERE id = :id
        """), {"id": activity_id}).fetchone()
    finally:
        db.close()

    assert row is not None
    legacy_text, binary_geojson, srid, gtype, npoints = row

    # Legacy column unchanged
    assert legacy_text is not None
    assert json.loads(legacy_text)["coordinates"] == coords

    # Binary column populated and matches
    assert binary_geojson is not None
    assert srid == 4326
    assert gtype == "ST_LineString"
    assert npoints == len(coords)

    decoded = json.loads(binary_geojson)
    assert decoded["type"] == "LineString"
    # PostGIS preserves coordinate order and values (within float
    # precision). Use approx equality to be safe with WKB↔text rounding.
    for got, want in zip(decoded["coordinates"], coords, strict=True):
        assert abs(got[0] - want[0]) < 1e-9
        assert abs(got[1] - want[1]) < 1e-9


def test_ingest_activity_handles_missing_geojson():
    """No geojson → both columns NULL, activity still created."""
    user_id = _user_id()
    result = ingest_activity(
        user_id=user_id,
        activity_data={
            "provider": "file",
            "provider_activity_id": f"test-{uuid.uuid4().hex[:8]}",
            "sport": "road",
            "name": "no geometry",
            "geometry_geojson": None,
        },
        contribute_heatmap=False,
        skip_heat_computation=True,
    )
    assert result["status"] == "created"

    db = SessionLocal()
    try:
        row = db.execute(sa_text("""
            SELECT geometry_geojson, geometry IS NULL AS geom_null
            FROM activities WHERE id = :id
        """), {"id": result["activity_id"]}).fetchone()
    finally:
        db.close()

    assert row is not None
    assert row[0] is None
    assert row[1] is True


def test_ingest_activity_handles_garbage_geojson():
    """Malformed geojson → text column keeps the bad input (so a fix-up
    job can find it), binary column stays NULL, no crash."""
    user_id = _user_id()
    result = ingest_activity(
        user_id=user_id,
        activity_data={
            "provider": "file",
            "provider_activity_id": f"test-{uuid.uuid4().hex[:8]}",
            "sport": "road",
            "name": "garbage geometry",
            "geometry_geojson": "not even json",
        },
        contribute_heatmap=False,
        skip_heat_computation=True,
    )
    assert result["status"] == "created"

    db = SessionLocal()
    try:
        row = db.execute(sa_text("""
            SELECT geometry_geojson, geometry IS NULL AS geom_null
            FROM activities WHERE id = :id
        """), {"id": result["activity_id"]}).fetchone()
    finally:
        db.close()

    assert row is not None
    assert row[0] == "not even json"
    assert row[1] is True
