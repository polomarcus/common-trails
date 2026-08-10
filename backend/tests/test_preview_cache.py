"""PR-E High #7: 5-min per-user preview count cache.

Walking 25 pages of Strava history on every consent-screen render is a
12s spinner for a 5000-activity beta user. Cache the count so re-renders
are instant.

We test ``cached_count_athlete_activities`` directly. TTL is patched to
keep test runtime short. Real API hits are mocked via the inner
``count_athlete_activities`` function.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.services import strava_client as sc


@pytest.fixture(autouse=True)
def _clear_cache():
    sc._preview_cache_clear()
    yield
    sc._preview_cache_clear()


def test_two_quick_calls_hit_the_cache() -> None:
    """First call paginates Strava; second call inside TTL is a cache hit."""
    hits = {"n": 0}

    async def _count(_tok):
        hits["n"] += 1
        return 1234

    with patch.object(sc, "count_athlete_activities", side_effect=_count):
        v1 = asyncio.run(sc.cached_count_athlete_activities("user-A", "tok"))
        v2 = asyncio.run(sc.cached_count_athlete_activities("user-A", "tok"))
        assert v1 == 1234 == v2
        assert hits["n"] == 1, "second call must NOT re-paginate"


def test_third_call_after_ttl_repaginates() -> None:
    """After TTL expiry, the next call hits Strava again."""
    hits = {"n": 0}

    async def _count(_tok):
        hits["n"] += 1
        return 99

    # Use a 0 TTL so the entry expires before the next call.
    with patch.object(sc, "_PREVIEW_CACHE_TTL_S", 0), \
         patch.object(sc, "count_athlete_activities", side_effect=_count):
        asyncio.run(sc.cached_count_athlete_activities("user-B", "tok"))
        asyncio.run(sc.cached_count_athlete_activities("user-B", "tok"))
        assert hits["n"] == 2, "TTL=0 must re-paginate every call"


def test_cache_is_keyed_per_user() -> None:
    """Cache key = user_id, NOT access_token — token refresh must not invalidate."""
    hits = {"n": 0}

    async def _count(_tok):
        hits["n"] += 1
        return 5

    with patch.object(sc, "count_athlete_activities", side_effect=_count):
        # Same user, different tokens → still cached
        asyncio.run(sc.cached_count_athlete_activities("user-A", "tok-old"))
        asyncio.run(sc.cached_count_athlete_activities("user-A", "tok-refreshed"))
        assert hits["n"] == 1

        # Different user → paginates
        asyncio.run(sc.cached_count_athlete_activities("user-B", "tok-other"))
        assert hits["n"] == 2


def test_cache_stores_none_results() -> None:
    """If the underlying call returns None (rate-limited), the None is
    cached too — otherwise we'd hammer Strava every preview render
    during a rate-limit storm."""
    hits = {"n": 0}

    async def _count(_tok):
        hits["n"] += 1
        return None

    with patch.object(sc, "count_athlete_activities", side_effect=_count):
        v1 = asyncio.run(sc.cached_count_athlete_activities("user-C", "tok"))
        v2 = asyncio.run(sc.cached_count_athlete_activities("user-C", "tok"))
        assert v1 is None and v2 is None
        assert hits["n"] == 1, "None must be cached, not re-fetched on every call"
