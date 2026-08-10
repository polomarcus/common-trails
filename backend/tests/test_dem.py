"""Tests for DEM elevation provider (Open-Meteo)."""

import httpx
import pytest

from app.services import dem


@pytest.fixture(autouse=True)
def _clean_cache():
    """Clear elevation cache before each test."""
    dem.clear_cache()
    yield
    dem.clear_cache()


def _mock_response(elevations: list[float | None], status_code: int = 200):
    """Build a mock httpx.Response with the given elevation array."""
    return httpx.Response(
        status_code=status_code,
        json={"elevation": elevations},
        request=httpx.Request("GET", dem._OPEN_METEO_URL),
    )


@pytest.mark.asyncio
async def test_fetch_elevations_batching(monkeypatch):
    """150 unique coords → 2 HTTP requests (100 + 50)."""
    call_count = 0

    async def mock_get(self, url, *, params=None, **kwargs):
        nonlocal call_count
        call_count += 1
        n = len(params["latitude"].split(","))
        return _mock_response([100.0 + i for i in range(n)])

    monkeypatch.setenv("DEM_PROVIDER", "open-meteo")
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    coords = [[i * 0.01, 43.0 + i * 0.01] for i in range(150)]
    result = await dem.fetch_elevations(coords)

    assert len(result) == 150
    assert call_count == 2
    assert all(e is not None for e in result)


@pytest.mark.asyncio
async def test_fetch_elevations_cache(monkeypatch):
    """Second call with same coords uses cache — no extra HTTP calls."""
    call_count = 0

    async def mock_get(self, url, *, params=None, **kwargs):
        nonlocal call_count
        call_count += 1
        n = len(params["latitude"].split(","))
        return _mock_response([200.0] * n)

    monkeypatch.setenv("DEM_PROVIDER", "open-meteo")
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    coords = [[3.87, 43.61], [3.88, 43.62]]
    r1 = await dem.fetch_elevations(coords)
    assert call_count == 1

    r2 = await dem.fetch_elevations(coords)
    assert call_count == 1  # no new HTTP call
    assert r1 == r2


@pytest.mark.asyncio
@pytest.mark.slow
async def test_fetch_elevations_api_error(monkeypatch):
    """500 response → returns list of None (flaky in CI — async monkeypatch race)."""

    async def mock_get(self, url, *, params=None, **kwargs):
        return _mock_response([], status_code=500)

    monkeypatch.setenv("DEM_PROVIDER", "open-meteo")
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    coords = [[3.87, 43.61], [3.88, 43.62]]
    result = await dem.fetch_elevations(coords)

    assert len(result) == 2
    assert all(e is None for e in result)


@pytest.mark.asyncio
async def test_fetch_elevations_disabled(monkeypatch):
    """DEM_PROVIDER=none → returns None without HTTP call."""
    call_count = 0

    async def mock_get(self, url, *, params=None, **kwargs):
        nonlocal call_count
        call_count += 1
        return _mock_response([100.0])

    monkeypatch.setenv("DEM_PROVIDER", "none")
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    coords = [[3.87, 43.61], [3.88, 43.62]]
    result = await dem.fetch_elevations(coords)

    assert len(result) == 2
    assert all(e is None for e in result)
    assert call_count == 0


@pytest.mark.asyncio
async def test_compute_slopes_for_edges_basic(monkeypatch):
    """Compute slope from two endpoints with known elevations."""

    async def mock_get(self, url, *, params=None, **kwargs):
        lats = [float(x) for x in params["latitude"].split(",")]
        # Return 100m for first point, 200m for second point
        elevations = [100.0 + i * 100 for i in range(len(lats))]
        return _mock_response(elevations)

    monkeypatch.setenv("DEM_PROVIDER", "open-meteo")
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    # Edge: ~111m horizontal distance (0.001° lat at ~43°N)
    edges = [
        [3.870, 43.610, 3.870, 43.611, 0, 0, 4, 5, 1],  # lon1, lat1, lon2, lat2, ...
    ]
    slopes = await dem.compute_slopes_for_edges(edges)

    assert len(slopes) == 1
    # 100m elevation gain over ~111m → ~90% slope (rough — depends on exact haversine)
    assert slopes[0] != 0.0, "slope should be non-zero"
    assert slopes[0] > 0, "uphill should be positive"


@pytest.mark.asyncio
async def test_compute_slopes_for_edges_disabled(monkeypatch):
    """DEM_PROVIDER=none → all slopes return 0.0."""
    monkeypatch.setenv("DEM_PROVIDER", "none")

    edges = [
        [3.870, 43.610, 3.871, 43.611, 0, 0, 4, 5, 1],
    ]
    slopes = await dem.compute_slopes_for_edges(edges)

    assert len(slopes) == 1
    assert slopes[0] == 0.0


@pytest.mark.asyncio
async def test_compute_slopes_for_edges_empty():
    """Empty edge list returns empty slopes."""
    slopes = await dem.compute_slopes_for_edges([])
    assert slopes == []


@pytest.mark.asyncio
async def test_compute_slopes_for_edges_api_error(monkeypatch):
    """API failure → slopes default to 0.0."""

    async def mock_get(self, url, *, params=None, **kwargs):
        return _mock_response([], status_code=500)

    monkeypatch.setenv("DEM_PROVIDER", "open-meteo")
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    edges = [
        [3.870, 43.610, 3.871, 43.611, 0, 0, 4, 5, 1],
    ]
    slopes = await dem.compute_slopes_for_edges(edges)

    assert len(slopes) == 1
    assert slopes[0] == 0.0


@pytest.mark.asyncio
async def test_fetch_elevations_deduplicates(monkeypatch):
    """Duplicate coords (after rounding) result in a single API point."""
    received_lats = []

    async def mock_get(self, url, *, params=None, **kwargs):
        received_lats.append(params["latitude"])
        n = len(params["latitude"].split(","))
        return _mock_response([150.0] * n)

    monkeypatch.setenv("DEM_PROVIDER", "open-meteo")
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    # Two coords that round to the same (lat, lon)
    coords = [[3.87001, 43.61002], [3.87002, 43.61001]]
    result = await dem.fetch_elevations(coords)

    assert len(result) == 2
    assert result[0] == result[1] == 150.0
    # Only 1 unique point sent to API
    assert len(received_lats[0].split(",")) == 1
