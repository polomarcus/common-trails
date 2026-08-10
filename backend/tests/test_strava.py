"""Tests for Strava integration (TEST_MODE=true — no network calls)."""
import json
from urllib.parse import parse_qs, urlparse


class TestStravaLogin:
    """Strava OAuth login/signup flow (no pre-existing account needed)."""

    def test_login_redirects(self, client, monkeypatch):
        """GET /login redirects to stub callback in TEST_MODE."""
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)
        monkeypatch.setattr(strava_mod, "TEST_MODE", True)

        resp = client.get(
            "/integrations/strava/login",
            follow_redirects=False,
        )
        assert resp.status_code in (302, 307)
        loc = resp.headers.get("location", "")
        assert "stub_code_test" in loc
        # State is a signed JWT (PR #349). Round-trip-decode it to
        # prove the redirect carries the login sentinel, instead of
        # asserting on a literal "stub_login" — that was a TEST_MODE-
        # only string from the old DB-backed state.
        from urllib.parse import parse_qs, urlparse

        from app.api.integrations_strava import (
            _STRAVA_LOGIN_SENTINEL,
            _decode_oauth_state,
        )
        state = parse_qs(urlparse(loc).query).get("state", [None])[0]
        assert state is not None, f"no state param in redirect: {loc}"
        assert _decode_oauth_state(state) == _STRAVA_LOGIN_SENTINEL

    def test_login_callback_creates_user_and_returns_token(self, client, monkeypatch):
        """Full flow: /login → /callback → redirect with JWT token."""
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)
        monkeypatch.setattr(strava_mod, "TEST_MODE", True)
        monkeypatch.setattr(strava_mod, "FRONTEND_URL", "http://localhost:3787")

        # Step 1: initiate login (sets state in _oauth_states)
        login_resp = client.get(
            "/integrations/strava/login",
            follow_redirects=False,
        )
        assert login_resp.status_code in (302, 307)

        # Step 2: follow the redirect to callback
        callback_url = login_resp.headers["location"]
        cb_resp = client.get(callback_url, follow_redirects=False)
        assert cb_resp.status_code in (302, 307)

        # Verify redirect includes user_id and auth cookie
        loc = cb_resp.headers["location"]
        assert "status=connected" in loc
        assert "user_id=" in loc

        # Extract auth cookie set on redirect and verify it works for /auth/me
        jwt_token = cb_resp.cookies.get("auth_token")
        assert jwt_token is not None, "auth_token cookie not set on redirect"
        me_resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        assert me_resp.status_code == 200
        assert "strava" in me_resp.json()["email"]

    def test_login_idempotent(self, client, monkeypatch):
        """Second login with same Strava ID returns the same user."""
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)
        monkeypatch.setattr(strava_mod, "TEST_MODE", True)
        monkeypatch.setattr(strava_mod, "FRONTEND_URL", "http://localhost:3787")

        user_ids = []
        for _ in range(2):
            login_resp = client.get(
                "/integrations/strava/login",
                follow_redirects=False,
            )
            callback_url = login_resp.headers["location"]
            cb_resp = client.get(callback_url, follow_redirects=False)
            loc = cb_resp.headers["location"]
            parsed = urlparse(loc)
            params = parse_qs(parsed.query)
            _user_id = params["user_id"][0]  # noqa: F841 — verify param exists
            jwt_token = cb_resp.cookies.get("auth_token")
            assert jwt_token is not None, "auth_token cookie not set on redirect"
            me_resp = client.get(
                "/auth/me",
                headers={"Authorization": f"Bearer {jwt_token}"},
            )
            user_ids.append(me_resp.json()["user_id"])

        assert user_ids[0] == user_ids[1]


class TestStravaConnect:
    def test_connect_disabled_returns_501(self, client, auth_headers, monkeypatch):
        """When ENABLE_STRAVA_INTEGRATION=false and TEST_MODE=false, returns 501."""
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", False)
        monkeypatch.setattr(strava_mod, "TEST_MODE", False)

        resp = client.get(
            "/integrations/strava/connect",
            headers=auth_headers,
            follow_redirects=False,
        )
        assert resp.status_code == 501

    def test_connect_enabled_test_mode_redirects(self, client, auth_headers, monkeypatch):
        """In TEST_MODE+enabled, /connect redirects to stub callback."""
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)
        monkeypatch.setattr(strava_mod, "TEST_MODE", True)

        resp = client.get(
            "/integrations/strava/connect",
            headers=auth_headers,
            follow_redirects=False,
        )
        # Should redirect to stub callback
        assert resp.status_code in (302, 307)
        assert "stub_code_test" in resp.headers.get("location", "")

    def test_disconnect_requires_auth(self, client, monkeypatch):
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)
        resp = client.post("/integrations/strava/disconnect")
        assert resp.status_code == 401


class TestStravaCallback:
    def test_callback_test_mode_redirects_to_frontend(self, client, monkeypatch):
        """In TEST_MODE, callback redirects to FRONTEND_URL/strava?status=connected."""
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)
        monkeypatch.setattr(strava_mod, "TEST_MODE", True)
        monkeypatch.setattr(strava_mod, "FRONTEND_URL", "http://localhost:3787")

        resp = client.get(
            "/integrations/strava/callback?code=stub_code_test&scope=read,activity:read_all",
            follow_redirects=False,
        )
        # Should redirect to dedicated Strava page
        assert resp.status_code in (302, 307)
        assert "/strava?status=connected" in resp.headers.get("location", "")

    def test_callback_disabled_returns_501(self, client, monkeypatch):
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", False)
        monkeypatch.setattr(strava_mod, "TEST_MODE", False)

        resp = client.get("/integrations/strava/callback?code=anything")
        assert resp.status_code == 501


class TestStravaImport:
    def test_import_all_test_mode(self, client, auth_headers, monkeypatch):
        """In TEST_MODE+enabled, import_all returns COMPLETED immediately."""
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)
        monkeypatch.setattr(strava_mod, "TEST_MODE", True)

        resp = client.post(
            "/integrations/strava/import_all",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "COMPLETED"
        assert data["imported_count"] >= 0

    def test_import_all_disabled(self, client, auth_headers, monkeypatch):
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", False)
        monkeypatch.setattr(strava_mod, "TEST_MODE", False)

        resp = client.post("/integrations/strava/import_all", headers=auth_headers)
        assert resp.status_code == 501

    def test_get_job_status(self, client, auth_headers, monkeypatch):
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)
        monkeypatch.setattr(strava_mod, "TEST_MODE", True)

        create_resp = client.post(
            "/integrations/strava/import_all",
            headers=auth_headers,
        )
        job_id = create_resp.json()["job_id"]

        resp = client.get(
            f"/integrations/strava/jobs/{job_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["status"] in ("COMPLETED", "RUNNING", "PENDING", "FAILED")

    def test_get_job_not_found(self, client, auth_headers, monkeypatch):
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)

        resp = client.get(
            "/integrations/strava/jobs/00000000-0000-0000-0000-000000000000",
            headers=auth_headers,
        )
        assert resp.status_code == 404


GEOJSON_LINE = json.dumps({
    "type": "LineString",
    "coordinates": [[4.83, 45.75], [4.84, 45.76], [4.85, 45.77]],
})


class TestStravaOnlyLoginE2E:
    """Full Strava-only auth flow: login via OAuth, use JWT for all features."""

    def _strava_login(self, client, monkeypatch):
        """Helper: complete Strava login flow and return (jwt_token, user_data)."""
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)
        monkeypatch.setattr(strava_mod, "TEST_MODE", True)
        monkeypatch.setattr(strava_mod, "FRONTEND_URL", "http://localhost:3787")

        login_resp = client.get("/integrations/strava/login", follow_redirects=False)
        assert login_resp.status_code in (302, 307)

        callback_url = login_resp.headers["location"]
        cb_resp = client.get(callback_url, follow_redirects=False)
        assert cb_resp.status_code in (302, 307)

        # Auth cookie is set on the redirect response
        jwt_token = cb_resp.cookies.get("auth_token")
        assert jwt_token is not None, "auth_token cookie not set on redirect"

        me_resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        assert me_resp.status_code == 200
        return jwt_token, me_resp.json()

    def test_strava_user_cannot_login_with_password(self, client, monkeypatch):
        """Strava-only users have no password — email/password login must fail."""
        token, user_data = self._strava_login(client, monkeypatch)

        resp = client.post(
            "/auth/login",
            data={"username": user_data["email"], "password": "anything"},
        )
        assert resp.status_code == 401

    def test_strava_user_can_create_route(self, client, monkeypatch):
        """Strava-created account can create and retrieve routes."""
        token, _ = self._strava_login(client, monkeypatch)
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/routes",
            json={"name": "Strava Route", "sport": "road", "visibility": "public",
                  "geometry_geojson": GEOJSON_LINE},
            headers=headers,
        )
        assert resp.status_code == 201
        route_id = resp.json()["id"]

        get_resp = client.get(f"/routes/{route_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "Strava Route"

    def test_strava_user_can_create_trip(self, client, monkeypatch):
        """Strava-created account can create trips."""
        token, _ = self._strava_login(client, monkeypatch)
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/trips",
            json={"name": "Strava Trip", "sport": "gravel", "visibility": "private", "status": "draft"},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "Strava Trip"

    def test_strava_user_can_access_drafts(self, client, monkeypatch):
        """Strava-created account can save and list drafts."""
        token, _ = self._strava_login(client, monkeypatch)
        headers = {"Authorization": f"Bearer {token}"}

        # Create a draft
        client.post(
            "/routes",
            json={"name": "Strava Draft", "sport": "mtb", "visibility": "private", "status": "draft"},
            headers=headers,
        )

        resp = client.get("/me/drafts", headers=headers)
        assert resp.status_code == 200
        names = [d["name"] for d in resp.json()]
        assert "Strava Draft" in names

    def test_strava_user_can_import_activities(self, client, monkeypatch):
        """Strava-created account can trigger activity import."""
        import app.api.integrations_strava as strava_mod
        token, _ = self._strava_login(client, monkeypatch)
        headers = {"Authorization": f"Bearer {token}"}

        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)
        monkeypatch.setattr(strava_mod, "TEST_MODE", True)

        resp = client.post("/integrations/strava/import_all", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "COMPLETED"


class TestPhotoImportShortSession:
    """Pin the short-session-per-batch invariant for `_run_photo_import`.

    Phase 4 of the Strava import used to open one `SessionLocal()` at the
    top of the function and hold it across `await asyncio.gather` + every
    rate-limit `asyncio.sleep`. On db-f1-micro that pins one of the 5+3
    pool connections idle for the entire phase duration — the same
    anti-pattern PR #314 fixed for Phases 1+2 and Phase 3. This test
    counts `SessionLocal()` invocations across a complete phase run and
    asserts the helper-per-batch shape so the regression can't sneak back.
    See `/tmp/strava-tough-review.md` Sev-1 #2.
    """

    def test_session_opens_once_per_helper_call_not_once_per_phase(
        self, monkeypatch,
    ):
        """`_run_photo_import` opens NO outer session; helpers each own theirs.

        Stubs the three helpers (`_photos_next_batch`, `_photos_apply_batch`,
        `_read_photo_progress`) so the test is independent of the DB schema
        / activity-photo fixtures. Each stub *would have* opened a
        short-lived session in real code — we count how many times the
        body calls them, and assert the body itself doesn't open one.
        """
        import asyncio

        import app.api.integrations_strava as strava_mod

        # Track calls to each helper. The presence of these calls is the
        # short-session signal — each one corresponds to one DB session
        # opened-and-closed inside the helper. The body of
        # `_run_photo_import` must call them and never `SessionLocal()`
        # directly.
        calls: dict[str, int] = {"next": 0, "apply": 0, "read": 0, "session": 0}

        # First call returns one batch with two activities; subsequent
        # calls return empty → terminates the while-loop after one batch.
        batches = [
            [("act-1", "900001", "Photo Test 1"), ("act-2", "900002", "Photo Test 2")],
            [],
        ]

        def fake_next_batch(user_id: str, batch_size: int):
            calls["next"] += 1
            return batches.pop(0) if batches else []

        def fake_apply_batch(job_id, user_id, inserts, failed_delta=0, last_error=None):
            calls["apply"] += 1
            # Count how many we'd have inserted (parity with real signature).
            return len(inserts)

        def fake_read_progress(job_id: str):
            calls["read"] += 1
            return 4, 0  # 2 activities × 2 stub photos, no errors

        # If anything in `_run_photo_import` opens a session directly,
        # this counter trips — the test will then show "session > 0",
        # which fails the assertion at the end.
        real_session_local = strava_mod.SessionLocal

        def detecting_session_local(*args, **kwargs):
            calls["session"] += 1
            return real_session_local(*args, **kwargs)

        monkeypatch.setattr(strava_mod, "_photos_next_batch", fake_next_batch)
        monkeypatch.setattr(strava_mod, "_photos_apply_batch", fake_apply_batch)
        monkeypatch.setattr(strava_mod, "_read_photo_progress", fake_read_progress)
        monkeypatch.setattr(strava_mod, "SessionLocal", detecting_session_local)

        # Stub the photo-fetch so we don't reach the network.
        async def fake_get_photos(token, prov_id):
            return [{
                "unique_id": f"stub_{prov_id}",
                "urls": {"600": "x", "100": "x"},
                "location": [45.0, 4.0],
                "caption": "",
            }]
        monkeypatch.setattr(strava_mod, "get_activity_photos", fake_get_photos)

        # Make the inter-batch pause instant so the test stays fast.
        async def _instant_sleep(_):
            return None
        monkeypatch.setattr(strava_mod.asyncio, "sleep", _instant_sleep)

        # Stub the notification emit too — it lazy-imports inside the
        # function so monkeypatch on the module won't catch it; replace
        # the attribute on the lazily-imported module instead.
        import app.services.notifications as notif_mod
        emit_calls: list[dict] = []
        monkeypatch.setattr(
            notif_mod, "emit_notification",
            lambda **kw: emit_calls.append(kw),
        )

        asyncio.run(strava_mod._run_photo_import(
            job_id="job-test",
            user_id="user-test",
            access_token="stub_token",
        ))

        # Expectations under the per-batch-short-session pattern:
        # - `_photos_next_batch` called once per batch (data batch + the
        #   empty terminator) → 2 calls.
        # - `_photos_apply_batch` called once per data batch (the empty
        #   terminator returns early without calling apply) → 1 call.
        # - `_read_photo_progress` called once at end-of-phase → 1 call.
        # - `SessionLocal` NEVER called from the body itself.
        assert calls["next"] == 2, f"Expected 2 batch reads, got {calls['next']}"
        assert calls["apply"] == 1, f"Expected 1 batch apply, got {calls['apply']}"
        assert calls["read"] == 1, f"Expected 1 progress read, got {calls['read']}"
        assert calls["session"] == 0, (
            f"`_run_photo_import` opened {calls['session']} sessions directly — "
            f"it must delegate ALL session lifetimes to the per-batch helpers"
        )
        # End-of-phase notification should have fired (4 photos > 0).
        assert len(emit_calls) == 1
        assert emit_calls[0]["kind"] == "strava_photo_import_complete"

    def test_run_photo_import_has_no_outer_session(self):
        """Source-level guard: `_run_photo_import` body must not contain
        `db = SessionLocal()` at the top — that pattern is what PR #314
        eradicated from Phases 1+2 and Phase 3. Catches the regression at
        review time, not just at prod-blowup time."""
        import inspect

        import app.api.integrations_strava as strava_mod

        src = inspect.getsource(strava_mod._run_photo_import)
        # The function body must not assign `SessionLocal()` to any local
        # name — all session lifetimes belong to the helpers.
        assert "SessionLocal()" not in src, (
            "_run_photo_import body opens an outer session — should delegate "
            "to _photos_next_batch / _photos_apply_batch / _read_photo_progress"
        )
        # And the per-batch helpers must exist.
        assert hasattr(strava_mod, "_photos_next_batch")
        assert hasattr(strava_mod, "_photos_apply_batch")
        assert hasattr(strava_mod, "_read_photo_progress")
