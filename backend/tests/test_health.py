"""Tests for health endpoints."""


def test_healthz_returns_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_readyz_returns_200(client):
    """Ready = the DB answers. Raw display (the default, like prod) never writes
    the legacy ``heat_edges`` table, so the probe reports ``warming`` with
    ``heat_edges == 0`` there; ``ok`` only appears when legacy rows exist."""
    resp = client.get("/readyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ok", "warming")
    assert "version" in data
    assert data["heat_edges"] >= 0
