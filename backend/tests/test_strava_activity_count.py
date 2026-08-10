"""Tests for the accurate Strava activity counter (paginates /athlete/activities).

Covers the helper directly (mocks httpx) and the /integrations/strava/preview
endpoint fallback semantics. The whole reason for this helper is that the
legacy /athletes/{id}/stats endpoint only exposes ride+run+swim totals — hike,
walk, AlpineSki, Workout, BackcountrySki, etc. are missing. Paginating
/athlete/activities counts every sport type.
"""
from __future__ import annotations

import httpx
import pytest

from app.services import strava_client


def _ok_page(items: list[dict]) -> httpx.Response:
    return httpx.Response(
        status_code=200,
        json=items,
        request=httpx.Request("GET", f"{strava_client.STRAVA_API_BASE}/athlete/activities"),
    )


def _err_response(status_code: int, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json={},
        headers=headers or {},
        request=httpx.Request("GET", f"{strava_client.STRAVA_API_BASE}/athlete/activities"),
    )


@pytest.mark.asyncio
async def test_count_two_full_pages_plus_partial(monkeypatch):
    """Full page (200) + full page (200) + partial page (56) → 456 total."""
    monkeypatch.setattr(strava_client, "TEST_MODE", False)

    page_count = strava_client._COUNT_PAGE_SIZE
    pages = [
        [{"id": i} for i in range(page_count)],
        [{"id": page_count + i} for i in range(page_count)],
        [{"id": 2 * page_count + i} for i in range(56)],
    ]
    calls: list[int] = []

    async def mock_get(self, url, *, params=None, headers=None, **kwargs):
        page = params["page"]
        calls.append(page)
        assert params["per_page"] == page_count
        assert headers["Authorization"].startswith("Bearer ")
        return _ok_page(pages[page - 1])

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    total = await strava_client.count_athlete_activities("tok")

    assert total == 2 * page_count + 56
    assert calls == [1, 2, 3]


@pytest.mark.asyncio
async def test_count_single_partial_page(monkeypatch):
    """First page has < per_page items → return immediately, no second call."""
    monkeypatch.setattr(strava_client, "TEST_MODE", False)

    calls: list[int] = []

    async def mock_get(self, url, *, params=None, headers=None, **kwargs):
        calls.append(params["page"])
        return _ok_page([{"id": i} for i in range(42)])

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    total = await strava_client.count_athlete_activities("tok")

    assert total == 42
    assert calls == [1]


@pytest.mark.asyncio
async def test_count_empty_account(monkeypatch):
    """Empty page on first call → 0."""
    monkeypatch.setattr(strava_client, "TEST_MODE", False)

    async def mock_get(self, url, *, params=None, headers=None, **kwargs):
        return _ok_page([])

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    total = await strava_client.count_athlete_activities("tok")
    assert total == 0


@pytest.mark.asyncio
async def test_count_full_then_empty_page(monkeypatch):
    """200 + 0 → 200 (the count is exactly per_page, signal via empty next page)."""
    monkeypatch.setattr(strava_client, "TEST_MODE", False)

    page_count = strava_client._COUNT_PAGE_SIZE
    pages = [[{"id": i} for i in range(page_count)], []]
    calls: list[int] = []

    async def mock_get(self, url, *, params=None, headers=None, **kwargs):
        page = params["page"]
        calls.append(page)
        return _ok_page(pages[page - 1])

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    total = await strava_client.count_athlete_activities("tok")

    assert total == page_count
    assert calls == [1, 2]


@pytest.mark.asyncio
async def test_count_retries_429_then_succeeds(monkeypatch):
    """429 with Retry-After=0 → sleep, retry, succeed."""
    monkeypatch.setattr(strava_client, "TEST_MODE", False)

    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(strava_client.asyncio, "sleep", fake_sleep)

    call = {"n": 0}

    async def mock_get(self, url, *, params=None, headers=None, **kwargs):
        call["n"] += 1
        if call["n"] == 1:
            return _err_response(429, headers={"Retry-After": "0"})
        return _ok_page([{"id": 1}])

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    total = await strava_client.count_athlete_activities("tok")

    assert total == 1
    assert sleeps == [0]


@pytest.mark.asyncio
async def test_count_429_exhausts_retries(monkeypatch):
    """Persistent 429 → returns None so caller falls back to /stats."""
    monkeypatch.setattr(strava_client, "TEST_MODE", False)

    async def fake_sleep(d):
        return None

    monkeypatch.setattr(strava_client.asyncio, "sleep", fake_sleep)

    async def mock_get(self, url, *, params=None, headers=None, **kwargs):
        return _err_response(429, headers={"Retry-After": "0"})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    total = await strava_client.count_athlete_activities("tok")
    assert total is None


@pytest.mark.asyncio
async def test_count_500_returns_none(monkeypatch):
    """5xx mid-pagination → return None (caller falls back to /stats)."""
    monkeypatch.setattr(strava_client, "TEST_MODE", False)

    async def mock_get(self, url, *, params=None, headers=None, **kwargs):
        return _err_response(500)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    total = await strava_client.count_athlete_activities("tok")
    assert total is None


@pytest.mark.asyncio
async def test_count_timeout_returns_none(monkeypatch):
    """httpx.ReadTimeout mid-pagination → return None, no exception leaks."""
    monkeypatch.setattr(strava_client, "TEST_MODE", False)

    async def mock_get(self, url, *, params=None, headers=None, **kwargs):
        raise httpx.ReadTimeout("hung")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    total = await strava_client.count_athlete_activities("tok")
    assert total is None


@pytest.mark.asyncio
async def test_count_non_list_response_returns_none(monkeypatch):
    """Strava returns a dict (e.g. error envelope) → None, no crash."""
    monkeypatch.setattr(strava_client, "TEST_MODE", False)

    async def mock_get(self, url, *, params=None, headers=None, **kwargs):
        return httpx.Response(
            status_code=200,
            json={"errors": [{"resource": "Activity"}]},
            request=httpx.Request(
                "GET", f"{strava_client.STRAVA_API_BASE}/athlete/activities"
            ),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    total = await strava_client.count_athlete_activities("tok")
    assert total is None


@pytest.mark.asyncio
async def test_count_test_mode_returns_stub(monkeypatch):
    """TEST_MODE → stub 42, no HTTP calls."""
    monkeypatch.setattr(strava_client, "TEST_MODE", True)

    async def fail_get(*args, **kwargs):
        raise AssertionError("must not call HTTP in TEST_MODE")

    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)

    total = await strava_client.count_athlete_activities("tok")
    assert total == 42
