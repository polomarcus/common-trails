"""Security/robustness coverage for Strava 429 handling in strava_client.

Drives the REAL ``get_activity_stream`` and ``get_activity_photos`` from
``app.services.strava_client``. On an HTTP 429 these MUST raise
``StravaRateLimited`` carrying the ``retry_after`` parsed from the
``Retry-After`` header — NOT a generic RuntimeError, and NOT a silent retry
loop that would compound Strava's app-global 100/15min limit.

Both functions short-circuit to a stub in TEST_MODE, so each test monkeypatches
TEST_MODE=false (on the module attribute the functions actually read) to drive
the real httpx path. The shared client is mocked to return a controlled 429.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

import app.services.strava_client as sc
from app.services.strava_client import StravaRateLimited


def _resp_429(retry_after: str | None) -> httpx.Response:
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return httpx.Response(
        429,
        headers=headers,
        json={"message": "Rate Limit Exceeded"},
        request=httpx.Request("GET", "https://www.strava.com/api/v3/activities/1/streams"),
    )


@pytest.mark.asyncio
async def test_get_activity_stream_429_raises_rate_limited(monkeypatch):
    monkeypatch.setattr(sc, "TEST_MODE", False, raising=False)

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=_resp_429("57"))
    with (
        patch.object(sc, "get_http_client", AsyncMock(return_value=fake_client)),
        pytest.raises(StravaRateLimited) as ei,
    ):
        await sc.get_activity_stream("token-xyz", 12345)

    assert ei.value.retry_after == 57, "retry_after must be parsed from Retry-After header"
    assert ei.value.endpoint == "activity_stream"


@pytest.mark.asyncio
async def test_get_activity_photos_429_raises_rate_limited(monkeypatch):
    monkeypatch.setattr(sc, "TEST_MODE", False, raising=False)

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=_resp_429("120"))
    with (
        patch.object(sc, "get_http_client", AsyncMock(return_value=fake_client)),
        pytest.raises(StravaRateLimited) as ei,
    ):
        await sc.get_activity_photos("token-xyz", 67890)

    assert ei.value.retry_after == 120
    assert ei.value.endpoint == "activity_photos"


@pytest.mark.asyncio
async def test_429_without_retry_after_uses_fallback(monkeypatch):
    """Missing Retry-After header → still StravaRateLimited, with the module's
    fallback seconds (not a crash, not retry_after=None)."""
    monkeypatch.setattr(sc, "TEST_MODE", False, raising=False)

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=_resp_429(None))
    with (
        patch.object(sc, "get_http_client", AsyncMock(return_value=fake_client)),
        pytest.raises(StravaRateLimited) as ei,
    ):
        await sc.get_activity_stream("token-xyz", 1)

    assert ei.value.retry_after == sc._RATELIMIT_RETRY_AFTER_FALLBACK_S
    assert isinstance(ei.value.retry_after, int)
