"""Queue-path resolution reads GCP_PROJECT (the var the prod deploy sets), with
a GOOGLE_CLOUD_PROJECT fallback. Regression: it used to read ONLY
GOOGLE_CLOUD_PROJECT — which deploy-prod.sh never sets — so every bare queue
name raised, breaking the Strava webhook enqueue (503) + artefact-rebuild."""
import pytest

from app.services import cloud_tasks


@pytest.fixture
def _clean_env(monkeypatch):
    for k in ("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT", "CLOUD_TASKS_QUEUE",
              "CLOUD_TASKS_LOCATION", "CLOUD_TASKS_ARTEFACT_QUEUE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLOUD_TASKS_LOCATION", "europe-west1")


def test_bare_queue_resolves_from_gcp_project(_clean_env, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT", "common-trails")
    monkeypatch.setenv("CLOUD_TASKS_QUEUE", "common-trails-heat-compute-prod")
    assert cloud_tasks._resolve_queue_path() == (
        "projects/common-trails/locations/europe-west1/queues/common-trails-heat-compute-prod"
    )


def test_google_cloud_project_still_works_as_fallback(_clean_env, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "common-trails")
    monkeypatch.setenv("CLOUD_TASKS_QUEUE", "q")
    assert cloud_tasks._resolve_queue_path().endswith("/queues/q")


def test_bare_queue_without_any_project_raises(_clean_env, monkeypatch):
    # This is the OLD prod state (GCP_PROJECT set but code read GOOGLE_CLOUD_PROJECT):
    # with NEITHER project var, a bare queue must raise — the guard still holds.
    monkeypatch.setenv("CLOUD_TASKS_QUEUE", "q")
    with pytest.raises(RuntimeError):
        cloud_tasks._resolve_queue_path()


def test_resolve_queue_path_for_artefact_queue_uses_gcp_project(_clean_env, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT", "common-trails")
    monkeypatch.setenv("CLOUD_TASKS_ARTEFACT_QUEUE", "common-trails-artefact-rebuild-prod")
    got = cloud_tasks._resolve_queue_path_for("CLOUD_TASKS_ARTEFACT_QUEUE")
    assert got.endswith("/queues/common-trails-artefact-rebuild-prod")
    assert "projects/common-trails/" in got


def test_fully_qualified_queue_passthrough(_clean_env, monkeypatch):
    monkeypatch.setenv("CLOUD_TASKS_QUEUE", "projects/x/locations/y/queues/z")
    assert cloud_tasks._resolve_queue_path() == "projects/x/locations/y/queues/z"
