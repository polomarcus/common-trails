"""Strava API client — official API only.

LEGAL NOTICE:
- Uses Strava official REST API v3 exclusively.
- Imports personal activity streams (GPS traces) and activity photos only.
- Strava heatmap data is proprietary — import is FORBIDDEN.
- TEST_MODE=true: returns deterministic stub data, no network calls.
"""

import asyncio
import logging
import os
import time

from app.config import TEST_MODE

log = logging.getLogger(__name__)

# Strava REST API v3 base URL. Overridable via env so the June 2027 migration
# (www.strava.com/api/v3 → api-v3.strava.com, per the 2026-06 API Program
# changes) is a single env flip, not a code change. Default stays the current
# URL — do NOT point at the new domain until Strava activates it (2027-06-01).
STRAVA_API_BASE = os.environ.get("STRAVA_API_BASE", "https://www.strava.com/api/v3")

# Pagination + safety caps for count_athlete_activities.
_COUNT_PAGE_SIZE = 200
_COUNT_TIMEOUT_S = 10.0
_COUNT_MAX_PAGES = 200  # 200 * 200 = 40k activities — well above any real account
_COUNT_RETRY_AFTER_FALLBACK_S = 60
_COUNT_MAX_RATELIMIT_RETRIES = 3

# Strava rate-limit fallback when the server omits the Retry-After header.
_RATELIMIT_RETRY_AFTER_FALLBACK_S = 60
# Strava rate-limit window is 15 min. A Retry-After above this strongly
# suggests we hit the daily 1000-request cap and the actual wait is in
# hours — callers should bail rather than thrash the wall.
RATELIMIT_DAY_CAP_THRESHOLD_S = 900


class StravaRateLimited(Exception):
    """Raised on Strava HTTP 429. Carries the parsed Retry-After seconds
    so callers can sleep precisely instead of using a hardcoded fallback.
    """

    def __init__(self, retry_after: int, *, endpoint: str = "") -> None:
        self.retry_after = retry_after
        self.endpoint = endpoint
        super().__init__(
            f"Strava rate-limited on {endpoint or '<endpoint>'}; retry_after={retry_after}s"
        )


# Preview cache — 5 min TTL keyed by user_id. Acceptable on min=0/max=1
# Cloud Run (single instance). For multi-instance scaling, replace with
# a DB-backed `strava_preview_cache` table or a Redis instance.
_PREVIEW_CACHE_TTL_S = 300
_MISS: object = object()
_preview_count_cache: dict[str, tuple[int | None, float]] = {}


def _preview_cache_get(user_id: str):
    """Return the cached count, or `_MISS` sentinel when missing/expired."""
    entry = _preview_count_cache.get(user_id)
    if entry is None:
        return _MISS
    value, expires_at = entry
    if time.monotonic() >= expires_at:
        _preview_count_cache.pop(user_id, None)
        return _MISS
    return value


def _preview_cache_set(user_id: str, value: int | None) -> None:
    _preview_count_cache[user_id] = (value, time.monotonic() + _PREVIEW_CACHE_TTL_S)


def _preview_cache_clear() -> None:
    """Test helper — purge the cache between tests."""
    _preview_count_cache.clear()


# ── Stub data ─────────────────────────────────────────────────────────────────

STUB_PHOTOS = [
    {
        "unique_id": "stub_photo_001",
        "urls": {
            "600": "https://dgtzuqphqg23d.cloudfront.net/stub-600x315.jpg",
            "100": "https://dgtzuqphqg23d.cloudfront.net/stub-100x100.jpg",
        },
        "location": [45.765, 4.836],
        "caption": "Vue du col",
    },
    {
        "unique_id": "stub_photo_002",
        "urls": {
            "600": "https://dgtzuqphqg23d.cloudfront.net/stub2-600x315.jpg",
            "100": "https://dgtzuqphqg23d.cloudfront.net/stub2-100x100.jpg",
        },
        "location": [45.767, 4.838],
        "caption": "",
    },
]

STUB_STREAM = {
    "latlng": {
        "data": [
            [45.764, 4.835],
            [45.765, 4.836],
            [45.766, 4.837],
            [45.767, 4.838],
            [45.768, 4.839],
        ]
    },
    "altitude": {
        "data": [210.0, 245.0, 290.0, 265.0, 230.0]
    },
}


# ── Shared httpx client ───────────────────────────────────────────────────────
#
# Reused across Strava API calls so we don't pay a fresh TLS handshake
# (~50 ms) on each of the 200+ requests a Phase-1 discover makes.
# Lazy-init on first use under an asyncio.Lock to avoid the race where
# two coroutines see _HTTPX_CLIENT is None at startup.

_HTTPX_CLIENT = None
_HTTPX_LOCK: asyncio.Lock | None = None
_HTTPX_DEFAULT_TIMEOUT_S = 30.0


async def get_http_client():
    """Return the process-wide httpx.AsyncClient, creating it on first call."""
    global _HTTPX_CLIENT, _HTTPX_LOCK
    if _HTTPX_CLIENT is not None:
        return _HTTPX_CLIENT
    if _HTTPX_LOCK is None:
        _HTTPX_LOCK = asyncio.Lock()
    async with _HTTPX_LOCK:
        if _HTTPX_CLIENT is None:
            import httpx
            _HTTPX_CLIENT = httpx.AsyncClient(timeout=_HTTPX_DEFAULT_TIMEOUT_S)
    return _HTTPX_CLIENT


async def aclose_http_client() -> None:
    """Close the shared client on app shutdown. Idempotent."""
    global _HTTPX_CLIENT
    if _HTTPX_CLIENT is not None:
        client = _HTTPX_CLIENT
        _HTTPX_CLIENT = None
        try:
            await client.aclose()
        except Exception as exc:
            log.warning("aclose_http_client failed: %s", exc)


def _parse_retry_after(headers, default: int = _RATELIMIT_RETRY_AFTER_FALLBACK_S) -> int:
    """Best-effort parse of a Retry-After header. Strava sends an integer
    number of seconds; we coerce defensively in case of malformed values.
    """
    raw = headers.get("Retry-After") if headers else None
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(0, value)


# ── Client ────────────────────────────────────────────────────────────────────

async def get_activity_stream(access_token: str, activity_id: int) -> dict:
    """Get GPS stream for a single activity. Personal data only.

    Raises:
        StravaRateLimited: on HTTP 429 — carries ``retry_after`` seconds.
        RuntimeError: any other failure (network, 4xx/5xx, JSON parse).
    """
    if TEST_MODE:
        return STUB_STREAM

    client = await get_http_client()
    try:
        resp = await client.get(
            f"{STRAVA_API_BASE}/activities/{activity_id}/streams",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"keys": "latlng,altitude", "key_by_type": "true"},
            timeout=15.0,
        )
        if resp.status_code == 429:
            raise StravaRateLimited(
                _parse_retry_after(resp.headers),
                endpoint="activity_stream",
            )
        resp.raise_for_status()
        return resp.json()
    except StravaRateLimited:
        raise
    except Exception as exc:
        # Don't tag activity_id (PII) but the call site is observable from
        # the stack. 429s are routine and re-raised as RuntimeError("...429...")
        # which _run_gps_upgrade detects by string match — Sentry sees them
        # all anyway, but that's acceptable noise vs. the silent-fail risk.
        import sentry_sdk
        sentry_sdk.set_tag("strava.api_call", "get_activity_stream")
        sentry_sdk.capture_exception(exc)
        raise RuntimeError(f"Strava get_activity_stream failed: {exc}") from exc


async def get_activity_photos(access_token: str, activity_id: int) -> list[dict]:
    """Fetch all photos for a Strava activity. Personal data only.

    Raises:
        StravaRateLimited: on HTTP 429 — carries ``retry_after`` seconds.
        RuntimeError: any other failure.
    """
    if TEST_MODE:
        return STUB_PHOTOS

    client = await get_http_client()
    try:
        resp = await client.get(
            f"{STRAVA_API_BASE}/activities/{activity_id}/photos",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"size": 600, "photo_sources": "true"},
            timeout=15.0,
        )
        if resp.status_code == 429:
            raise StravaRateLimited(
                _parse_retry_after(resp.headers),
                endpoint="activity_photos",
            )
        resp.raise_for_status()
        return resp.json()
    except StravaRateLimited:
        raise
    except Exception as exc:
        import sentry_sdk
        sentry_sdk.set_tag("strava.api_call", "get_activity_photos")
        sentry_sdk.capture_exception(exc)
        raise RuntimeError(f"Strava get_activity_photos failed: {exc}") from exc


async def count_athlete_activities(access_token: str) -> int | None:
    """Count ALL activities on the athlete's account via /athlete/activities pagination.

    Unlike /athletes/{id}/stats (which only exposes ride/run/swim totals), this
    walks the full activity list so hike, walk, AlpineSki, Workout, etc. are
    counted too. Page contents are discarded after counting to keep memory flat.

    Returns the total count, or None if any HTTP error prevents a reliable
    result — caller should fall back to /stats in that case.
    """
    if TEST_MODE:
        return 42

    client = await get_http_client()

    total = 0
    try:
        for page in range(1, _COUNT_MAX_PAGES + 1):
            ratelimit_retries = 0
            while True:
                resp = await client.get(
                    f"{STRAVA_API_BASE}/athlete/activities",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"page": page, "per_page": _COUNT_PAGE_SIZE},
                    timeout=_COUNT_TIMEOUT_S,
                )
                if resp.status_code == 429:
                    if ratelimit_retries >= _COUNT_MAX_RATELIMIT_RETRIES:
                        log.warning(
                            "Strava 429 on page %d, exhausted retries — aborting count",
                            page,
                        )
                        return None
                    retry_after = _parse_retry_after(
                        resp.headers, _COUNT_RETRY_AFTER_FALLBACK_S,
                    )
                    log.warning(
                        "Strava 429 on page %d, waiting %ds (retry %d)",
                        page, retry_after, ratelimit_retries + 1,
                    )
                    await asyncio.sleep(retry_after)
                    ratelimit_retries += 1
                    continue
                if not resp.is_success:
                    log.warning(
                        "Strava /athlete/activities returned %d on page %d",
                        resp.status_code, page,
                    )
                    return None
                break

            page_items = resp.json()
            if not isinstance(page_items, list):
                return None
            count = len(page_items)
            total += count
            # We only need the count — drop the page contents.
            del page_items
            if count < _COUNT_PAGE_SIZE:
                return total
    except Exception as exc:
        import sentry_sdk
        sentry_sdk.set_tag("strava.api_call", "count_athlete_activities")
        sentry_sdk.capture_exception(exc)
        log.warning("count_athlete_activities failed: %s", exc)
        return None

    # Hit the safety cap — return what we have but log it.
    log.warning(
        "count_athlete_activities reached page cap (%d), returning partial total",
        _COUNT_MAX_PAGES,
    )
    return total


async def cached_count_athlete_activities(
    user_id: str, access_token: str,
) -> int | None:
    """`count_athlete_activities` with a per-user 5-min cache.

    Walking 25 pages of Strava history on every consent-screen render
    is a 12 s spinner for a 5000-activity beta user. Cache the count so
    re-renders are instant. TTL chosen to balance "fresh enough" vs
    "stop hammering the API" — activities added during a 5-min window
    just don't appear in the preview, the actual import is still
    accurate.

    Cache is keyed by user_id (not access_token) so token refresh
    doesn't invalidate. Module-level dict is fine on min=0/max=1
    Cloud Run; for multi-instance scaling, swap for a DB-backed table.
    """
    cached = _preview_cache_get(user_id)
    if cached is not _MISS:
        return cached  # type: ignore[return-value]
    value = await count_athlete_activities(access_token)
    _preview_cache_set(user_id, value)
    return value


def stream_to_geojson(stream: dict) -> dict | None:
    """Convert a Strava latlng+altitude stream to GeoJSON LineString.

    Produces 3D coords [lon, lat, elevation_m] when altitude stream is present,
    2D [lon, lat] otherwise.
    """
    latlng_data = stream.get("latlng", {}).get("data", [])
    if not latlng_data:
        return None
    altitude_data = stream.get("altitude", {}).get("data", [])
    if altitude_data and len(altitude_data) == len(latlng_data):
        coords = [[lon, lat, alt] for (lat, lon), alt in zip(latlng_data, altitude_data, strict=True)]
    else:
        # GeoJSON uses [lon, lat]
        coords = [[lon, lat] for lat, lon in latlng_data]
    return {"type": "LineString", "coordinates": coords}
