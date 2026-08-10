"""Non-regression tests for the run_jobs config-drift startup guard.

``trigger_ingest_archives_job`` / ``trigger_build_pmtiles_job`` silently return
False (event-driven drain + PMTiles rebuild never fire) when ``GCP_PROJECT`` /
``GCP_REGION`` / the ``*_JOB_NAME`` vars are unset — the classic cause being a
prod ``gcloud run ... --set-env-vars`` that WIPES the whole env. The guard
``warn_on_missing_job_config`` surfaces that in the logs without crashing.

These drive the REAL function (no inline mirror).
"""
import logging

from app.services.run_jobs import (
    _EXPECTED_JOB_CONFIG_ENV,
    warn_on_missing_job_config,
)

_ALL = {
    "GCP_PROJECT": "proj",
    "GCP_REGION": "europe-west1",
    "INGEST_ARCHIVES_JOB_NAME": "ingest-pending-archives-prod",
    "BUILD_PMTILES_JOB_NAME": "common-trails-build-pmtiles-prod",
}


def _set_env(monkeypatch, present: dict) -> None:
    for k in _EXPECTED_JOB_CONFIG_ENV:
        if k in present:
            monkeypatch.setenv(k, present[k])
        else:
            monkeypatch.delenv(k, raising=False)


def test_all_present_no_warning(monkeypatch, caplog):
    _set_env(monkeypatch, _ALL)
    with caplog.at_level(logging.WARNING, logger="app.services.run_jobs"):
        missing = warn_on_missing_job_config()
    assert missing == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_missing_vars_returned_and_warned(monkeypatch, caplog):
    # Simulate a --set-env-vars wipe: only DATABASE_URL-style survivors remain,
    # every job-trigger var is gone.
    _set_env(monkeypatch, {})
    with caplog.at_level(logging.WARNING, logger="app.services.run_jobs"):
        missing = warn_on_missing_job_config()
    assert set(missing) == set(_EXPECTED_JOB_CONFIG_ENV)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    # Every missing var is named in the single warning line.
    msg = warnings[0].getMessage()
    for var in _EXPECTED_JOB_CONFIG_ENV:
        assert var in msg


def test_partial_missing_only_reports_absent(monkeypatch, caplog):
    _set_env(monkeypatch, {k: v for k, v in _ALL.items() if k != "BUILD_PMTILES_JOB_NAME"})
    with caplog.at_level(logging.WARNING, logger="app.services.run_jobs"):
        missing = warn_on_missing_job_config()
    assert missing == ["BUILD_PMTILES_JOB_NAME"]


def test_does_not_raise_on_missing(monkeypatch):
    """The guard must NEVER crash startup — the daily backstop still runs."""
    _set_env(monkeypatch, {})
    # No exception = pass.
    warn_on_missing_job_config()
