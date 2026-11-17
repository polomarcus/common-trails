"""``POST /internal/artefacts/rebuild`` only fires the build-pmtiles JOB.

The ``artefact-rebuild-{window}`` Cloud Task (one per 5-min window of
uploads) lands on the worker. The handler used to run the FULL
community build in-process on the 2 GiB worker (``build_pmtiles.main``) and
re-upload the mutable PMTiles outside the job's compare-and-set publish. It
now asks for one run of the 8 GiB ``build-pmtiles`` Cloud Run Job and returns:

* 200 ``{"status": "ok", "job": "triggered"}`` when the run was requested;
* 503 when it was not, so Cloud Tasks retries (overlapping builds are safe).

Driven through the real FastAPI route; the job trigger is patched at its
source module (``app.services.run_jobs``), which the handler reads at call
time.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

_PATH = "/internal/artefacts/rebuild"


@pytest.fixture
def no_inprocess_build():
    """Any in-process build is a regression: record it instead of running it."""
    with patch("app.jobs.build_pmtiles.main") as build:
        yield build


def test_route_triggers_the_build_job(client, no_inprocess_build):
    trigger = MagicMock(return_value=True)
    with patch("app.services.run_jobs.trigger_build_pmtiles_job", trigger):
        resp = client.post(_PATH, json={})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok", "job": "triggered"}
    trigger.assert_called_once_with()
    no_inprocess_build.assert_not_called()


def test_route_never_builds_in_process(client, no_inprocess_build):
    """Non-regression: whatever the trigger outcome, the worker never runs
    ``build_pmtiles`` itself, and the old build-and-upload helper is gone."""
    from app.api import internal_artefacts

    for outcome in (True, False):
        with patch("app.services.run_jobs.trigger_build_pmtiles_job", return_value=outcome):
            client.post(_PATH, json={})
    no_inprocess_build.assert_not_called()
    assert not hasattr(internal_artefacts, "_rebuild_and_upload_pmtiles")


def test_trigger_failure_is_503_so_cloud_tasks_retries(client, no_inprocess_build):
    with patch("app.services.run_jobs.trigger_build_pmtiles_job", return_value=False):
        resp = client.post(_PATH, json={})
    assert resp.status_code == 503, resp.text
    no_inprocess_build.assert_not_called()


# ── OIDC: same gate as before (audience = INTERNAL_ARTEFACT_HANDLER_URL) ─────

_AUDIENCE = f"https://example.invalid{_PATH}"
_INVOKER_SA = "common-trails-tasks@example.iam.gserviceaccount.com"
_TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2ln"


@pytest.fixture
def prod_oidc(client, monkeypatch):
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("INTERNAL_ARTEFACT_HANDLER_URL", _AUDIENCE)
    monkeypatch.setenv("CLOUD_TASKS_INVOKER_SA", _INVOKER_SA)
    return client


def test_valid_oidc_token_reaches_the_trigger(prod_oidc, no_inprocess_build):
    def _verify(token, request, audience):  # noqa: ARG001
        assert audience == _AUDIENCE, "handler must verify against INTERNAL_ARTEFACT_HANDLER_URL"
        return {"iss": "https://accounts.google.com", "aud": audience, "email": _INVOKER_SA}

    trigger = MagicMock(return_value=True)
    with patch("google.oauth2.id_token.verify_oauth2_token", side_effect=_verify), \
         patch("app.services.run_jobs.trigger_build_pmtiles_job", trigger):
        resp = prod_oidc.post(_PATH, json={}, headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 200, resp.text
    trigger.assert_called_once_with()


def test_wrong_audience_is_401_and_fires_nothing(prod_oidc, no_inprocess_build):
    def _verify(token, request, audience):  # noqa: ARG001
        raise ValueError(f"Token has wrong audience attacker.invalid, expected {audience}")

    trigger = MagicMock(return_value=True)
    with patch("google.oauth2.id_token.verify_oauth2_token", side_effect=_verify), \
         patch("app.services.run_jobs.trigger_build_pmtiles_job", trigger):
        resp = prod_oidc.post(_PATH, json={}, headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 401, resp.text
    trigger.assert_not_called()
