"""Same-user re-ingest must NOT inflate `user_count`.

The dedup contract in [project_dedup_bug.md] depends on the
`COUNT(*) FROM heat_edge_contributors GROUP BY edge_key` recompute at
`ingest.py:_update_heat_edges` — which (given the new triple PK
`(edge_key, user_id_hash, activity_id)` from migration 0052) is
equivalent to `COUNT(DISTINCT user_id_hash)` per edge for any single
user. So a single user uploading the same GPX twice (or re-tracing the
same physical edge in a second activity) does NOT count as 2 distinct
contributors for K-anonymity purposes.

This pins the contract: if a future refactor breaks the PK or the
`user_count` UPDATE at `ingest.py:1950`, K-anonymity
(`user_count >= 2`) would start exposing edges contributed by a single
user — a privacy regression.

Uses Iceland coords inside a dedicated bbox (zero prod data) and
short edges (≤ 5m) so densification doesn't multiply edge count.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import _update_heat_edges

# Distinct bbox from the other test files (in `test_ingest_direction.py`).
_SPORT = "gravel"
_BBOX = (-23.5, 65.6, -23.0, 65.8)  # far-north Iceland, off-grid
# 0.0001° lon at lat 65.7 ≈ 4.6m → 1 edge after densification.
_TRACE = [[-23.10000, 65.70000], [-23.09990, 65.70000]]


def _geojson(coords: list) -> str:
    return json.dumps({"type": "LineString", "coordinates": coords})


def _wipe_bbox() -> None:
    """Delete heat_edges + contributors that intersect _BBOX."""
    min_lon, min_lat, max_lon, max_lat = _BBOX
    db = SessionLocal()
    try:
        db.execute(sa_text(
            """
            DELETE FROM heat_edge_contributors WHERE edge_key IN (
                SELECT edge_key FROM heat_edges
                WHERE ST_Intersects(
                    geometry,
                    ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
                )
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat})
        db.execute(sa_text(
            """
            DELETE FROM heat_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat})
        db.commit()
    finally:
        db.close()


def _first_edge_in_bbox() -> dict | None:
    min_lon, min_lat, max_lon, max_lat = _BBOX
    db = SessionLocal()
    try:
        row = db.execute(sa_text("""
            SELECT edge_key, pass_count, user_count
            FROM heat_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            LIMIT 1
        """), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).fetchone()
        if not row:
            return None
        return {"edge_key": row[0], "pass_count": row[1], "user_count": row[2]}
    finally:
        db.close()


def _contributors_for(edge_key: str) -> int:
    db = SessionLocal()
    try:
        row = db.execute(sa_text(
            "SELECT COUNT(*) FROM heat_edge_contributors WHERE edge_key = :k"
        ), {"k": edge_key}).fetchone()
        return row[0] if row else 0
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clear_bbox():
    _wipe_bbox()
    yield
    _wipe_bbox()


def test_same_user_re_ingest_same_activity_is_noop():
    """Two ingests of the same trace by the SAME user with the SAME
    activity_id → user_count stays at 1 AND pass_count stays at 1.

    This is the migration-0052 fix: rebuild_heatmap and other paths
    that re-ingest existing activities must NOT inflate pass_count.
    Before the fix, every full rebuild → every contributor edge →
    pass_count += 1, compounding indefinitely across rebuild cycles
    (Paul's local DB had edges with pass_count=222 / user_count=1 from
    222 rebuild compounding cycles).
    """
    activity_id = "00000000-0000-0000-0000-0000000000a1"
    _update_heat_edges("user-A", _SPORT, _geojson(_TRACE), activity_id=activity_id)
    _update_heat_edges("user-A", _SPORT, _geojson(_TRACE), activity_id=activity_id)

    edge = _first_edge_in_bbox()
    assert edge is not None, "Expected one heat_edge after two ingests of same trace"
    assert edge["user_count"] == 1, (
        f"Same user re-ingest must not increment user_count; got {edge['user_count']}. "
        f"This breaks K-anonymity — a single user can satisfy K=2 on their own."
    )
    # Critical regression catch: same activity twice → pass_count unchanged.
    assert edge["pass_count"] == 1, (
        f"Same activity re-ingested → pass_count must stay at 1; got {edge['pass_count']}. "
        f"This is the migration-0052 fix — rebuild_heatmap was compounding "
        f"pass_count on every cycle."
    )


def test_same_user_different_activity_bumps_pass_count():
    """Same user, same trace, DIFFERENT activity_ids → user_count
    stays at 1 (still only one distinct user), but pass_count grows
    because each activity is a real new contribution."""
    _update_heat_edges("user-A", _SPORT, _geojson(_TRACE),
                       activity_id="00000000-0000-0000-0000-0000000000a1")
    _update_heat_edges("user-A", _SPORT, _geojson(_TRACE),
                       activity_id="00000000-0000-0000-0000-0000000000a2")
    _update_heat_edges("user-A", _SPORT, _geojson(_TRACE),
                       activity_id="00000000-0000-0000-0000-0000000000a3")

    edge = _first_edge_in_bbox()
    assert edge is not None
    assert edge["user_count"] == 1, (
        f"Three activities by the SAME user → user_count must stay at 1; "
        f"got {edge['user_count']}. K-anonymity privacy bug."
    )
    assert edge["pass_count"] == 3, (
        f"Three distinct activities → pass_count = 3; got {edge['pass_count']}. "
        f"Different activities by the same user are legitimate new passes."
    )
    # 3 contributor rows — one per (user, activity) pair, all sharing user_id_hash.
    assert _contributors_for(edge["edge_key"]) == 3


def test_two_distinct_users_increment_user_count():
    """Sanity check on the opposite case — two DIFFERENT users on the
    same edge → user_count = 2. Without this, the previous tests
    could be vacuously true on a broken UPDATE."""
    _update_heat_edges("user-A", _SPORT, _geojson(_TRACE),
                       activity_id="00000000-0000-0000-0000-0000000000a1")
    _update_heat_edges("user-B", _SPORT, _geojson(_TRACE),
                       activity_id="00000000-0000-0000-0000-0000000000b1")

    edge = _first_edge_in_bbox()
    assert edge is not None
    assert edge["user_count"] == 2, (
        f"Two distinct users must yield user_count=2; got {edge['user_count']}. "
        f"The `COUNT(*) FROM heat_edge_contributors` update in ingest.py:1950 "
        f"may have regressed (the triple PK on contributors plus a single "
        f"activity per user makes this equivalent to COUNT(DISTINCT user_id_hash))."
    )
    assert edge["pass_count"] == 2, (
        f"Two users each contributing one activity → pass_count = 2; "
        f"got {edge['pass_count']}."
    )
    assert _contributors_for(edge["edge_key"]) == 2


def test_rebuild_idempotence():
    """The critical regression catch: simulate `rebuild_heatmap_parallel`
    running 3 times over the same activities (the prod bug pattern).
    pass_count must stay STABLE across rebuilds, not inflate.

    This is the test that would have caught the prod bug Paul
    screenshotted on 2026-05-31 (single edge: 2 contributors, 735 pass
    MTB / 242 GRAVEL / 121 ROAD / 105 RUN — none of those numbers are
    real ride counts; they're rebuild-compounding artefacts).
    """
    aid_a = "00000000-0000-0000-0000-00000000aaa1"
    aid_b = "00000000-0000-0000-0000-00000000bbb1"
    aid_c = "00000000-0000-0000-0000-00000000ccc1"

    # Initial ingest: 3 distinct activities (by 2 users) → pass_count=3.
    _update_heat_edges("user-A", _SPORT, _geojson(_TRACE), activity_id=aid_a)
    _update_heat_edges("user-B", _SPORT, _geojson(_TRACE), activity_id=aid_b)
    _update_heat_edges("user-A", _SPORT, _geojson(_TRACE), activity_id=aid_c)

    edge_after_initial = _first_edge_in_bbox()
    assert edge_after_initial is not None
    initial_pass = edge_after_initial["pass_count"]
    initial_user = edge_after_initial["user_count"]
    assert initial_pass == 3
    assert initial_user == 2

    # Simulate 3 rebuild cycles (same activities replayed).
    for _ in range(3):
        _update_heat_edges("user-A", _SPORT, _geojson(_TRACE), activity_id=aid_a)
        _update_heat_edges("user-B", _SPORT, _geojson(_TRACE), activity_id=aid_b)
        _update_heat_edges("user-A", _SPORT, _geojson(_TRACE), activity_id=aid_c)

    edge_after_rebuilds = _first_edge_in_bbox()
    assert edge_after_rebuilds is not None
    assert edge_after_rebuilds["pass_count"] == initial_pass, (
        f"3 rebuild cycles must NOT inflate pass_count. "
        f"Before: {initial_pass}, after: {edge_after_rebuilds['pass_count']}. "
        f"This is the migration-0052 regression catch — pre-fix this would "
        f"have been 12 (3 + 9 phantom passes from the 3 rebuilds)."
    )
    assert edge_after_rebuilds["user_count"] == initial_user
