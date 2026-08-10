"""Tests for the Strava client hardening (PR C from the workflow audit).

Covers:
- StravaRateLimited is raised on HTTP 429 with parsed Retry-After.
- _run_gps_upgrade honours `retry_after` from the exception (vs. flat 60s).
- The shared httpx client is a process-wide singleton.
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest

import app.services.strava_client as sc

# ── 429 → StravaRateLimited ──────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict | None = None, json_body=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_body if json_body is not None else {}
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    """Drop-in stub for httpx.AsyncClient.get/post — yields scripted responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def get(self, *args, **kwargs):
        self.calls += 1
        return self._responses.pop(0) if self._responses else _FakeResponse(200, json_body=[])

    async def post(self, *args, **kwargs):
        return await self.get(*args, **kwargs)


@pytest.fixture(autouse=True)
def _disable_test_mode(monkeypatch):
    """The strava_client short-circuits on TEST_MODE — disable it for these
    tests so the real code paths run against the fake client."""
    monkeypatch.setattr(sc, "TEST_MODE", False)
    # Reset shared client between tests so singleton assertions are deterministic.
    sc._HTTPX_CLIENT = None
    sc._HTTPX_LOCK = None
    yield
    sc._HTTPX_CLIENT = None
    sc._HTTPX_LOCK = None


def test_parse_retry_after_default():
    assert sc._parse_retry_after({}) == sc._RATELIMIT_RETRY_AFTER_FALLBACK_S
    assert sc._parse_retry_after({"Retry-After": "42"}) == 42
    assert sc._parse_retry_after({"Retry-After": " 17  "}) == 17
    # Malformed Retry-After falls back to default.
    assert sc._parse_retry_after({"Retry-After": "soon"}, default=99) == 99
    # Negative values clamped to 0.
    assert sc._parse_retry_after({"Retry-After": "-5"}) == 0


def test_get_activity_stream_raises_strava_rate_limited_on_429(monkeypatch):
    fake = _FakeClient([_FakeResponse(429, headers={"Retry-After": "123"})])

    async def _fake_get_http_client():
        return fake

    monkeypatch.setattr(sc, "get_http_client", _fake_get_http_client)

    with pytest.raises(sc.StravaRateLimited) as exc_info:
        asyncio.run(sc.get_activity_stream("token", 123))

    assert exc_info.value.retry_after == 123
    assert exc_info.value.endpoint == "activity_stream"


def test_get_activity_stream_429_default_retry_after_when_header_missing(monkeypatch):
    fake = _FakeClient([_FakeResponse(429, headers={})])

    async def _fake_get_http_client():
        return fake

    monkeypatch.setattr(sc, "get_http_client", _fake_get_http_client)

    with pytest.raises(sc.StravaRateLimited) as exc_info:
        asyncio.run(sc.get_activity_stream("token", 1))
    assert exc_info.value.retry_after == sc._RATELIMIT_RETRY_AFTER_FALLBACK_S


def test_get_activity_photos_raises_strava_rate_limited_on_429(monkeypatch):
    fake = _FakeClient([_FakeResponse(429, headers={"Retry-After": "7"})])

    async def _fake_get_http_client():
        return fake

    monkeypatch.setattr(sc, "get_http_client", _fake_get_http_client)

    with pytest.raises(sc.StravaRateLimited) as exc_info:
        asyncio.run(sc.get_activity_photos("token", 1))
    assert exc_info.value.retry_after == 7
    assert exc_info.value.endpoint == "activity_photos"


def test_get_activity_stream_non_429_still_wraps_runtime_error(monkeypatch):
    """Non-rate-limit failures keep the legacy RuntimeError wrapping so
    existing string-match ("401 in err_str") branches still work."""
    fake = _FakeClient([_FakeResponse(500)])

    async def _fake_get_http_client():
        return fake

    monkeypatch.setattr(sc, "get_http_client", _fake_get_http_client)

    with pytest.raises(RuntimeError, match="get_activity_stream failed"):
        asyncio.run(sc.get_activity_stream("token", 1))


# ── Shared httpx client (singleton) ──────────────────────────────────────────


def test_get_http_client_returns_singleton():
    async def _drive():
        c1 = await sc.get_http_client()
        c2 = await sc.get_http_client()
        return c1, c2

    c1, c2 = asyncio.run(_drive())
    assert c1 is c2, "get_http_client must return the same instance across calls"
    # And the module-level slot points to that same instance.
    assert sc._HTTPX_CLIENT is c1

    async def _cleanup():
        await sc.aclose_http_client()
    asyncio.run(_cleanup())


def test_get_http_client_concurrent_init_is_safe():
    """Two coroutines racing to init must not create two clients."""
    async def _drive():
        return await asyncio.gather(sc.get_http_client(), sc.get_http_client())

    c1, c2 = asyncio.run(_drive())
    assert c1 is c2

    async def _cleanup():
        await sc.aclose_http_client()
    asyncio.run(_cleanup())


def test_aclose_http_client_is_idempotent():
    """Calling aclose twice (e.g. lifespan + signal handler) must not throw."""
    async def _drive():
        await sc.get_http_client()
        await sc.aclose_http_client()
        await sc.aclose_http_client()  # second call: client is None, noop
    asyncio.run(_drive())
    assert sc._HTTPX_CLIENT is None


# ── _run_gps_upgrade honours retry_after ─────────────────────────────────────


def test_run_gps_upgrade_sleeps_for_retry_after_not_flat_60(monkeypatch):
    """Replace `get_activity_stream` so it raises StravaRateLimited(retry_after=137)
    on the first batch and returns normally on the second. Assert the worker
    slept 137s (parsed from the exception), not 60s (the old hardcoded fallback).
    """
    import app.api.integrations_strava as mod

    # First call raises 429 (retry_after=137). Subsequent calls succeed and
    # exhaust the batch so the loop terminates.
    raised_once = {"done": False}

    async def _fake_get_stream(access_token, activity_id):
        if not raised_once["done"]:
            raised_once["done"] = True
            raise sc.StravaRateLimited(retry_after=137, endpoint="activity_stream")
        return {
            "latlng": {"data": [[45.0, 4.0], [45.1, 4.1]]},
            "altitude": {"data": [200.0, 210.0]},
        }

    # Two batches: first triggers the 429, second drains. Then an empty batch
    # exits the while-loop.
    batches = [
        [("a1", "111", "road")],
        [("a2", "222", "road")],
        [],
    ]
    batch_iter = iter(batches)

    def _fake_next_batch(_user_id, _n):
        return next(batch_iter, [])

    apply_calls: list = []

    def _fake_apply_batch(job_id, upgrades, upgraded_delta, stop_error):
        apply_calls.append((job_id, list(upgrades), upgraded_delta, stop_error))

    def _fake_update_heat(*args, **kwargs):
        pass

    def _fake_read_progress(_job_id):
        return (1, 1, 0)

    def _fake_emit_notification(*args, **kwargs):
        pass

    sleep_calls: list[float] = []

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(mod, "get_activity_stream", _fake_get_stream)
    monkeypatch.setattr(mod, "_gps_next_batch", _fake_next_batch)
    monkeypatch.setattr(mod, "_gps_apply_batch", _fake_apply_batch)
    import app.services.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "_update_heat_edges", _fake_update_heat)
    monkeypatch.setattr(mod, "_read_job_progress", _fake_read_progress)
    # _run_gps_upgrade calls `from app.services.notifications import emit_notification`
    # at the bottom — monkeypatch the symbol on its module so the in-function
    # import resolves to our stub.
    import app.services.notifications as notif_mod
    monkeypatch.setattr(notif_mod, "emit_notification", _fake_emit_notification)
    monkeypatch.setattr(mod.asyncio, "sleep", _fake_sleep)

    asyncio.run(mod._run_gps_upgrade("job-1", "user-1", "token"))

    # The rate-limited batch must have slept the EXACT value from the exception
    # (137), not the legacy flat 60.
    assert 137 in sleep_calls, (
        f"expected sleep(137) from StravaRateLimited.retry_after; saw {sleep_calls}"
    )
    assert 60 not in sleep_calls, (
        f"flat sleep(60) detected — retry_after propagation regressed: {sleep_calls}"
    )


def test_run_gps_upgrade_day_cap_aborts(monkeypatch):
    """retry_after > RATELIMIT_DAY_CAP_THRESHOLD_S means the daily cap is
    hit — worker must bail rather than asyncio.sleep for hours."""
    import app.api.integrations_strava as mod

    async def _fake_get_stream(_access_token, _activity_id):
        raise sc.StravaRateLimited(
            retry_after=sc.RATELIMIT_DAY_CAP_THRESHOLD_S + 100,
            endpoint="activity_stream",
        )

    batches = [
        [("a1", "111", "road")],
        [],  # if we DIDN'T bail, this empty batch is what would terminate the loop
    ]
    batch_iter = iter(batches)

    def _fake_next_batch(_user_id, _n):
        return next(batch_iter, [])

    apply_calls = []

    def _fake_apply_batch(job_id, upgrades, upgraded_delta, stop_error):
        apply_calls.append((job_id, list(upgrades), upgraded_delta, stop_error))

    def _fake_update_heat(*args, **kwargs):
        pass

    def _fake_read_progress(_job_id):
        return (0, 1, 1)

    import app.services.notifications as notif_mod
    monkeypatch.setattr(notif_mod, "emit_notification", lambda *a, **k: None)

    sleep_calls: list[float] = []

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)
        # If we ever sleep >= RATELIMIT_DAY_CAP_THRESHOLD_S, the test must fail.
        assert seconds < sc.RATELIMIT_DAY_CAP_THRESHOLD_S, (
            f"slept {seconds}s — day-cap guard didn't fire"
        )

    monkeypatch.setattr(mod, "get_activity_stream", _fake_get_stream)
    monkeypatch.setattr(mod, "_gps_next_batch", _fake_next_batch)
    monkeypatch.setattr(mod, "_gps_apply_batch", _fake_apply_batch)
    import app.services.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "_update_heat_edges", _fake_update_heat)
    monkeypatch.setattr(mod, "_read_job_progress", _fake_read_progress)
    monkeypatch.setattr(mod.asyncio, "sleep", _fake_sleep)

    asyncio.run(mod._run_gps_upgrade("job-1", "user-1", "token"))

    # The bail path persists a `last_error` describing the day-cap.
    daycap_errors = [c for c in apply_calls if c[3] and "daily cap" in c[3]]
    assert daycap_errors, (
        f"day-cap last_error not persisted; apply_calls={apply_calls}"
    )


def teardown_module(module):  # noqa: D401
    """Final safety net — close the shared client if any test left it dangling."""
    with contextlib.suppress(Exception):
        asyncio.run(sc.aclose_http_client())
