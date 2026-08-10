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
_BBOX = (7.2, 4.2, 7.21, 4.21)
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


def _count_edges() -> int:
    db = SessionLocal()
    try:
        return db.execute(sa_text(
            "SELECT COUNT(*) FROM heat_edges "
            "WHERE ST_Intersects(geometry, ST_MakeEnvelope(:a,:b,:c,:d,4326))"
        ), {"a": _BBOX[0], "b": _BBOX[1], "c": _BBOX[2], "d": _BBOX[3]}).scalar() or 0
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _USER})
        db.execute(sa_text(
            "DELETE FROM heat_edge_contributors WHERE edge_key IN ("
            "  SELECT edge_key FROM heat_edges "
            "  WHERE ST_Intersects(geometry, ST_MakeEnvelope(:a,:b,:c,:d,4326)))"
        ), {"a": _BBOX[0], "b": _BBOX[1], "c": _BBOX[2], "d": _BBOX[3]})
        db.execute(sa_text(
            "DELETE FROM heat_edges "
            "WHERE ST_Intersects(geometry, ST_MakeEnvelope(:a,:b,:c,:d,4326))"
        ), {"a": _BBOX[0], "b": _BBOX[1], "c": _BBOX[2], "d": _BBOX[3]})
        db.commit()
    finally:
        db.close()


def _ingest_strava():
    return ingest_activity(_USER, {
        "provider": "strava", "provider_activity_id": "xsrc-strava-1",
        "source": STRAVA_API_SOURCE, "sport": "mtb",
        "geometry_geojson": _STRAVA_GEO, "distance_m": 5000,
        "activity_date": _DATE,
    }, contribute_heatmap=True)  # inline heat (skip defaults False)


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
    assert _count_edges() == 0, "strava_api ride must not feed community"

    r2 = _ingest_manual()
    assert r2["status"] == "promoted", f"expected promotion, got {r2}"
    assert r2["activity_id"] == r1["activity_id"], "promotion reuses the existing row"

    # Exactly one activity row (no double personal display).
    assert _count_activities() == 1

    source, geom = _row(r1["activity_id"])
    assert source == COMMUNITY_SOURCE, "row promoted to community provenance"
    # Compliance: the stored geometry is the UPLOADED GPX, not the Strava polyline.
    assert geom == _MANUAL_GEO

    # The ride now appears in the community, counted once.
    assert _count_edges() > 0
    assert r2["edges_indexed"] > 0


def test_manual_then_strava_skips_without_double_count():
    r1 = _ingest_manual()
    assert r1["status"] == "created"
    edges_after_manual = _count_edges()
    assert edges_after_manual > 0

    r2 = _ingest_strava()
    # A strava sync landing over an existing community row is a plain dedup
    # skip — no second row, no touching the community copy.
    assert r2["status"] == "already_exists"
    assert r2["activity_id"] == r1["activity_id"]
    assert _count_activities() == 1
    assert _count_edges() == edges_after_manual, "community count must not change"

    # The kept row stays community + keeps the uploaded geometry.
    source, geom = _row(r1["activity_id"])
    assert source == COMMUNITY_SOURCE
    assert geom == _MANUAL_GEO


def test_async_promotion_adopts_manual_contribute_intent():
    """ASYNC path (skip_heat_computation=True → enqueue): promotion must adopt
    the INCOMING upload's contribute_heatmap intent, not the stale twin's.

    Regression: the pre-existing strava_api twin is opted OUT
    (contribute_heatmap=False) while the manual upload intends IN (True). On
    the async path the enqueued ``process_heat_compute`` re-reads the row's
    ``contribute_heatmap`` from the DB; if promotion left it False the promoted
    ride would silently never reach the community. FAILS on the pre-fix code
    (``_promote_activity_to_community`` didn't set contribute_heatmap → noop),
    PASSES after.
    """
    from app.api.internal_ingest import process_heat_compute

    # 1. Pre-existing Strava-API twin, opted OUT, stored WITHOUT heat (async).
    r1 = ingest_activity(_USER, {
        "provider": "strava", "provider_activity_id": "xsrc-async-strava",
        "source": STRAVA_API_SOURCE, "sport": "mtb",
        "geometry_geojson": _STRAVA_GEO, "distance_m": 5000,
        "activity_date": _DATE,
    }, contribute_heatmap=False, skip_heat_computation=True)
    assert r1["status"] == "created"
    assert _contribute_of(r1["activity_id"]) is False
    assert _count_edges() == 0

    # 2. Manual upload of the SAME ride, opted IN, via the ASYNC path
    #    (skip_heat_computation=True → nothing computed inline, just promoted).
    r2 = ingest_activity(_USER, {
        "provider": "file", "provider_activity_id": None,
        "source": COMMUNITY_SOURCE, "sport": "mtb",
        "geometry_geojson": _MANUAL_GEO, "distance_m": 5030,
        "file_hash": "xsrc-async-manual",
        "activity_date": _DATE + timedelta(seconds=40),
    }, contribute_heatmap=True, skip_heat_computation=True)
    assert r2["status"] == "promoted"
    assert r2["activity_id"] == r1["activity_id"]
    assert r2["edges_indexed"] == 0, "async path defers heat to the enqueued task"
    assert _count_edges() == 0, "no inline heat on the async path yet"
    # The promoted row adopts the manual upload's opt-IN intent.
    assert _contribute_of(r1["activity_id"]) is True

    # 3. The enqueued task runs — re-reading contribute_heatmap from the DB.
    res = process_heat_compute(r1["activity_id"], _USER)
    assert res["status"] == "ok", f"promoted ride must reach the community; got {res}"
    assert res["edges_indexed"] > 0
    assert _count_edges() > 0


def test_promotion_then_reupload_is_idempotent():
    _ingest_strava()
    r2 = _ingest_manual(file_hash="xsrc-manual-dup")
    assert r2["status"] == "promoted"
    edges_after_promotion = _count_edges()

    # Re-uploading the same manual file → file-hash dedup, no new row, no
    # inflation of the community count.
    r3 = _ingest_manual(file_hash="xsrc-manual-dup")
    assert r3["status"] == "already_exists"
    assert _count_activities() == 1
    assert _count_edges() == edges_after_promotion
