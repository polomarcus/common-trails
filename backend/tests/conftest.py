"""Pytest configuration and shared fixtures."""
import os

import pytest
from fastapi.testclient import TestClient

# Force test mode before importing anything
os.environ["TEST_MODE"] = "true"
os.environ.setdefault("ENABLE_STRAVA_INTEGRATION", "true")
os.environ["DEM_PROVIDER"] = "none"  # no HTTP calls in tests — DEM tests monkeypatch back
os.environ["OVERPASS_ENABLED"] = "false"  # no HTTP calls in tests — overpass tests monkeypatch back
os.environ["DFCI_ENABLED"] = "false"  # no Overpass calls for DFCI in tests
os.environ["DFCI_HERAULT_ENABLED"] = "false"  # no API calls for DFCI Hérault in tests
os.environ["TRAILS_ENABLED"] = "false"  # no Overpass/cache calls for trails in tests
os.environ["SKIP_BACKGROUND_LOAD"] = "true"  # skip entire _background_data_load in TestClient lifespan
os.environ["ROUTING_EXTERNAL"] = "false"  # no OSRM/BRouter calls in tests
os.environ["RATELIMIT_ENABLED"] = "false"  # disable rate limiting in tests (test_security.py re-enables explicitly)
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://postgres:postgres@db:5432/common_trails",
)

from app.main import app  # noqa: E402
from app.rate_limit import limiter  # noqa: E402

# Force-disable rate limiting after import (module-level flag may differ from env var)
limiter.enabled = False


@pytest.fixture(scope="session")
def client() -> TestClient:
    """FastAPI test client (synchronous)."""
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _reset_rate_limiter_and_cookies(client):
    """Ensure rate limiter is disabled and cookies are cleared before each test."""
    limiter.enabled = False
    client.cookies.clear()
    yield
    limiter.enabled = False


# Hérault/Gard corridor the local golden GPX fixtures live in. ONE shared bbox
# so every OSM-gated golden uses the SAME skip predicate (agent-C audit: today
# the convention is ad-hoc per-test bbox isolation).
HERAULT_BBOX = (43.40, 3.30, 44.30, 4.30)  # lat0, lon0, lat1, lon1


def osm_present_in_bbox(bbox: tuple[float, float, float, float] = HERAULT_BBOX,
                        min_edges: int = 1000) -> bool:
    """True when osm_road_edges has a real OSM import covering ``bbox`` — the
    single gate for all ``@pytest.mark.golden`` ingestion tests.

    CI has no PBF → returns False → those tests skip. Local runs with the
    occitanie import → returns True → they run for real. Opens its own
    short-lived session so it's callable at module-collection time.
    """
    from sqlalchemy import text as _sa_text

    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        n = db.execute(_sa_text(
            "SELECT count(*) FROM osm_road_edges WHERE geometry && "
            "ST_MakeEnvelope(:lon0,:lat0,:lon1,:lat1,4326)"
        ), {"lat0": bbox[0], "lon0": bbox[1], "lat1": bbox[2], "lon1": bbox[3]}).scalar() or 0
        return n > min_edges
    except Exception:
        return False
    finally:
        db.close()


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    """Register a fresh test user and return Authorization headers."""
    import uuid

    email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "testpass123", "username": f"user_{uuid.uuid4().hex[:6]}"},
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
