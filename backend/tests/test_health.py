"""Tests for health endpoints."""
import pytest
from fastapi import HTTPException


def test_healthz_returns_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_readyz_ok_in_raw_mode(client, monkeypatch):
    """Ready = the DB answers and the startup load is finished. Raw display
    (the default, like prod) never writes the legacy ``heat_edges`` table; the
    probe used to require rows there and reported ``warming`` forever."""
    from app import main

    monkeypatch.setitem(main.startup_progress, "done", True)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    # The CD post-deploy healthcheck greps the body for this key.
    assert data["version"]
    # No heatmap-table probe left in the response.
    assert set(data) == {"status", "version"}


def test_readyz_warming_while_startup_load_runs(client, monkeypatch):
    """Still HTTP 200 (the wake-up banner only looks at ``res.ok``) but the
    status says the background data load has not finished."""
    from app import main

    monkeypatch.setitem(main.startup_progress, "done", False)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "warming"
    assert resp.json()["version"]


def test_readyz_never_touches_heatmap_tables(monkeypatch):
    """Drives the real handler with a fake session that raises on anything but
    the connectivity probe: readiness must not depend on heat_edges*."""
    from app import main
    from app.api.health import readyz

    monkeypatch.setitem(main.startup_progress, "done", True)
    seen: list[str] = []

    class _DB:
        def execute(self, stmt, params=None):
            sql = str(getattr(stmt, "text", stmt))
            seen.append(sql)
            if "heat_edges" in sql or "information_schema" in sql:
                raise AssertionError(f"readyz must not query: {sql}")

    resp = readyz(db=_DB())
    assert resp.status == "ok"
    assert any("SELECT 1" in s for s in seen)


def test_readyz_503_when_db_down():
    from app.api.health import readyz

    class _DeadDB:
        def execute(self, *a, **k):
            raise Exception("connection refused")

    with pytest.raises(HTTPException) as exc:
        readyz(db=_DeadDB())
    assert exc.value.status_code == 503
