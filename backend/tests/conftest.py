"""Pytest configuration and shared fixtures."""
import os

import pytest
from fastapi.testclient import TestClient

# Force test mode before importing anything
os.environ["TEST_MODE"] = "true"
os.environ["DFCI_ENABLED"] = "false"  # no Overpass calls for DFCI in tests
os.environ["DFCI_HERAULT_ENABLED"] = "false"  # no API calls for DFCI Hérault in tests
os.environ["SKIP_BACKGROUND_LOAD"] = "true"  # skip entire _background_data_load in TestClient lifespan
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
