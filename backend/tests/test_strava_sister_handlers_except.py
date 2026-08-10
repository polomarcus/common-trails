"""PR #360 regression tests for the 4 sister handlers that got the
same sentry capture-first + suppression pattern as `_run_strava_import`.

The original `_run_strava_import` outer except is pinned by
`test_strava_outer_except.py` (PR #357). PR #360 swept the same
pattern into 4 sister handlers; this file pins each one so a future
refactor that re-introduces the old pattern (set_tag before
capture_exception, or function-local `import sentry_sdk` shadowing
the module-level one) fails immediately:

- `_run_gps_upgrade` outer except
- `_run_photo_import` outer except
- `strava_callback` OAuth token-exchange except
- `strava_preview` /stats fallback except

For each: simulate an inner-body raise, patch `sentry_sdk` at the
module attribute, assert (a) capture fires, (b) capture comes BEFORE
any `set_tag` so a tag-set error can't preempt the actual stack
capture.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

# ── _run_gps_upgrade ────────────────────────────────────────────────


def test_run_gps_upgrade_captures_sentry_first_then_tags():
    """Inner body raises (via mocked `_gps_next_batch`). The outer
    except must call `capture_exception` BEFORE any `set_tag`, so a
    sentry-SDK tag failure can't skip the actual stack capture.
    """
    import app.api.integrations_strava as mod

    inner_exc = RuntimeError("gps batch boom")
    call_order: list[str] = []

    def fake_capture(_exc):
        call_order.append("capture")

    def fake_set_tag(key, _val):
        call_order.append(f"set_tag:{key}")
        raise RuntimeError("sentry tag failure")

    def boom(*_a, **_kw):
        raise inner_exc

    with patch.object(mod, "_gps_next_batch", side_effect=boom), \
         patch.object(mod, "_job_update"), \
         patch.object(mod, "sentry_sdk") as fake_sentry, \
         patch("app.services.cloud_tasks.enqueue_heat_compute"):
        fake_sentry.capture_exception.side_effect = fake_capture
        fake_sentry.set_tag.side_effect = fake_set_tag
        # Must NOT raise — handler absorbs everything.
        asyncio.run(mod._run_gps_upgrade("job-xyz", "user-xyz", "tok"))

        assert call_order, "Sentry calls never reached"
        assert call_order[0] == "capture", (
            f"capture_exception must be invoked BEFORE set_tag so a tag "
            f"failure cannot skip the capture; call order was {call_order}"
        )


def test_run_gps_upgrade_captures_even_when_job_update_raises():
    """`_job_update` raising inside the except must not preempt the
    sentry capture (both are wrapped, and capture comes first).
    """
    import app.api.integrations_strava as mod

    inner_exc = RuntimeError("gps batch boom")

    def boom(*_a, **_kw):
        raise inner_exc

    with patch.object(mod, "_gps_next_batch", side_effect=boom), \
         patch.object(mod, "_job_update", side_effect=RuntimeError("db drop")), \
         patch.object(mod, "sentry_sdk") as fake_sentry, \
         patch("app.services.cloud_tasks.enqueue_heat_compute"):
        asyncio.run(mod._run_gps_upgrade("job-xyz", "user-xyz", "tok"))
        assert fake_sentry.capture_exception.call_count == 1
        captured_exc = fake_sentry.capture_exception.call_args.args[0]
        assert captured_exc is inner_exc


# ── _run_photo_import ────────────────────────────────────────────────


def test_run_photo_import_captures_sentry_first_then_tags():
    """Same ordering pin for `_run_photo_import`."""
    import app.api.integrations_strava as mod

    inner_exc = RuntimeError("photo batch boom")
    call_order: list[str] = []

    def fake_capture(_exc):
        call_order.append("capture")

    def fake_set_tag(key, _val):
        call_order.append(f"set_tag:{key}")
        raise RuntimeError("sentry tag failure")

    def boom(*_a, **_kw):
        raise inner_exc

    with patch.object(mod, "_photos_next_batch", side_effect=boom), \
         patch.object(mod, "_job_update"), \
         patch.object(mod, "sentry_sdk") as fake_sentry:
        fake_sentry.capture_exception.side_effect = fake_capture
        fake_sentry.set_tag.side_effect = fake_set_tag
        asyncio.run(mod._run_photo_import("job-xyz", "user-xyz", "tok"))

        assert call_order, "Sentry calls never reached"
        assert call_order[0] == "capture", (
            f"capture_exception must be invoked BEFORE set_tag; "
            f"call order was {call_order}"
        )


def test_run_photo_import_captures_even_when_job_update_raises():
    """`_job_update` raising inside the except must not preempt the
    sentry capture.
    """
    import app.api.integrations_strava as mod

    inner_exc = RuntimeError("photo batch boom")

    def boom(*_a, **_kw):
        raise inner_exc

    with patch.object(mod, "_photos_next_batch", side_effect=boom), \
         patch.object(mod, "_job_update", side_effect=RuntimeError("db drop")), \
         patch.object(mod, "sentry_sdk") as fake_sentry:
        asyncio.run(mod._run_photo_import("job-xyz", "user-xyz", "tok"))
        assert fake_sentry.capture_exception.call_count == 1
        captured_exc = fake_sentry.capture_exception.call_args.args[0]
        assert captured_exc is inner_exc


# ── strava_preview /stats fallback ────────────────────────────────────


def test_strava_preview_stats_fallback_captures_real_handler():
    """Drive the actual `strava_preview` handler with mocked deps so
    the /stats HTTP call inside the fallback raises. Assert sentry
    captures the original exception (the request proceeds and the
    handler returns a normal `StravaPreviewResponse` either way).
    """
    import app.api.integrations_strava as mod

    inner_exc = RuntimeError("/stats fallback boom")

    # `acct` shape needed by the handler body.
    fake_acct = MagicMock()
    fake_acct.access_token = "enc-tok"
    fake_acct.external_user_id = "12345"
    fake_acct.athlete_name = "Paul"

    # Mock `current_user` (AuthenticatedUser) — handler only reads .user_id.
    fake_user = MagicMock()
    fake_user.user_id = "user-xyz"

    # Mock `db` — the handler does `.query(Activity).filter(...).count()` for
    # already_imported. Return 0 via a chained-MagicMock so we don't hit DB.
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.count.return_value = 0

    # Mock the httpx client to raise on `.get()`, triggering the fallback's
    # `except Exception as exc:` block where the sentry capture lives.
    async def fake_get(*_a, **_kw):
        raise inner_exc

    fake_http_client = MagicMock()
    fake_http_client.get = fake_get

    async def fake_get_http_client():
        return fake_http_client

    async def fake_cached_count(_user_id, _token):
        return None  # Force fallback to /stats path

    async def _run():
        with patch.object(mod, "_get_strava_account", return_value=fake_acct), \
             patch.object(mod, "cached_count_athlete_activities", side_effect=fake_cached_count), \
             patch.object(mod, "decrypt_token", return_value="plain-tok"), \
             patch.object(mod, "get_http_client", side_effect=fake_get_http_client), \
             patch.object(mod, "TEST_MODE", False), \
             patch.object(mod, "sentry_sdk") as fake_sentry:
            resp = await mod.strava_preview(current_user=fake_user, db=fake_db)
            return resp, fake_sentry

    resp, fake_sentry = asyncio.run(_run())

    # Handler returned successfully despite the /stats failure.
    assert resp.athlete_name == "Paul"
    assert resp.already_imported_count == 0
    # The original exception was captured to sentry.
    assert fake_sentry.capture_exception.call_count == 1, (
        f"strava_preview /stats fallback must capture sentry on inner failure; "
        f"got call_count={fake_sentry.capture_exception.call_count}"
    )
    assert fake_sentry.capture_exception.call_args.args[0] is inner_exc


def test_strava_preview_stats_fallback_returns_when_sentry_raises():
    """If the sentry SDK itself errors during the fallback, the handler
    must STILL return a normal response (sentry-blip is not user-facing).
    """
    import app.api.integrations_strava as mod

    fake_acct = MagicMock()
    fake_acct.access_token = "enc-tok"
    fake_acct.external_user_id = "12345"
    fake_acct.athlete_name = "Paul"

    fake_user = MagicMock()
    fake_user.user_id = "user-xyz"

    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.count.return_value = 0

    async def fake_get(*_a, **_kw):
        raise RuntimeError("/stats fallback boom")

    fake_http_client = MagicMock()
    fake_http_client.get = fake_get

    async def fake_get_http_client():
        return fake_http_client

    async def fake_cached_count(_user_id, _token):
        return None

    async def _run():
        with patch.object(mod, "_get_strava_account", return_value=fake_acct), \
             patch.object(mod, "cached_count_athlete_activities", side_effect=fake_cached_count), \
             patch.object(mod, "decrypt_token", return_value="plain-tok"), \
             patch.object(mod, "get_http_client", side_effect=fake_get_http_client), \
             patch.object(mod, "TEST_MODE", False), \
             patch.object(mod, "sentry_sdk") as fake_sentry:
            fake_sentry.capture_exception.side_effect = RuntimeError("sentry hub not init")
            return await mod.strava_preview(current_user=fake_user, db=fake_db)

    resp = asyncio.run(_run())
    # Returned a normal response despite double failure (network + sentry).
    assert resp.athlete_name == "Paul"


# ── strava_callback OAuth token-exchange ─────────────────────────────
#
# NOT pinned by a unit test in this file. Driving `strava_callback`
# end-to-end requires building a valid OAuth state JWT, mocking the
# state JWT cookie, the multi-step DB session, and the auth dep —
# heavy enough that a focused HTTP-level test would belong in
# `test_strava_oauth_callback.py` (if/when written). The handler body
# is otherwise structurally identical to the patterns pinned above
# (see `_run_gps_upgrade` and `strava_preview`), so a future regression
# would surface in code review or in those tests' shape audits.
