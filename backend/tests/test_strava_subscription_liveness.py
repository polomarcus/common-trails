"""FIX 2a coverage — proactive Strava push-subscription liveness assertion.

Drives the REAL ``app.api.internal_strava_health.check_subscription_liveness``
with a monkeypatched ``httpx.get`` (no network) and env-configured creds.

The four verdicts that matter operationally:
  * ``ok``           — our exact callback URL is registered.
  * ``missing``      — Strava has NO subscription → every event silently lost.
  * ``url_mismatch`` — a subscription exists but points elsewhere (stale URL).
  * ``unknown``      — creds/callback unset → skip, no network call, no alert.

The daily health-check only *reactively* notices "no activity in 30 days".
This check asks Strava directly, catching a dead/mis-pointed subscription
within a day.
"""
from __future__ import annotations

import httpx
import pytest

import app.api.internal_strava_health as health_mod

_EXPECTED = "https://api.example.run.app/integrations/strava/webhook"


@pytest.fixture
def _creds(monkeypatch):
    monkeypatch.setenv("STRAVA_CLIENT_ID", "12345")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "shhh-secret")
    monkeypatch.setenv("STRAVA_WEBHOOK_CALLBACK_URL", _EXPECTED)
    yield


def _patch_get(monkeypatch, *, status: int, payload):
    def fake_get(url, params=None, timeout=None):
        return httpx.Response(
            status,
            json=payload,
            request=httpx.Request("GET", url),
        )
    monkeypatch.setattr(httpx, "get", fake_get)


def test_verdict_ok_when_expected_url_registered(_creds, monkeypatch):
    _patch_get(monkeypatch, status=200, payload=[
        {"id": 987, "callback_url": _EXPECTED},
    ])
    result = health_mod.check_subscription_liveness()
    assert result["verdict"] == "ok"
    assert _EXPECTED in result["registered_urls"]


def test_verdict_missing_when_no_subscription(_creds, monkeypatch):
    _patch_get(monkeypatch, status=200, payload=[])
    result = health_mod.check_subscription_liveness()
    assert result["verdict"] == "missing"
    assert result["registered_urls"] == []


def test_verdict_url_mismatch_when_pointing_elsewhere(_creds, monkeypatch):
    stale = "https://OLD-domain.run.app/integrations/strava/webhook"
    _patch_get(monkeypatch, status=200, payload=[
        {"id": 987, "callback_url": stale},
    ])
    result = health_mod.check_subscription_liveness()
    assert result["verdict"] == "url_mismatch"
    assert stale in result["registered_urls"]
    assert _EXPECTED not in result["registered_urls"]


def test_verdict_unknown_when_creds_missing(monkeypatch):
    # Ensure creds are unset → function must SKIP without any network call.
    monkeypatch.delenv("STRAVA_CLIENT_ID", raising=False)
    monkeypatch.delenv("STRAVA_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("STRAVA_WEBHOOK_CALLBACK_URL", raising=False)

    def _boom(*a, **k):
        raise AssertionError("must not call Strava when creds unset")
    monkeypatch.setattr(httpx, "get", _boom)

    result = health_mod.check_subscription_liveness()
    assert result["verdict"] == "unknown"


def test_verdict_error_on_non_200(_creds, monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return httpx.Response(500, text="boom", request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", fake_get)
    result = health_mod.check_subscription_liveness()
    assert result["verdict"] == "error"


def test_verdict_error_on_transport_failure(_creds, monkeypatch):
    def fake_get(url, params=None, timeout=None):
        raise httpx.ConnectError("dns")
    monkeypatch.setattr(httpx, "get", fake_get)
    result = health_mod.check_subscription_liveness()
    assert result["verdict"] == "error"
