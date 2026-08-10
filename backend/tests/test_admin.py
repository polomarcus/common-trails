"""Admin dashboard endpoint smoke tests.

The admin module was untested when these endpoints landed; these tests
pin the new shape (engagement stats, provider split, heatmap quality,
recent-routes, recent-activities) so a future schema rename doesn't
silently break the admin page.
"""
from __future__ import annotations

import uuid


def _make_admin(client) -> dict[str, str]:
    """Register a fresh test user and flip is_admin=True directly in the DB."""
    email = f"admin_{uuid.uuid4().hex[:8]}@example.com"
    username = f"adm_{uuid.uuid4().hex[:6]}"
    resp = client.post("/auth/register", json={
        "email": email, "password": "testpass123", "username": username,
    })
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]

    # Promote to admin via direct DB write — there's no /admin/promote endpoint.
    from sqlalchemy import text as sa_text

    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        db.execute(
            sa_text("UPDATE users SET is_admin = true WHERE email = :e"),
            {"e": email},
        )
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}


class TestAdminDashboard:
    def test_non_admin_user_gets_403(self, client, auth_headers):
        resp = client.get("/admin/dashboard", headers=auth_headers)
        assert resp.status_code == 403

    def test_dashboard_shape(self, client):
        admin_headers = _make_admin(client)
        resp = client.get("/admin/dashboard", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Pre-existing keys still there
        assert "users" in data
        assert "activities" in data
        assert "routes" in data
        assert "heatmap" in data
        assert "graph_health" in data

        # New keys from the 2026-05-16 admin extension
        assert "signups_7d" in data["users"]
        assert "signups_30d" in data["users"]
        assert "active_7d" in data["users"]
        assert "active_30d" in data["users"]
        assert "by_provider" in data["activities"]
        assert "integrations" in data
        assert "strava_connected" in data["integrations"]
        assert "osm_way_id_pct" in data["heatmap"]
        assert "grid_fallback_pct" in data["heatmap"]
        assert "system" in data
        assert "revision" in data["system"]
        # Types: all the percentages are numeric, never None
        assert isinstance(data["heatmap"]["osm_way_id_pct"], int | float)
        assert isinstance(data["users"]["signups_7d"], int)


class TestHeatEdgeStatsGuard:
    """`_heat_edge_stats` must be fail-soft on db-f1-micro.

    An unbounded `SELECT count(*) FROM heat_edges` full scan of the ~5 M-row
    table severed the f1-micro connection in the build_pmtiles path (#442).
    The same scans back the now-live /admin dashboard (#443); if any of them
    hangs / OOMs / raises, the helper must degrade to `-1` sentinels and roll
    the transaction back — NEVER propagate and 500 the whole dashboard.

    Drives the real helper (no inline mirror) via a MagicMock session.
    """

    def test_scan_failure_degrades_to_sentinels_and_rolls_back(self):
        from unittest.mock import MagicMock

        from app.api.admin import _heat_edge_stats

        db = MagicMock()
        # Every heavy read raises — SET LOCAL (execute w/o .scalar) is fine.
        db.query.return_value.scalar.side_effect = RuntimeError("server closed the connection unexpectedly")
        db.execute.return_value.scalar.side_effect = RuntimeError("server closed the connection unexpectedly")

        # Must not raise.
        result = _heat_edge_stats(db)

        hm = result["heatmap"]
        assert hm["edges"] == -1
        assert hm["cells"] == -1
        assert hm["max_contributors_on_edge"] == -1
        assert hm["osm_tagged"] == -1
        assert hm["grid_fallback"] == -1
        # Percentages stay numeric (the frontend renders them directly).
        assert hm["osm_way_id_pct"] == 0.0
        assert hm["grid_fallback_pct"] == 0.0

        gh = result["graph_health"]
        assert gh["total_vertices"] == -1
        assert gh["dead_ends"] == -1
        assert gh["dead_end_pct"] == 0

        # The transaction was rolled back so the pooled connection is reusable
        # (once per guarded block that failed).
        assert db.rollback.call_count >= 1

    def test_happy_path_computes_real_percentages(self):
        from unittest.mock import MagicMock

        from app.api.admin import _heat_edge_stats

        db = MagicMock()
        # edges, cells, max_contributors (3 ORM count/agg .scalar() calls)
        db.query.return_value.scalar.side_effect = [1000, 50, 7]
        # osm_tagged, dead_ends, total_vertices (3 raw-SQL .scalar() calls;
        # the SET LOCAL executes don't call .scalar()).
        db.execute.return_value.scalar.side_effect = [500, 30, 100]

        result = _heat_edge_stats(db)

        hm = result["heatmap"]
        assert hm["edges"] == 1000
        assert hm["cells"] == 50
        assert hm["max_contributors_on_edge"] == 7
        assert hm["osm_tagged"] == 500
        assert hm["grid_fallback"] == 500
        assert hm["osm_way_id_pct"] == 50.0
        assert hm["grid_fallback_pct"] == 50.0

        gh = result["graph_health"]
        assert gh["dead_ends"] == 30
        assert gh["total_vertices"] == 100
        assert gh["dead_end_pct"] == 30.0
        db.rollback.assert_not_called()


class TestAdminRecentRoutes:
    def test_non_admin_gets_403(self, client, auth_headers):
        resp = client.get("/admin/routes/recent", headers=auth_headers)
        assert resp.status_code == 403

    def test_limit_param_validated(self, client):
        admin_headers = _make_admin(client)
        # Out-of-bound limit values are rejected by FastAPI's Query() guard.
        resp = client.get("/admin/routes/recent?limit=0", headers=admin_headers)
        assert resp.status_code == 422
        resp = client.get("/admin/routes/recent?limit=999", headers=admin_headers)
        assert resp.status_code == 422

    def test_returns_recent_route_with_creator(self, client, auth_headers):
        # Create a route as the (non-admin) auth_headers user so we know
        # something exists with a known creator.
        post = client.post("/routes", json={
            "name": "test admin recent route",
            "sport": "gravel",
            "visibility": "public",
        }, headers=auth_headers)
        assert post.status_code in (200, 201), post.text

        admin_headers = _make_admin(client)
        resp = client.get("/admin/routes/recent?limit=5", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 5
        assert isinstance(body["items"], list)
        # The just-created route should be in the most-recent slice.
        names = [r["name"] for r in body["items"]]
        assert "test admin recent route" in names
        # Shape sanity
        if body["items"]:
            first = body["items"][0]
            for key in ("id", "name", "sport", "visibility", "created_at"):
                assert key in first


class TestAdminRecentActivities:
    def test_non_admin_gets_403(self, client, auth_headers):
        resp = client.get("/admin/activities/recent", headers=auth_headers)
        assert resp.status_code == 403

    def test_shape(self, client):
        admin_headers = _make_admin(client)
        resp = client.get("/admin/activities/recent?limit=3", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 3
        assert isinstance(body["items"], list)
        # If any activities exist in this test DB, they must have a
        # `provider` field (the whole point of this endpoint vs the
        # dashboard's aggregated `by_provider`).
        for item in body["items"]:
            assert "provider" in item
            assert "sport" in item
            assert "created_at" in item

    def test_returns_recent_activity_with_creator(self, client):
        """Positive-path: insert one activity directly and verify it shows
        up in `/admin/activities/recent` with the correct provider + author
        username. Exercises the JOIN path that the shape test doesn't reach
        when the test DB starts empty."""
        from sqlalchemy import text as sa_text

        from app.db.session import SessionLocal

        # Register a user, then insert an Activity for them directly via SQL
        # (bypassing the GPX upload flow — we only need a row in the table).
        email = f"actor_{uuid.uuid4().hex[:8]}@example.com"
        username = f"actor_{uuid.uuid4().hex[:6]}"
        resp = client.post("/auth/register", json={
            "email": email, "password": "testpass123", "username": username,
        })
        assert resp.status_code == 201, resp.text

        activity_id = str(uuid.uuid4())
        db = SessionLocal()
        try:
            row = db.execute(sa_text(
                "SELECT id FROM users WHERE email = :e"
            ), {"e": email}).fetchone()
            assert row is not None
            user_id = row[0]
            db.execute(sa_text("""
                INSERT INTO activities (id, user_id, provider, sport, name,
                    distance_m, elevation_gain_m, contribute_heatmap, created_at)
                VALUES (:id, :uid, 'file', 'gravel', 'admin-test-activity',
                    12345, 234, true, NOW())
            """), {"id": activity_id, "uid": user_id})
            db.commit()
        finally:
            db.close()

        admin_headers = _make_admin(client)
        resp = client.get("/admin/activities/recent?limit=20", headers=admin_headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        match = next((i for i in items if i["id"] == activity_id), None)
        assert match is not None, (
            f"Inserted activity {activity_id} not returned by /admin/activities/recent. "
            f"Got {len(items)} items."
        )
        assert match["provider"] == "file"
        assert match["sport"] == "gravel"
        assert match["username"] == username  # JOIN landed correctly
        assert match["distance_m"] == 12345


class TestAdminUsers:
    """Per-user observability dashboard (GET /admin/users)."""

    def test_non_admin_gets_403(self, client, auth_headers):
        resp = client.get("/admin/users", headers=auth_headers)
        assert resp.status_code == 403

    def test_admin_sees_users_shape(self, client):
        admin_headers = _make_admin(client)
        resp = client.get("/admin/users", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "total_users" in body
        assert isinstance(body["users"], list)
        assert body["total_users"] == len(body["users"])
        # The admin we just made must be present, flagged is_admin.
        admins = [u for u in body["users"] if u["is_admin"]]
        assert admins, "expected at least one is_admin user"
        u = body["users"][0]
        for key in ("id", "email", "username", "is_admin", "created_at",
                    "strava", "activities"):
            assert key in u, f"missing {key}"
        acts = u["activities"]
        for key in ("total", "total_distance_m", "by_sport", "by_provider",
                    "first_activity_date", "last_activity_date",
                    "skipped_import_count", "failed_import_count"):
            assert key in acts, f"missing activities.{key}"

    def test_per_user_aggregation_and_strava(self, client):
        """Insert a user with 2 gravel + 1 road activity and a Strava
        account, then assert the per-user aggregation + Strava linkage
        shape returned by the real handler."""
        from sqlalchemy import text as sa_text

        from app.db.session import SessionLocal

        email = f"agg_{uuid.uuid4().hex[:8]}@example.com"
        username = f"agg_{uuid.uuid4().hex[:6]}"
        resp = client.post("/auth/register", json={
            "email": email, "password": "testpass123", "username": username,
        })
        assert resp.status_code == 201, resp.text

        db = SessionLocal()
        try:
            uid = db.execute(
                sa_text("SELECT id FROM users WHERE email = :e"), {"e": email}
            ).fetchone()[0]
            for sport, dist in [("gravel", 1000), ("gravel", 2000), ("road", 5000)]:
                db.execute(sa_text("""
                    INSERT INTO activities (id, user_id, provider, sport, name,
                        distance_m, contribute_heatmap, activity_date, created_at)
                    VALUES (:id, :uid, 'file', :sport, 'agg-act',
                        :dist, true, NOW(), NOW())
                """), {"id": str(uuid.uuid4()), "uid": uid, "sport": sport, "dist": dist})
            # A Strava account with a numeric athlete id + failures over the
            # reconnect threshold (default 3) so reconnect_required flips true.
            db.execute(sa_text("""
                INSERT INTO integration_accounts (id, user_id, provider,
                    access_token, external_user_id, athlete_name,
                    sync_failures, expires_at)
                VALUES (:id, :uid, 'strava', 'tok', '12345678',
                    'Test Athlete', 5, :exp)
            """), {"id": str(uuid.uuid4()), "uid": uid, "exp": 1})
            db.commit()
        finally:
            db.close()

        admin_headers = _make_admin(client)
        resp = client.get("/admin/users", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        row = next((u for u in resp.json()["users"] if u["id"] == str(uid)), None)
        assert row is not None, "inserted user not returned"

        acts = row["activities"]
        assert acts["total"] == 3
        assert acts["by_sport"]["gravel"]["count"] == 2
        assert acts["by_sport"]["gravel"]["distance_m"] == 3000
        assert acts["by_sport"]["road"]["count"] == 1
        assert acts["total_distance_m"] == 8000
        assert acts["by_provider"]["file"] == 3
        assert acts["first_activity_date"] is not None

        strava = row["strava"]
        assert strava is not None
        assert strava["athlete_id"] == "12345678"  # → strava.com/athletes/{id}
        assert strava["athlete_name"] == "Test Athlete"
        assert strava["sync_failures"] == 5
        assert strava["reconnect_required"] is True  # 5 >= threshold 3
        assert strava["token_expired"] is True  # expires_at=1 is in the past


class TestAdminArchives:
    """Whole-archive queue observability (GET /admin/archives).

    Beta safety net: a friend's big-archive upload that FAILS in the paced
    drain is invisible to them by design — the admin must be able to SEE the
    stuck/failed `pending_archives` row + its last_error here.
    """

    def test_non_admin_gets_403(self, client, auth_headers):
        resp = client.get("/admin/archives", headers=auth_headers)
        assert resp.status_code == 403

    def test_anonymous_gets_401(self, client):
        resp = client.get("/admin/archives")
        assert resp.status_code == 401

    def test_admin_sees_failed_archive_with_error_and_counts(self, client):
        """Seed one FAILED pending_archives row directly (bypassing the
        signed-URL upload flow — we only need the bookkeeping row), then
        assert the real handler surfaces it: correct status counts, the
        owner's email via the JOIN, drain progress numbers, and the
        last_error truncated to ~300 chars (head kept — that's where the
        traceback/reason lives)."""
        from sqlalchemy import text as sa_text

        from app.db.session import SessionLocal

        email = f"arch_{uuid.uuid4().hex[:8]}@example.com"
        username = f"arch_{uuid.uuid4().hex[:6]}"
        resp = client.post("/auth/register", json={
            "email": email, "password": "testpass123", "username": username,
        })
        assert resp.status_code == 201, resp.text

        archive_id = str(uuid.uuid4())
        # Head must survive truncation; total length 400 > the 300-char cap.
        long_error = "BadZipFile: File is not a zip file" + " x" * 183
        assert len(long_error) == 400
        db = SessionLocal()
        try:
            uid = db.execute(
                sa_text("SELECT id FROM users WHERE email = :e"), {"e": email}
            ).fetchone()[0]
            db.execute(sa_text("""
                INSERT INTO pending_archives (id, user_id, storage_backend,
                    bucket_key, status, attempts, members_total,
                    imported, skipped, failed, last_error)
                VALUES (:id, :uid, 'local', 'archive-intake/test.zip',
                    'failed', 3, 1406, 100, 5, 2, :err)
            """), {"id": archive_id, "uid": uid, "err": long_error})
            db.commit()
        finally:
            db.close()

        admin_headers = _make_admin(client)
        resp = client.get("/admin/archives", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # (a) counts by status — every lifecycle status is always present
        # (zero-filled) so the frontend pills never KeyError.
        for status in ("awaiting_upload", "uploaded", "processing", "done", "failed"):
            assert status in body["counts"], f"missing counts[{status}]"
        assert body["counts"]["failed"] >= 1

        # (b) the seeded row is in the recent slice with its diagnostics.
        item = next((i for i in body["items"] if i["id"] == archive_id), None)
        assert item is not None, (
            f"Seeded archive {archive_id} not returned by /admin/archives. "
            f"Got {len(body['items'])} items."
        )
        assert item["email"] == email  # JOIN landed correctly
        assert item["user_id"] == str(uid)
        assert item["status"] == "failed"
        assert item["attempts"] == 3
        assert item["members_total"] == 1406
        assert item["imported"] == 100
        assert item["skipped"] == 5
        assert item["failed"] == 2
        # Truncated to 300 chars, head (the actual reason) preserved.
        assert len(item["last_error"]) == 300
        assert item["last_error"].startswith("BadZipFile: File is not a zip file")
        assert item["created_at"] is not None
        assert item["updated_at"] is not None
