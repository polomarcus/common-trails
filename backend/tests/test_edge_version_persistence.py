"""Regression tests for the persisted edge_version (migration 0040).

Background (2026-05-10): the routing-graph cache is keyed by
``edge_version``. Before 2026-05-10, that counter was a Python module-level
int incremented on every ingest. Fine on a single Cloud Run instance —
but with ``max_instances ≥ 2``, instance B's counter lags instance A's,
so instance B serves stale graph caches.

The fix moves the version into ``edge_version_state`` (single-row table)
with a per-instance TTL cache. These tests pin that:

1. ``_bump_edge_version`` returns a strictly increasing value
2. ``get_edge_version`` reads through the cache (TTL) and refreshes after expiry
3. ``_bump_edge_version`` refreshes the cache so the next read sees the new value
"""
from __future__ import annotations

import time

import pytest
from sqlalchemy import text as sa_text


@pytest.fixture
def db_session():
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _reset_cache_before_each_test():
    """Clear the per-instance cache so tests don't leak state to each other."""
    from app.services import ingest as ingest_module
    ingest_module._edge_version_cache = (0, 0.0)
    yield


def test_bump_increments_persisted_version(db_session) -> None:
    """Each call to _bump_edge_version returns a value strictly greater than
    the previous call. The persisted ``edge_version_state.version`` reflects
    the same value."""
    from app.services.ingest import _bump_edge_version

    v1 = _bump_edge_version(db_session)
    v2 = _bump_edge_version(db_session)
    v3 = _bump_edge_version(db_session)

    assert v2 == v1 + 1
    assert v3 == v2 + 1

    # Persisted value matches
    persisted = db_session.execute(
        sa_text("SELECT version FROM edge_version_state WHERE id = 1")
    ).scalar()
    assert persisted == v3


def test_bump_refreshes_cache_for_immediate_read(db_session) -> None:
    """After a bump, the next get_edge_version() call must NOT need to round-trip
    to the DB — the cache should already reflect the new value. This is what
    keeps a freshly-ingested heat_edge visible in routing without a TTL wait."""
    from app.services.ingest import _bump_edge_version, get_edge_version

    v_after_bump = _bump_edge_version(db_session)
    db_session.commit()
    v_read = get_edge_version()

    assert v_read == v_after_bump, (
        f"get_edge_version after bump returned {v_read}, expected {v_after_bump} — "
        "the cache wasn't refreshed by the bump"
    )


def test_get_uses_ttl_cache(db_session, monkeypatch) -> None:
    """get_edge_version() within EDGE_VERSION_TTL_S after the previous read
    returns the cached value, NOT the (potentially newer) DB value. This is
    the trade-off: at max_instances ≥ 2, instance B may show up to TTL_S
    seconds of stale routes after instance A ingests — by design.
    """
    from app.services import ingest as ingest_module

    # Seed cache with a known value
    ingest_module._edge_version_cache = (42, time.monotonic())

    # Bypass cache TTL by NOT advancing time. Direct DB write underneath:
    db_session.execute(sa_text(
        "UPDATE edge_version_state SET version = 9999 WHERE id = 1"
    ))
    db_session.commit()

    # Within TTL, the read returns the cached 42, not the DB's 9999
    assert ingest_module.get_edge_version() == 42

    # Force expiry by setting cache age beyond the TTL window
    ingest_module._edge_version_cache = (
        42, time.monotonic() - ingest_module._EDGE_VERSION_TTL_S - 1.0
    )
    # Now the read should refresh from DB
    assert ingest_module.get_edge_version() == 9999

    # Reset the row so other tests don't see 9999
    db_session.execute(sa_text(
        "UPDATE edge_version_state SET version = 0 WHERE id = 1"
    ))
    db_session.commit()
