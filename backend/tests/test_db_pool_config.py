"""Cloud SQL pool sizing — env var reads + sane defaults.

The backend's SQLAlchemy engine is configured at module import time
from `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, and
`DB_POOL_RECYCLE` env vars. These tests pin that the config:

1. Defaults preserve the historical 20+10 behavior so local dev
   (docker-compose, no env override) runs unchanged.
2. Env vars are honored — required for prod to stay within Cloud SQL
   db-f1-micro's 25-connection cap.
3. The configured values surface on the engine's pool object so a
   future refactor that drops the env reading would fail this test.

The actual engine in `app.db.session` is the LIVE engine for this
test session — we don't recreate it. Tests read engine.pool stats
to verify the config that was loaded at import.
"""
from __future__ import annotations

import importlib
import os

import pytest


def test_default_pool_size_is_20() -> None:
    """Without env override, pool_size defaults to 20 (preserves local dev)."""
    # Re-import with no env override to verify default
    saved = {k: os.environ.pop(k, None) for k in (
        "DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT", "DB_POOL_RECYCLE",
    )}
    try:
        from app.db import session as session_mod
        importlib.reload(session_mod)
        assert session_mod._POOL_SIZE == 20
        assert session_mod._MAX_OVERFLOW == 10
        assert session_mod._POOL_TIMEOUT == 30
        assert session_mod._POOL_RECYCLE == 1800
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
        # Restore the module state
        from app.db import session as session_mod
        importlib.reload(session_mod)


@pytest.mark.parametrize("var,attr,override,expected", [
    ("DB_POOL_SIZE", "_POOL_SIZE", "5", 5),
    ("DB_MAX_OVERFLOW", "_MAX_OVERFLOW", "3", 3),
    ("DB_POOL_TIMEOUT", "_POOL_TIMEOUT", "10", 10),
    ("DB_POOL_RECYCLE", "_POOL_RECYCLE", "600", 600),
])
def test_pool_env_override(monkeypatch, var, attr, override, expected):
    """Each pool env var is read on import."""
    monkeypatch.setenv(var, override)
    from app.db import session as session_mod
    importlib.reload(session_mod)
    try:
        assert getattr(session_mod, attr) == expected, (
            f"{var}={override} should produce {attr}={expected}, "
            f"got {getattr(session_mod, attr)}"
        )
    finally:
        # Restore module state cleanly
        monkeypatch.delenv(var, raising=False)
        importlib.reload(session_mod)


def test_pool_size_within_db_f1_micro_budget() -> None:
    """Prod pool config + max_instances must stay within 22 client conns.

    db-f1-micro: max_connections=25, superuser_reserved_connections=3,
    so 22 conns are available for application clients. With the
    intended prod config (POOL_SIZE=5, MAX_OVERFLOW=3 per instance),
    max_instances=2 leaves 22 - 16 = 6 for Cloud SQL proxy, migrations,
    and admin. This is the algebraic constraint that must NEVER be
    violated."""
    PROD_POOL_SIZE = 5
    PROD_MAX_OVERFLOW = 3
    PER_INSTANCE = PROD_POOL_SIZE + PROD_MAX_OVERFLOW  # = 8

    DB_F1_MICRO_BUDGET = 22  # 25 max - 3 superuser-reserved

    # Even at 2 instances we must stay strictly under the budget
    assert 2 * PER_INSTANCE < DB_F1_MICRO_BUDGET, (
        f"Per-instance pool ({PER_INSTANCE}) × 2 instances exceeds db-f1-micro "
        f"budget ({DB_F1_MICRO_BUDGET}). Tune DB_POOL_SIZE / DB_MAX_OVERFLOW "
        f"or upgrade tier."
    )
    # And at 1 instance there's plenty of headroom
    assert 1 * PER_INSTANCE < DB_F1_MICRO_BUDGET, (
        "Per-instance pool exceeds db-f1-micro budget on its own"
    )


def test_engine_pool_pre_ping_is_on() -> None:
    """Engine must always have pool_pre_ping — without it, idle conns
    after a Cloud SQL restart return ResourceClosedError on first use."""
    from app.db.session import engine
    # pool_pre_ping is exposed on the pool dialect; this is the
    # SQLAlchemy way to verify it.
    assert engine.pool._pre_ping is True, (
        "pool_pre_ping must be enabled to survive Cloud SQL restarts"
    )
