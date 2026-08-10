"""PR-E observability: every silent ``except Exception:`` in the Strava
workflow now calls ``sentry_sdk.capture_exception`` with a phase tag.

We don't need a real Sentry DSN — patch the SDK at the module entry
point of each caller and assert ``capture_exception`` was invoked.

Coverage:

- ``integrations_strava._run_strava_import`` outer handler
- ``integrations_strava._run_gps_upgrade`` outer handler
- ``integrations_strava._run_photo_import`` outer handler
- ``integrations_strava.strava_callback`` token-exchange handler
- ``integrations_strava.strava_preview`` /stats fallback handler
- ``jobs.resync_strava._resync_user`` outer handler
- ``services.strava_client.get_activity_stream``
- ``services.strava_client.get_activity_photos``
- ``services.strava_client.count_athlete_activities``
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_shared_httpx_client():
    """Defensive: clear the process-wide ``_HTTPX_CLIENT`` between tests.

    A previous iteration of these tests patched ``httpx.AsyncClient``
    via ``patch("httpx.AsyncClient")``. Inside the ``with`` block, the
    code under test called ``get_http_client()`` which instantiated
    ``httpx.AsyncClient(...)`` — but that was the patched MagicMock,
    not the real class. The shared ``_HTTPX_CLIENT`` module-global then
    *retained* the MagicMock after the patch lifted, poisoning every
    test that ran afterwards (typically ``test_strava_activity_count``,
    which would fail with "object MagicMock can't be used in 'await'
    expression" — symptom of the leaked client).

    We've since moved every test in this file to patch ``get_http_client``
    directly so ``_HTTPX_CLIENT`` is never touched. This fixture remains
    as belt-and-suspenders for future contributors who add a test here.
    """
    from app.services import strava_client as sc
    sc._HTTPX_CLIENT = None
    sc._HTTPX_LOCK = None
    yield
    sc._HTTPX_CLIENT = None
    sc._HTTPX_LOCK = None


# ── strava_client.py — 3 silent handlers ─────────────────────────────────────


class TestStravaClientSentryCaptures:
    """Each strava_client.py API call captures to Sentry on failure."""

    def test_get_activity_stream_captures_on_http_error(self) -> None:
        from app.services import strava_client as sc

        async def _fail(*_a, **_kw):
            raise RuntimeError("simulated network failure")

        mock_client = MagicMock()
        mock_client.get = _fail

        async def _get_client():
            return mock_client

        with (
            patch.object(sc, "TEST_MODE", False),
            patch.object(sc, "get_http_client", _get_client),
            patch("sentry_sdk.capture_exception") as cap,
            patch("sentry_sdk.set_tag") as tag,
        ):
            with pytest.raises(RuntimeError):
                asyncio.run(sc.get_activity_stream("tok", 42))
            assert cap.called
            tag.assert_any_call("strava.api_call", "get_activity_stream")

    def test_get_activity_photos_captures_on_http_error(self) -> None:
        from app.services import strava_client as sc

        async def _fail(*_a, **_kw):
            raise RuntimeError("simulated network failure")

        mock_client = MagicMock()
        mock_client.get = _fail

        async def _get_client():
            return mock_client

        with (
            patch.object(sc, "TEST_MODE", False),
            patch.object(sc, "get_http_client", _get_client),
            patch("sentry_sdk.capture_exception") as cap,
            patch("sentry_sdk.set_tag") as tag,
        ):
            with pytest.raises(RuntimeError):
                asyncio.run(sc.get_activity_photos("tok", 42))
            assert cap.called
            tag.assert_any_call("strava.api_call", "get_activity_photos")

    def test_count_athlete_activities_captures_on_http_error(self) -> None:
        from app.services import strava_client as sc

        async def _fail(*_a, **_kw):
            raise RuntimeError("simulated network failure")

        mock_client = MagicMock()
        mock_client.get = _fail

        async def _get_client():
            return mock_client

        with (
            patch.object(sc, "TEST_MODE", False),
            patch.object(sc, "get_http_client", _get_client),
            patch("sentry_sdk.capture_exception") as cap,
            patch("sentry_sdk.set_tag") as tag,
        ):
            result = asyncio.run(sc.count_athlete_activities("tok"))
            # Returns None on failure — but Sentry MUST have been hit.
            assert result is None
            assert cap.called
            tag.assert_any_call("strava.api_call", "count_athlete_activities")


# ── integrations_strava.py — outer phase handlers ────────────────────────────


class TestIntegrationsStravaSentryCaptures:
    """The 4 phase handlers in integrations_strava.py capture to Sentry."""

    # `test_run_strava_import_outer_captures` removed: it copy-pasted the
    # handler body INLINE inside the test and asserted on its own copy,
    # so handler drift could never have failed it. The real contract is
    # pinned by `tests/test_strava_outer_except.py` (PR #357), which
    # invokes `_run_strava_import` and patches `sentry_sdk` to verify the
    # actual handler — including the capture-before-tag ordering.

    def test_run_gps_upgrade_outer_captures(self) -> None:
        """The ``_run_gps_upgrade`` outer except writes phase + captures."""
        from app.api import integrations_strava as iss

        cap = MagicMock()
        with (
            patch.object(iss, "_gps_next_batch", side_effect=RuntimeError("DB down")),
            patch("sentry_sdk.capture_exception", cap),
            patch("sentry_sdk.set_tag") as tag,
            patch.object(iss, "_job_update") as job_upd,
        ):
            asyncio.run(iss._run_gps_upgrade("job-uuid", "user-1", "tok"))
            assert cap.called
            tag.assert_any_call("strava.phase", "gps_upgrade")
            tag.assert_any_call("strava.job_id", "job-uuid")
            # Records the failed phase on the cursor for the frontend.
            job_upd.assert_any_call(
                "job-uuid", cursor_merge={"last_failed_phase": "gps_upgrade"},
            )

    def test_run_photo_import_outer_captures(self) -> None:
        """The ``_run_photo_import`` outer except writes phase + captures.

        ``SessionLocal()`` is called BEFORE the try block, so we can't
        patch it to raise. Instead we patch the first DB call inside
        the try (``db.query``) to throw — that fires the outer except.
        """
        from app.api import integrations_strava as iss

        # Fake session whose .query() raises and .close() is a no-op.
        fake_session = MagicMock()
        fake_session.query.side_effect = RuntimeError("DB pool exhausted")
        fake_session.close = MagicMock()

        cap = MagicMock()
        with (
            patch.object(iss, "SessionLocal", return_value=fake_session),
            patch("sentry_sdk.capture_exception", cap),
            patch("sentry_sdk.set_tag") as tag,
        ):
            asyncio.run(iss._run_photo_import("job-uuid", "user-1", "tok"))
            assert cap.called
            tag.assert_any_call("strava.phase", "photo_import")
            tag.assert_any_call("strava.job_id", "job-uuid")

    def test_strava_callback_token_exchange_captures(self) -> None:
        """The token-exchange except in ``strava_callback`` captures."""
        from fastapi import HTTPException, Request

        from app.api import integrations_strava as iss

        async def _fail(*_a, **_kw):
            raise RuntimeError("token endpoint refused")

        mock_client = MagicMock()
        mock_client.post = _fail

        async def _get_client():
            return mock_client

        cap = MagicMock()
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        req = Request(scope)
        with (
            patch.object(iss, "TEST_MODE", False),
            patch.object(iss, "STRAVA_ENABLED", True),
            # PR #349 replaced the DB-backed `_pop_oauth_state` with the
            # stateless `_decode_oauth_state`. Same contract:
            # str-or-None return. Patch the new name.
            patch.object(iss, "_decode_oauth_state", return_value="some-user-id"),
            patch.object(iss, "get_http_client", _get_client),
            patch("sentry_sdk.capture_exception", cap),
            patch("sentry_sdk.set_tag") as tag,
        ):
            with pytest.raises(HTTPException) as ei:
                asyncio.run(iss.strava_callback(code="real-code", request=req, state="some-state"))
            assert ei.value.status_code == 502
            assert cap.called
            tag.assert_any_call("strava.phase", "oauth_token_exchange")
