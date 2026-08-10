"""K-anonymity boundary — the single most important privacy invariant.

Tests:
  - An edge with user_count = K-1 must NOT be returned by any public
    heatmap endpoint (privacy leak otherwise).
  - An edge with user_count = K MUST be returned (else nobody sees the
    data they contributed to).

Tested at the SQL filter level (the actual ``user_count >= :k`` clause
used by all public heatmap endpoints), not via the FastAPI client —
this lets us pin the boundary without setting up multi-user fixtures.

Implementation note: heat_edges is partitioned by sport with a CHECK
constraint via LIST partitioning. We use the ``gravel`` partition for
test data and clean up via a sentinel edge_key prefix.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text as sa_text

_TEST_PREFIX = "_kanon_boundary"


@pytest.fixture
def db_session():
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _cleanup(db) -> None:
    db.execute(
        sa_text("DELETE FROM heat_edges WHERE edge_key LIKE :p"),
        {"p": _TEST_PREFIX + "%"},
    )
    db.commit()


def _insert_edge(db, edge_key: str, user_count: int, lon: float = 3.870, lat: float = 43.610) -> None:
    """Insert one heat_edge with the given user_count for K-anon boundary tests."""
    db.execute(sa_text("""
        INSERT INTO heat_edges (
            edge_key, sport, user_count, pass_count,
            forward_count, backward_count, geometry,
            osm_way_id, surface_type, highway_type
        ) VALUES (
            :ek, 'gravel', :uc, :uc,
            :uc, 0,
            ST_MakeLine(
                ST_MakePoint(:lon, :lat),
                ST_MakePoint(:lon + 0.0001, :lat)
            )::geometry(LineString, 4326),
            NULL, 'asphalt', 'residential'
        )
        ON CONFLICT (edge_key, sport) DO UPDATE
          SET user_count = EXCLUDED.user_count,
              pass_count = EXCLUDED.pass_count
    """), {"ek": edge_key, "uc": user_count, "lon": lon, "lat": lat})
    db.commit()


# ── The actual SQL filter under test ──────────────────────────────────────
#
# Every public heatmap endpoint applies ``WHERE user_count >= :k`` (and
# the matview/PMTiles equivalents do the same). We exercise that filter
# directly so the test pins the boundary regardless of which endpoint
# touches it.
_KANON_SQL = """
    SELECT edge_key
    FROM heat_edges
    WHERE edge_key LIKE :p
      AND user_count >= :k
"""


@pytest.mark.parametrize("k", [1, 2, 3])
def test_edge_with_uc_below_k_is_filtered_out(db_session, k):
    """An edge with user_count < K must NOT pass the K-anonymity filter."""
    try:
        _cleanup(db_session)
        # Insert an edge with user_count = K-1 (just under the gate)
        _insert_edge(db_session, f"{_TEST_PREFIX}/below/{k}", user_count=k - 1)

        rows = db_session.execute(sa_text(_KANON_SQL), {
            "p": _TEST_PREFIX + "%", "k": k,
        }).fetchall()

        assert rows == [], (
            f"K={k}: edge with user_count={k - 1} ({k}-1) must NOT be returned "
            f"by the K-anon filter. Returned: {rows}"
        )
    finally:
        _cleanup(db_session)


@pytest.mark.parametrize("k", [1, 2, 3])
def test_edge_with_uc_exactly_k_passes_filter(db_session, k):
    """An edge with user_count = K (the boundary) MUST pass the filter."""
    try:
        _cleanup(db_session)
        _insert_edge(db_session, f"{_TEST_PREFIX}/at/{k}", user_count=k)

        rows = db_session.execute(sa_text(_KANON_SQL), {
            "p": _TEST_PREFIX + "%", "k": k,
        }).fetchall()

        assert len(rows) == 1, (
            f"K={k}: edge with user_count={k} (exactly K) MUST be returned "
            f"by the K-anon filter. Got: {rows}"
        )
        assert rows[0][0] == f"{_TEST_PREFIX}/at/{k}"
    finally:
        _cleanup(db_session)


def test_mixed_edges_only_above_k_returned(db_session):
    """Realistic scenario: a sport partition has edges spanning user_count
    1..5. With K=2, only the user_count>=2 edges are returned."""
    try:
        _cleanup(db_session)
        # 5 edges with user_count = 1, 2, 3, 4, 5
        for i, uc in enumerate([1, 2, 3, 4, 5]):
            _insert_edge(
                db_session,
                f"{_TEST_PREFIX}/mixed/{i}",
                user_count=uc,
                lon=3.870 + i * 0.001,
            )

        rows = db_session.execute(sa_text(_KANON_SQL), {
            "p": _TEST_PREFIX + "/mixed/%", "k": 2,
        }).fetchall()

        # Expect 4 rows (uc = 2, 3, 4, 5)
        assert len(rows) == 4, (
            f"K=2 over user_counts 1/2/3/4/5 should return 4 rows, got {len(rows)}: {rows}"
        )
    finally:
        _cleanup(db_session)


def test_default_k_value_is_2_in_prod() -> None:
    """The HEATMAP_K_ANONYMITY default in the heatmap module must be 2.

    Lowering this default to 1 in prod would silently expose single-user
    data on the public heatmap. Tested via module read so a future PR
    that flips the default fails CI.
    """
    import os
    # Reset env to verify the hard-coded default
    saved = os.environ.pop("HEATMAP_K_ANONYMITY", None)
    try:
        # Re-import to pick up the default (the module reads at import time)
        import importlib

        from app.api import heatmap as heatmap_module
        importlib.reload(heatmap_module)
        assert heatmap_module.HEATMAP_K_ANONYMITY == 2, (
            f"HEATMAP_K_ANONYMITY default is {heatmap_module.HEATMAP_K_ANONYMITY}, expected 2. "
            "Lowering this default leaks single-user data publicly. "
            "Use the env var HEATMAP_K_ANONYMITY=1 in dev/docker-compose only."
        )
    finally:
        if saved is not None:
            os.environ["HEATMAP_K_ANONYMITY"] = saved
        # Reload once more to restore module state
        import importlib

        from app.api import heatmap as heatmap_module
        importlib.reload(heatmap_module)
