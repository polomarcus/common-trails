"""Cross-source idempotence (③): a ride arriving both via the Strava API and
via a manual upload must be counted ONCE in the community — fed by the
manual_upload copy's geometry, never double-counted.

The two copies share NO ``provider_activity_id`` (raw GPX has none), so only
the (user, start-time ±5min, distance ±5%) heuristic links them. When the
community-eligible copy lands over a non-community twin we PROMOTE the existing
row (source + authoritative uploaded geometry) instead of dropping the upload.

Drives the REAL ``ingest_activity``. FAILS on the pre-change code (the manual
upload was silently dropped as ``already_exists`` → the ride never reached the
community layer) and PASSES after.
"""
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import ingest_activity
from app.services.provenance import COMMUNITY_SOURCE, STRAVA_API_SOURCE

_USER = "xsrc-promo-user"
_LON0, _LAT0 = 7.2005, 4.2005
_DATE = datetime(2025, 5, 1, 7, 0, tzinfo=UTC)


def _geo(n: int = 5, step: float = 0.0002, jitter: float = 0.0) -> str:
    coords = [
        [round(_LON0 + i * step + jitter, 6), round(_LAT0 + i * step + jitter, 6)]
        for i in range(n)
    ]
    return json.dumps({"type": "LineString", "coordinates": coords})


# Distinct geometries so we can prove the STORED geometry after promotion is
# the uploaded (manual) one, not the Strava polyline (compliance).
_STRAVA_GEO = _geo(n=3, step=0.0004)      # coarse "polyline"
_MANUAL_GEO = _geo(n=6, step=0.0002)      # fine uploaded GPX


def _count_activities() -> int:
    db = SessionLocal()
    try:
        return db.execute(
            sa_text("SELECT COUNT(*) FROM activities WHERE user_id = :u"), {"u": _USER}
        ).scalar() or 0
    finally:
        db.close()


def _row(activity_id: str):
    db = SessionLocal()
    try:
        return db.execute(
            sa_text("SELECT source, geometry_geojson FROM activities WHERE id = :i"),
            {"i": activity_id},
        ).fetchone()
    finally:
        db.close()


def _contribute_of(activity_id: str) -> bool:
    db = SessionLocal()
    try:
        return db.execute(
            sa_text("SELECT contribute_heatmap FROM activities WHERE id = :i"),
            {"i": activity_id},
        ).scalar()
    finally:
        db.close()


def _community_rows() -> int:
    """Rows of the test user the community build would read — the SSOT gate
    (``provenance.community_eligible_conditions``) the raw export filters on."""
    from app.services.provenance import community_eligible_conditions

    conds, params = community_eligible_conditions()
    db = SessionLocal()
    try:
        return db.execute(
            sa_text("SELECT COUNT(*) FROM activities WHERE user_id = :u AND " + " AND ".join(conds)),
            {**params, "u": _USER},
        ).scalar() or 0
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _USER})
        db.commit()
    finally:
        db.close()


def _ingest_strava():
    return ingest_activity(_USER, {
        "provider": "strava", "provider_activity_id": "xsrc-strava-1",
        "source": STRAVA_API_SOURCE, "sport": "mtb",
        "geometry_geojson": _STRAVA_GEO, "distance_m": 5000,
        "activity_date": _DATE,
    }, contribute_heatmap=True)


def _ingest_manual(file_hash: str = "xsrc-manual-1"):
    return ingest_activity(_USER, {
        "provider": "file", "provider_activity_id": None,
        "source": COMMUNITY_SOURCE, "sport": "mtb",
        "geometry_geojson": _MANUAL_GEO, "distance_m": 5030,  # +0.6% — within ±5%
        "file_hash": file_hash,
        "activity_date": _DATE + timedelta(seconds=40),  # within ±5min
    }, contribute_heatmap=True)


def test_strava_then_manual_promotes_single_row_and_counts_once():
    r1 = _ingest_strava()
    assert r1["status"] == "created"
    assert _community_rows() == 0, "strava_api ride must not feed community"

    r2 = _ingest_manual()
    assert r2["status"] == "promoted", f"expected promotion, got {r2}"
    assert r2["activity_id"] == r1["activity_id"], "promotion reuses the existing row"

    # Exactly one activity row (no double personal display).
    assert _count_activities() == 1

    source, geom = _row(r1["activity_id"])
    assert source == COMMUNITY_SOURCE, "row promoted to community provenance"
    # Compliance: the stored geometry is the UPLOADED GPX, not the Strava polyline.
    assert geom == _MANUAL_GEO

    # The ride is now community-eligible, counted once.
    assert _community_rows() == 1


def test_manual_then_strava_skips_without_double_count():
    r1 = _ingest_manual()
    assert r1["status"] == "created"
    assert _community_rows() == 1

    r2 = _ingest_strava()
    # A strava sync landing over an existing community row is a plain dedup
    # skip — no second row, no touching the community copy.
    assert r2["status"] == "already_exists"
    assert r2["activity_id"] == r1["activity_id"]
    assert _count_activities() == 1
    assert _community_rows() == 1, "community count must not change"

    # The kept row stays community + keeps the uploaded geometry.
    source, geom = _row(r1["activity_id"])
    assert source == COMMUNITY_SOURCE
    assert geom == _MANUAL_GEO


def test_async_promotion_adopts_manual_contribute_intent():
    """Promotion must adopt the INCOMING upload's contribute_heatmap intent,
    not the stale twin's.

    Regression: the pre-existing strava_api twin is opted OUT
    (contribute_heatmap=False) while the manual upload intends IN (True). The
    community build reads the row's ``contribute_heatmap`` from the DB; if
    promotion left it False the promoted ride would silently never reach the
    community.
    """
    # 1. Pre-existing Strava-API twin, opted OUT.
    r1 = ingest_activity(_USER, {
        "provider": "strava", "provider_activity_id": "xsrc-async-strava",
        "source": STRAVA_API_SOURCE, "sport": "mtb",
        "geometry_geojson": _STRAVA_GEO, "distance_m": 5000,
        "activity_date": _DATE,
    }, contribute_heatmap=False)
    assert r1["status"] == "created"
    assert _contribute_of(r1["activity_id"]) is False

    # 2. Manual upload of the SAME ride, opted IN.
    r2 = ingest_activity(_USER, {
        "provider": "file", "provider_activity_id": None,
        "source": COMMUNITY_SOURCE, "sport": "mtb",
        "geometry_geojson": _MANUAL_GEO, "distance_m": 5030,
        "file_hash": "xsrc-async-manual",
        "activity_date": _DATE + timedelta(seconds=40),
    }, contribute_heatmap=True)
    assert r2["status"] == "promoted"
    assert r2["activity_id"] == r1["activity_id"]
    # The promoted row adopts the manual upload's opt-IN intent.
    assert _contribute_of(r1["activity_id"]) is True
    assert _community_rows() == 1


def test_promotion_then_reupload_is_idempotent():
    _ingest_strava()
    r2 = _ingest_manual(file_hash="xsrc-manual-dup")
    assert r2["status"] == "promoted"
    assert _community_rows() == 1

    # Re-uploading the same manual file → file-hash dedup, no new row, no
    # inflation of the community count.
    r3 = _ingest_manual(file_hash="xsrc-manual-dup")
    assert r3["status"] == "already_exists"
    assert _count_activities() == 1
    assert _community_rows() == 1
