"""Non-regression tests for the abuse/cost hardening sweep (2026-08-09).

One test per audit fix, each driving the REAL handler / helper (never an inline
mirror), each FAILING on the pre-fix code:

  HIGH 1  rate-limit key must ignore a spoofed LEADING X-Forwarded-For hop
  HIGH 2a build-pmtiles trigger is debounced (cost control)
  HIGH 2b /gpx/upload + /imports/files are per-user rate limited
  HIGH 3  /imports/strava-archive/init enforces per-user quotas (429)
  MED 4   /heatmap/stats + /heatmap/export are version-cached (no DB re-hit)
  MED 5   /geocode/reverse is per-IP rate limited + fast-fails when saturated
"""
from __future__ import annotations

import uuid

import pytest


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    """Minimal stand-in for a Starlette Request for the key-func unit tests."""

    def __init__(self, headers=None, client_host="9.9.9.9", cookies=None) -> None:
        self.headers = headers or {}
        self.client = _FakeClient(client_host) if client_host else None
        self.cookies = cookies or {}


VALID_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>tiny</name><trkseg>
    <trkpt lat="43.61" lon="3.87"><ele>100</ele></trkpt>
    <trkpt lat="43.62" lon="3.88"><ele>110</ele></trkpt>
  </trkseg></trk>
</gpx>"""


# ── HIGH 1 — spoofable rate-limit key ─────────────────────────────────────────
class TestRealClientIpKey:
    def test_spoofed_leading_xff_does_not_change_key(self):
        """A client rotating the LEADING XFF entry must NOT get a fresh bucket.

        FAILS on the old code (keyed on split(',')[0], so the two requests below
        produce different keys → unlimited fresh buckets). PASSES now: keyed on
        the rightmost (Google-appended) hop.
        """
        from app.rate_limit import _get_real_ip

        # Real client IP is the LAST hop Google appends; the client controls the
        # leading entries. Two requests that differ ONLY in the spoofed leading
        # entry must derive the SAME key.
        key_a = _get_real_ip(
            _FakeRequest({"X-Forwarded-For": "203.0.113.7, 70.1.1.1, 35.191.0.1"})
        )
        key_b = _get_real_ip(
            _FakeRequest({"X-Forwarded-For": "6.6.6.6, 9.9.9.9, 35.191.0.1"})
        )
        assert key_a == key_b == "35.191.0.1"

    def test_no_xff_falls_back_to_socket_peer(self):
        from app.rate_limit import _get_real_ip

        assert _get_real_ip(_FakeRequest({}, client_host="8.8.8.8")) == "8.8.8.8"

    def test_user_or_ip_key_prefers_user_then_ip(self):
        from app.api.auth import _create_token
        from app.rate_limit import user_or_ip_key

        uid = str(uuid.uuid4())
        token = _create_token(uid, "x@example.com")
        # Bearer token → stable per-user key regardless of source IP.
        assert (
            user_or_ip_key(
                _FakeRequest(
                    {"Authorization": f"Bearer {token}", "X-Forwarded-For": "1.2.3.4"}
                )
            )
            == f"user:{uid}"
        )
        # No token → trustworthy IP key.
        assert user_or_ip_key(_FakeRequest({}, client_host="5.6.7.8")) == "ip:5.6.7.8"


# ── HIGH 2a — build-pmtiles trigger debounce ─────────────────────────────────
class TestBuildPmtilesDebounce:
    def test_trigger_debounced_within_cooldown(self, monkeypatch):
        """Real debounce helper: first call fires, subsequent calls inside the
        cooldown are suppressed, a call past the cooldown fires again."""
        from app.services import run_jobs

        monkeypatch.setenv("BUILD_PMTILES_DEBOUNCE_S", "600")
        run_jobs.reset_build_pmtiles_debounce()
        assert run_jobs.build_pmtiles_debounce_ok(now=1000.0) is True
        assert run_jobs.build_pmtiles_debounce_ok(now=1100.0) is False  # within 600s
        assert run_jobs.build_pmtiles_debounce_ok(now=1300.0) is False  # still within
        assert run_jobs.build_pmtiles_debounce_ok(now=1601.0) is True   # cooldown elapsed

    def test_cooldown_zero_disables_debounce(self, monkeypatch):
        from app.services import run_jobs

        monkeypatch.setenv("BUILD_PMTILES_DEBOUNCE_S", "0")
        run_jobs.reset_build_pmtiles_debounce()
        assert run_jobs.build_pmtiles_debounce_ok(now=1.0) is True
        assert run_jobs.build_pmtiles_debounce_ok(now=1.0) is True


# ── HIGH 2b — per-user upload rate limit ─────────────────────────────────────
class TestUploadRateLimit:
    def test_gpx_upload_is_per_user_rate_limited(self, client, monkeypatch):
        """The real /gpx/upload handler returns 429 once the per-user limit is
        exceeded. Drives the real slowapi decorator + user_or_ip_key."""
        from app.rate_limit import limiter

        monkeypatch.setenv("UPLOAD_RATE_LIMIT", "2/hour")
        email = f"rl_{uuid.uuid4().hex[:8]}@example.com"
        reg = client.post(
            "/auth/register",
            json={"email": email, "password": "testpass123",
                  "username": f"u_{uuid.uuid4().hex[:6]}"},
        )
        headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}

        limiter.enabled = True
        limiter.reset()
        try:
            codes = []
            for _ in range(3):
                resp = client.post(
                    "/gpx/upload",
                    headers=headers,
                    files={"file": ("t.gpx", VALID_GPX, "application/gpx+xml")},
                    data={"sport": "road", "contribute_heatmap": "false"},
                )
                codes.append(resp.status_code)
            assert codes[0] == 202 and codes[1] == 202, codes
            assert codes[2] == 429, codes
        finally:
            limiter.enabled = False
            limiter.reset()

    def test_imports_files_is_per_user_rate_limited(self, client, monkeypatch):
        from app.rate_limit import limiter

        monkeypatch.setenv("UPLOAD_RATE_LIMIT", "2/hour")
        email = f"rl2_{uuid.uuid4().hex[:8]}@example.com"
        reg = client.post(
            "/auth/register",
            json={"email": email, "password": "testpass123",
                  "username": f"u_{uuid.uuid4().hex[:6]}"},
        )
        headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}

        limiter.enabled = True
        limiter.reset()
        try:
            codes = []
            for _ in range(3):
                resp = client.post(
                    "/imports/files",
                    headers=headers,
                    files={"file": ("t.gpx", VALID_GPX, "application/gpx+xml")},
                    data={"sport": "road", "contribute_heatmap": "false"},
                )
                codes.append(resp.status_code)
            assert codes[:2] == [202, 202], codes
            assert codes[2] == 429, codes
        finally:
            limiter.enabled = False
            limiter.reset()


# ── HIGH 3 — per-user archive quotas ─────────────────────────────────────────
@pytest.fixture
def _local_archive_backend(monkeypatch, tmp_path):
    from app.services import archive_intake

    monkeypatch.setattr(archive_intake, "ARCHIVE_INTAKE_DIR", str(tmp_path / "intake"))
    monkeypatch.setattr(archive_intake, "UPLOADS_BUCKET", "")


def _init_body() -> dict:
    return {
        "sport": "road",
        "consent": True,
        "consent_version": "v-test",
        "consent_text": "Je consens (ODbL).",
        "locale": "fr",
        "filename": "export.zip",
    }


class TestArchiveInitQuota:
    def test_open_archive_cap_returns_429(self, client, auth_headers, monkeypatch, _local_archive_backend):
        """Real /init handler: once MAX_OPEN open archives exist, further init
        calls are rejected 429 (before minting a URL / recording consent)."""
        monkeypatch.setattr("app.api.imports.MAX_OPEN_ARCHIVES_PER_USER", 2)
        monkeypatch.setattr("app.api.imports.MAX_ARCHIVE_INITS_PER_DAY", 100)

        for _ in range(2):
            assert client.post(
                "/imports/strava-archive/init", headers=auth_headers, json=_init_body()
            ).status_code == 201
        resp = client.post(
            "/imports/strava-archive/init", headers=auth_headers, json=_init_body()
        )
        assert resp.status_code == 429, resp.text
        assert "in progress" in resp.json()["detail"]

    def test_daily_init_cap_returns_429(self, client, auth_headers, monkeypatch, _local_archive_backend):
        monkeypatch.setattr("app.api.imports.MAX_OPEN_ARCHIVES_PER_USER", 100)
        monkeypatch.setattr("app.api.imports.MAX_ARCHIVE_INITS_PER_DAY", 2)

        for _ in range(2):
            assert client.post(
                "/imports/strava-archive/init", headers=auth_headers, json=_init_body()
            ).status_code == 201
        resp = client.post(
            "/imports/strava-archive/init", headers=auth_headers, json=_init_body()
        )
        assert resp.status_code == 429, resp.text
        assert "Daily" in resp.json()["detail"]


# ── MEDIUM 4 — stats/export version cache + bounded timeout ──────────────────
class TestHeatmapCostGuards:
    def test_stats_served_from_version_cache(self, client, monkeypatch):
        """A second identical /heatmap/stats call is served from the in-memory
        version cache with NO call into the DB aggregation function."""
        from app.api import heatmap as hm

        hm._reset_tile_caches()
        calls = {"n": 0}
        real = hm.ingest_service.get_heat_cells_aggregated

        def _spy(*a, **k):
            calls["n"] += 1
            # The endpoint must pass a bounded statement timeout (cost guard).
            assert k.get("statement_timeout_ms") == hm._STATS_STATEMENT_TIMEOUT_MS
            return real(*a, **k)

        monkeypatch.setattr(hm.ingest_service, "get_heat_cells_aggregated", _spy)

        q = "?sport=road&min_lon=3.0&min_lat=43.0&max_lon=4.0&max_lat=44.0"
        assert client.get(f"/heatmap/stats{q}").status_code == 200
        n_after_first = calls["n"]
        assert n_after_first >= 1
        assert client.get(f"/heatmap/stats{q}").status_code == 200
        assert calls["n"] == n_after_first, "second call must hit the cache, not the DB"

    def test_export_served_from_version_cache(self, client, monkeypatch):
        from app.api import heatmap as hm

        hm._reset_tile_caches()
        calls = {"n": 0}
        real = hm.ingest_service.get_heat_cells_aggregated

        def _spy(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(hm.ingest_service, "get_heat_cells_aggregated", _spy)

        q = "?sport=road&min_lon=3.0&min_lat=43.0&max_lon=4.0&max_lat=44.0"
        assert client.get(f"/heatmap/export{q}").status_code == 200
        n1 = calls["n"]
        assert n1 >= 1
        assert client.get(f"/heatmap/export{q}").status_code == 200
        assert calls["n"] == n1, "second export must hit the cache, not the DB"


# ── MEDIUM 5 — geocode per-IP limit + saturation fast-fail ───────────────────
class TestGeocodeGuards:
    def test_reverse_is_per_ip_rate_limited(self, client, monkeypatch):
        """Real /geocode/reverse returns 429 once the per-IP limit is hit.

        Pre-seed the label cache so the handler never touches the network — the
        rate limit is evaluated BEFORE the body, so cache hits still count.
        """
        from app.api import geocode
        from app.rate_limit import limiter

        monkeypatch.setenv("GEOCODE_RATE_LIMIT", "2/minute")
        lat, lon = 43.6112, 3.8767
        geocode._label_cache[(round(lat, 4), round(lon, 4))] = "TestVille"

        limiter.enabled = True
        limiter.reset()
        try:
            codes = [
                client.get(f"/geocode/reverse?lat={lat}&lon={lon}").status_code
                for _ in range(3)
            ]
            assert codes[:2] == [200, 200], codes
            assert codes[2] == 429, codes
        finally:
            limiter.enabled = False
            limiter.reset()

    def test_reverse_fast_fails_when_semaphore_saturated(self, client, monkeypatch):
        """When the upstream semaphore is saturated the handler returns 429
        immediately instead of awaiting a slot / hitting Nominatim."""
        from app.api import geocode

        class _AlwaysLocked:
            def locked(self):
                return True

        monkeypatch.setattr(geocode, "_nominatim_semaphore", _AlwaysLocked())
        # Fresh coords → cache miss → reaches the saturation check.
        resp = client.get("/geocode/reverse?lat=48.85&lon=2.35")
        assert resp.status_code == 429, resp.text
        assert "busy" in resp.json()["detail"]
