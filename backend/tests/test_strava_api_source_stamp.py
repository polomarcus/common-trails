"""The Strava-API ingest paths stamp ``source='strava_api'`` (②).

Drives the REAL ingest entry points (``ingest_activities_bulk`` used by the
bulk import / webhook worker, and ``ingest_activity`` used by resync), not an
inline mirror. FAILS on the pre-change code (no ``source`` param / column
written) and PASSES after.
"""
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import ingest_activities_bulk, ingest_activity
from app.services.provenance import COMMUNITY_SOURCE, STRAVA_API_SOURCE

_USER = "src-stamp-user"
_GEO = json.dumps({"type": "LineString", "coordinates": [[3.9, 43.7], [3.901, 43.701]]})


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _USER})
        db.commit()
    finally:
        db.close()


def _source_of(activity_id: str) -> str | None:
    db = SessionLocal()
    try:
        return db.execute(
            sa_text("SELECT source FROM activities WHERE id = :i"), {"i": activity_id}
        ).scalar()
    finally:
        db.close()


def _sources() -> list[str | None]:
    db = SessionLocal()
    try:
        return [
            r[0] for r in db.execute(
                sa_text("SELECT source FROM activities WHERE user_id = :u"), {"u": _USER}
            ).fetchall()
        ]
    finally:
        db.close()


def test_bulk_stamps_strava_api_source():
    ingest_activities_bulk(
        user_id=_USER,
        activities_data=[{
            "provider": "strava", "provider_activity_id": "stamp-1",
            "sport": "road", "geometry_geojson": _GEO,
            "distance_m": 12000, "activity_date": datetime(2025, 6, 1, 7, 0, tzinfo=UTC),
        }],
        contribute_heatmap=False,
        source=STRAVA_API_SOURCE,
    )
    assert _sources() == [STRAVA_API_SOURCE]


def test_bulk_per_row_source_overrides_call_default():
    ingest_activities_bulk(
        user_id=_USER,
        activities_data=[{
            "provider": "file", "provider_activity_id": None,
            "source": COMMUNITY_SOURCE,  # per-row wins over the call default
            "sport": "road", "geometry_geojson": _GEO,
            "distance_m": 13000, "activity_date": datetime(2025, 6, 2, 7, 0, tzinfo=UTC),
        }],
        contribute_heatmap=False,
        source=STRAVA_API_SOURCE,
    )
    assert _sources() == [COMMUNITY_SOURCE]


def test_bulk_default_none_leaves_source_null():
    ingest_activities_bulk(
        user_id=_USER,
        activities_data=[{
            "provider": "strava", "provider_activity_id": "stamp-none",
            "sport": "road", "geometry_geojson": _GEO,
            "distance_m": 14000, "activity_date": datetime(2025, 6, 3, 7, 0, tzinfo=UTC),
        }],
        contribute_heatmap=False,
        # no source= → NULL (unchanged legacy default)
    )
    assert _sources() == [None]


def test_single_ingest_stamps_source():
    # The resync path passes source="strava_api" in its activity_data dict.
    r = ingest_activity(_USER, {
        "provider": "strava", "provider_activity_id": "stamp-single",
        "source": STRAVA_API_SOURCE, "sport": "road",
        "geometry_geojson": _GEO, "distance_m": 15000,
        "activity_date": datetime(2025, 6, 4, 7, 0, tzinfo=UTC),
    }, skip_heat_computation=True)
    assert r["status"] == "created"
    assert _source_of(r["activity_id"]) == STRAVA_API_SOURCE
