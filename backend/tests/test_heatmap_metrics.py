"""Real execute-and-assert tests for the admin heatmap MONITORING feature.

Covers the three pieces landed in ``feat/admin-heatmap-monitoring``:

1. ``build_pmtiles.capture_heatmap_metrics_snapshot`` — the append-only
   snapshot writer (reuses ``compute_community_stats``, SSOT). Asserts it
   writes the documented columns with the right types.
2. ``GET /admin/heatmap-metrics`` — admin-gated; returns the Level-2
   time-series + the Level-1 freshness object. Non-admin → 403.
3. ``admin._heatmap_freshness`` — the Level-1 computation, driven directly
   so a logic regression (reconnect threshold, webhook env) fails the test.

Drives the REAL functions/handlers (no inline mirror). Non-destructive on the
shared local DB: synthetic ``source`` tags + integration rows, cleaned up in
``finally``. Needs the PostGIS DB with migration 0058 (``heatmap_metrics``);
skips cleanly if absent.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal


def _db_available() -> bool:
    try:
        db = SessionLocal()
        try:
            db.execute(sa_text("SELECT 1 FROM heatmap_metrics LIMIT 1"))
            db.execute(sa_text("SELECT 1 FROM heat_edges_agg LIMIT 1"))
            return True
        finally:
            db.close()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="needs the PostGIS DB with migration 0058 (heatmap_metrics)",
)


def _make_admin(client) -> dict[str, str]:
    """Register a fresh user and flip is_admin=True directly in the DB."""
    email = f"admin_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/auth/register", json={
        "email": email, "password": "testpass123", "username": f"adm_{uuid.uuid4().hex[:6]}",
    })
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    db = SessionLocal()
    try:
        db.execute(sa_text("UPDATE users SET is_admin = true WHERE email = :e"), {"e": email})
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}


class TestCaptureSnapshot:
    def test_snapshot_writes_documented_columns(self):
        """The REAL snapshot writer inserts one row with the right columns."""
        from app.jobs.build_pmtiles import capture_heatmap_metrics_snapshot

        source = f"test-{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        try:
            row = capture_heatmap_metrics_snapshot(db, source=source, min_uc=1)
            assert row is not None
            # Returned dict carries the numbers it inserted.
            assert row["source"] == source
            for k in ("heat_edges", "agg_ways", "activities", "contributors", "network_km"):
                assert isinstance(row[k], (int, float)), k
            assert row["grid_fallback_pct"] is None  # rebuild/daily leave it NULL

            # And it is actually persisted with those columns.
            persisted = db.execute(sa_text(
                "SELECT heat_edges, agg_ways, activities, contributors, "
                "network_km, grid_fallback_pct, source, captured_at "
                "FROM heatmap_metrics WHERE source = :s"
            ), {"s": source}).first()
            assert persisted is not None
            assert persisted[6] == source
            assert persisted[7] is not None  # captured_at defaulted to now()
            # agg_ways matches a live count of the small agg table.
            live_ways = db.execute(sa_text("SELECT COUNT(*) FROM heat_edges_agg")).scalar()
            assert persisted[1] == live_ways
        finally:
            db.execute(sa_text("DELETE FROM heatmap_metrics WHERE source = :s"), {"s": source})
            db.commit()
            db.close()

    def test_snapshot_can_carry_grid_fallback_pct(self):
        from app.jobs.build_pmtiles import capture_heatmap_metrics_snapshot

        source = f"test-{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        try:
            row = capture_heatmap_metrics_snapshot(
                db, source=source, min_uc=1, grid_fallback_pct=3.5,
            )
            assert row is not None and row["grid_fallback_pct"] == 3.5
            persisted = db.execute(sa_text(
                "SELECT grid_fallback_pct FROM heatmap_metrics WHERE source = :s"
            ), {"s": source}).scalar()
            assert float(persisted) == 3.5
        finally:
            db.execute(sa_text("DELETE FROM heatmap_metrics WHERE source = :s"), {"s": source})
            db.commit()
            db.close()


class TestHeatmapMetricsEndpoint:
    def test_non_admin_gets_403(self, client, auth_headers):
        resp = client.get("/admin/heatmap-metrics", headers=auth_headers)
        assert resp.status_code == 403

    def test_shape_freshness_and_series(self, client):
        from app.jobs.build_pmtiles import capture_heatmap_metrics_snapshot

        admin_headers = _make_admin(client)
        source = f"test-{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        try:
            capture_heatmap_metrics_snapshot(db, source=source, min_uc=1)
        finally:
            db.close()

        try:
            resp = client.get("/admin/heatmap-metrics", headers=admin_headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            # Level-2 series
            assert "series" in data and isinstance(data["series"], list)
            assert data["count"] == len(data["series"])
            mine = [p for p in data["series"] if p["source"] == source]
            assert len(mine) == 1
            point = mine[0]
            for k in ("captured_at", "heat_edges", "agg_ways", "activities",
                      "contributors", "network_km", "grid_fallback_pct", "source"):
                assert k in point, k

            # Level-1 freshness object
            fr = data["freshness"]
            for k in ("last_activity_at", "heat_agg_updated_at", "heat_edges_agg",
                      "last_resync_at", "last_resync_status", "strava_connected",
                      "strava_sync_failures", "strava_reconnect_required",
                      "reconnect_threshold", "webhook_subscription_present"):
                assert k in fr, k
            assert isinstance(fr["strava_reconnect_required"], bool)
            assert isinstance(fr["webhook_subscription_present"], bool)
        finally:
            db = SessionLocal()
            db.execute(sa_text("DELETE FROM heatmap_metrics WHERE source = :s"), {"s": source})
            db.commit()
            db.close()

    def test_series_is_chronological_ascending(self, client):
        admin_headers = _make_admin(client)
        base = f"test-{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        try:
            # Two snapshots with explicit increasing captured_at.
            db.execute(sa_text(
                "INSERT INTO heatmap_metrics (captured_at, source) "
                "VALUES (now() - interval '2 days', :s1), (now() - interval '1 day', :s2)"
            ), {"s1": f"{base}-old", "s2": f"{base}-new"})
            db.commit()
        finally:
            db.close()
        try:
            resp = client.get("/admin/heatmap-metrics", headers=admin_headers)
            assert resp.status_code == 200
            series = resp.json()["series"]
            ours = [p for p in series if p["source"].startswith(base)]
            assert [p["source"] for p in ours] == [f"{base}-old", f"{base}-new"]
        finally:
            db = SessionLocal()
            db.execute(sa_text("DELETE FROM heatmap_metrics WHERE source LIKE :p"), {"p": f"{base}%"})
            db.commit()
            db.close()


class TestFreshnessComputation:
    def test_reconnect_required_crosses_threshold(self, monkeypatch):
        """sync_failures >= STRAVA_RECONNECT_THRESHOLD flips reconnect_required."""
        from app.api.admin import _heatmap_freshness
        from app.api.integrations_strava import STRAVA_RECONNECT_THRESHOLD

        acct_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        db = SessionLocal()
        try:
            db.execute(sa_text(
                "INSERT INTO integration_accounts "
                "(id, user_id, provider, access_token, sync_failures) "
                "VALUES (:id, :uid, 'strava', 'tok', :sf)"
            ), {"id": acct_id, "uid": user_id, "sf": STRAVA_RECONNECT_THRESHOLD + 1})
            db.commit()

            monkeypatch.setenv("STRAVA_WEBHOOK_SUBSCRIPTION_ID", "sub-123")
            fr = _heatmap_freshness(db)
            assert fr["strava_connected"] >= 1
            assert fr["strava_sync_failures"] >= STRAVA_RECONNECT_THRESHOLD
            assert fr["strava_reconnect_required"] is True
            assert fr["reconnect_threshold"] == STRAVA_RECONNECT_THRESHOLD
            assert fr["webhook_subscription_present"] is True
        finally:
            db.execute(sa_text("DELETE FROM integration_accounts WHERE id = :id"), {"id": acct_id})
            db.commit()
            db.close()

    def test_webhook_absent_when_env_unset(self, monkeypatch):
        from app.api.admin import _heatmap_freshness

        monkeypatch.delenv("STRAVA_WEBHOOK_SUBSCRIPTION_ID", raising=False)
        db = SessionLocal()
        try:
            fr = _heatmap_freshness(db)
            assert fr["webhook_subscription_present"] is False
        finally:
            db.close()
