"""ST-S1.5 regression: outer except in `_run_strava_import` must
report to Sentry even if its own helpers (cursor read, job update,
notification emit) raise.

Pre-PR #357 the four side-effects ran sequentially without isolation,
so a connection-drop inside `_job_read_cursor` would escape, mark the
row stuck in RUNNING forever, AND skip the Sentry capture. PR #357
wraps each side-effect, and (this fix) reorders the sentry block so a
tag-set failure can't preempt the capture itself.

This test pins the contract: when the inner job body raises, AND the
post-mortem helpers raise too, `sentry_sdk.capture_exception` is still
called with the ORIGINAL exception.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _bypass_test_mode():
    # `_run_strava_import` short-circuits in TEST_MODE before the try
    # block we want to exercise. Force the live path; we mock every
    # network/DB call below so nothing real fires.
    with patch("app.api.integrations_strava.TEST_MODE", False):
        yield


def test_outer_except_captures_to_sentry_even_when_post_mortem_helpers_raise():
    """Inner body raises (httpx unavailable) → outer except runs.
    Inside the except, `_job_read_cursor`, `_job_update`, and
    `emit_notification` all raise too. `sentry_sdk.capture_exception`
    must still be called with the original `RuntimeError`.
    """
    import app.api.integrations_strava as mod

    inner_exc = RuntimeError("Phase 1 boom")

    cursor_calls = {"n": 0}

    def fake_read_cursor(_job_id: str) -> dict:
        cursor_calls["n"] += 1
        if cursor_calls["n"] == 1:
            return {"phase": "phase1", "contribute_heatmap": False}
        raise RuntimeError("cursor connection drop")

    def fake_job_update(*_args, **_kwargs):
        raise RuntimeError("job update connection drop")

    def fake_emit(*_args, **_kwargs):
        raise RuntimeError("notification fanout failed")

    class _BoomClient:
        async def __aenter__(self):
            raise inner_exc

        async def __aexit__(self, *_a):
            return False

    fake_httpx = MagicMock()
    fake_httpx.AsyncClient = lambda: _BoomClient()

    with patch.object(mod, "_job_read_cursor", side_effect=fake_read_cursor), \
         patch.object(mod, "_job_exists", return_value=True), \
         patch.object(mod, "_job_update", side_effect=fake_job_update), \
         patch.object(mod, "sentry_sdk") as fake_sentry, \
         patch.dict("sys.modules", {"httpx": fake_httpx}), \
         patch("app.services.notifications.emit_notification", side_effect=fake_emit):
        # Must NOT raise — the outer except absorbs everything.
        asyncio.run(mod._run_strava_import("job-xyz", "user-xyz", "tok"))

        assert fake_sentry.capture_exception.call_count == 1, (
            f"capture_exception must fire exactly once even when post-mortem "
            f"helpers raise; got {fake_sentry.capture_exception.call_count}"
        )
        captured_exc = fake_sentry.capture_exception.call_args.args[0]
        assert captured_exc is inner_exc, (
            f"Sentry must capture the ORIGINAL exception, not a secondary "
            f"failure; got {captured_exc!r}"
        )


def test_outer_except_captures_sentry_first_then_tags():
    """Tag-set failure must not preempt `capture_exception`. Verifies
    the ordering fix from PR #357 review: capture call comes before
    any set_tag call so a tag SDK error can't lose the stack.
    """
    import app.api.integrations_strava as mod

    inner_exc = RuntimeError("Phase 1 boom")
    call_order: list[str] = []

    def fake_capture(_exc):
        call_order.append("capture")

    def fake_set_tag(key, _val):
        call_order.append(f"set_tag:{key}")
        # Simulate sentry-sdk raising on tag set (e.g. hub not initialized)
        raise RuntimeError("sentry tag failure")

    class _BoomClient:
        async def __aenter__(self):
            raise inner_exc

        async def __aexit__(self, *_a):
            return False

    fake_httpx = MagicMock()
    fake_httpx.AsyncClient = lambda: _BoomClient()

    with patch.object(mod, "_job_read_cursor", return_value={"phase": "phase1"}), \
         patch.object(mod, "_job_exists", return_value=True), \
         patch.object(mod, "_job_update"), \
         patch.object(mod, "sentry_sdk") as fake_sentry, \
         patch.dict("sys.modules", {"httpx": fake_httpx}), \
         patch("app.services.notifications.emit_notification"):
        fake_sentry.capture_exception.side_effect = fake_capture
        fake_sentry.set_tag.side_effect = fake_set_tag
        asyncio.run(mod._run_strava_import("job-xyz", "user-xyz", "tok"))

        assert call_order, "Sentry calls never reached"
        assert call_order[0] == "capture", (
            f"capture_exception must be invoked BEFORE set_tag so a tag failure "
            f"cannot skip the capture; call order was {call_order}"
        )
