"""``ingest_activity`` stamps the provenance ``source`` it is given (②)."""
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import ingest_activity
from app.services.provenance import STRAVA_API_SOURCE

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


def test_no_source_leaves_it_null():
    r = ingest_activity(_USER, {
        "provider": "strava", "provider_activity_id": "stamp-none",
        "sport": "road", "geometry_geojson": _GEO,
        "distance_m": 14000, "activity_date": datetime(2025, 6, 3, 7, 0, tzinfo=UTC),
    }, contribute_heatmap=False)
    assert r["status"] == "created"
    assert _source_of(r["activity_id"]) is None


def test_single_ingest_stamps_source():
    r = ingest_activity(_USER, {
        "provider": "strava", "provider_activity_id": "stamp-single",
        "source": STRAVA_API_SOURCE, "sport": "road",
        "geometry_geojson": _GEO, "distance_m": 15000,
        "activity_date": datetime(2025, 6, 4, 7, 0, tzinfo=UTC),
    })
    assert r["status"] == "created"
    assert _source_of(r["activity_id"]) == STRAVA_API_SOURCE
