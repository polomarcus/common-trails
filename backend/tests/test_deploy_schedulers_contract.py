"""Non-regression pins for the prod Cloud Scheduler wiring in deploy-prod.sh.

2026-11-07 ops audit:
  * P0-1 — the drain / rebuild-pmtiles / resync-reminder schedulers call
    ``POST …/jobs/<job>:run`` as ``common-trails-tasks-invoker@`` but that SA had
    no ``roles/run.invoker`` on those jobs → PERMISSION_DENIED on every run since
    creation (the backstops never fired once, silently).
  * P0-2 — the Strava health-check scheduler audience fix: gone with the
    Strava API sync; the deploy must no longer touch that scheduler nor the
    three Strava jobs.
  * P2-1 — the drain job lacked ``HEATMAP_PUBLIC_BASE_URL`` (the "calque" email
    linked storage.googleapis.com) and ``HEATMAP_GCS_BUCKET`` on build-pmtiles
    was never asserted.

These tests EXECUTE the real script with a stub ``gcloud`` on PATH that records
every invocation (no inline mirror of the script's logic), then assert on the
calls it would make against prod.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


def _find_deploy_sh() -> Path | None:
    # Host: <repo>/scripts/deploy-prod.sh ; container: /scripts (bind-mounted).
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "scripts" / "deploy-prod.sh"
        if candidate.is_file():
            return candidate
    return None


_DEPLOY_SH = _find_deploy_sh()

pytestmark = pytest.mark.skipif(
    _DEPLOY_SH is None or shutil.which("bash") is None,
    reason="scripts/deploy-prod.sh (or bash) not reachable here (mount ./scripts to run)",
)

_TASKS_INVOKER = "serviceAccount:common-trails-tasks-invoker@common-trails.iam.gserviceaccount.com"
_JOBS_SA = "serviceAccount:common-trails-jobs@common-trails.iam.gserviceaccount.com"
_SCHEDULED_JOBS = (
    "common-trails-ingest-pending-archives-prod",
    "common-trails-build-pmtiles-prod",
    "common-trails-resync-reminder-prod",
)
_SEP = "@@CALL@@"


@pytest.fixture(scope="module")
def gcloud_calls(tmp_path_factory) -> list[list[str]]:
    """Run deploy-prod.sh end-to-end against a recording gcloud stub."""
    tmp = tmp_path_factory.mktemp("deploy")
    log = tmp / "gcloud.log"
    stub = tmp / "gcloud"
    stub.write_text(
        f'#!/usr/bin/env bash\n{{ echo "{_SEP}"; for a in "$@"; do printf "%s\\n" "$a"; done; }} >> "{log}"\n'
        # The private "Ma heatmap" bucket assertion reads this field back and
        # aborts the deploy unless GCS reports it enforced.
        'case "$*" in *public_access_prevention*) echo enforced ;; esac\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {
        **os.environ,
        "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
        "DEPLOY_YES": "1",
        # As in prod (deploy-gcp.yml): the per-user "Ma heatmap" file is ON.
        "MY_HEATMAP_FILES_ENABLED": "true",
    }
    proc = subprocess.run(
        ["bash", str(_DEPLOY_SH), "test-tag"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"deploy-prod.sh failed:\n{proc.stdout}\n{proc.stderr}"
    calls = []
    for chunk in log.read_text(encoding="utf-8").split(f"{_SEP}\n")[1:]:
        calls.append(chunk.rstrip("\n").split("\n"))
    return calls


def _flag(call: list[str], name: str) -> str | None:
    for arg in call:
        if arg.startswith(f"--{name}="):
            return arg.split("=", 1)[1]
    return None


def _job_env_update(calls: list[list[str]], job: str) -> str:
    for c in calls:
        if c[:4] == ["run", "jobs", "update", job] and _flag(c, "update-env-vars"):
            return _flag(c, "update-env-vars") or ""
    raise AssertionError(f"no --update-env-vars call for {job}")


@pytest.mark.parametrize("job", _SCHEDULED_JOBS)
def test_scheduler_sa_gets_run_invoker_on_each_scheduled_job(gcloud_calls, job):
    """P0-1: each job a scheduler fires via jobs:run must grant the scheduler SA
    roles/run.invoker ON THE JOB (least privilege, not project-wide)."""
    bindings = [c for c in gcloud_calls if c[:4] == ["run", "jobs", "add-iam-policy-binding", job]]
    assert bindings, f"no run.invoker binding asserted on {job}"
    assert _flag(bindings[0], "member") == _TASKS_INVOKER
    assert _flag(bindings[0], "role") == "roles/run.invoker"


def test_no_project_level_or_broader_grant(gcloud_calls):
    """Least privilege: never a project-wide IAM grant from the deploy."""
    assert not any(c[:2] == ["projects", "add-iam-policy-binding"] for c in gcloud_calls)
    for c in gcloud_calls:
        if "add-iam-policy-binding" in c:
            if c[:3] == ["storage", "buckets", "add-iam-policy-binding"]:
                # The ONE bucket-scoped grant: the runtime SA on the PRIVATE
                # "Ma heatmap" bucket (docs/ma-heatmap-fichier-prive.md §3).
                assert c[3] == "gs://common-trails-my-heatmap-prod"
                assert _flag(c, "role") == "roles/storage.objectAdmin"
                assert _flag(c, "member") == "serviceAccount:common-trails-api@common-trails.iam.gserviceaccount.com"
                continue
            if c[:3] == ["tasks", "queues", "add-iam-policy-binding"]:
                # Jobs SA may enqueue on the artefact-rebuild queue ONLY.
                assert c[3] == "common-trails-artefact-rebuild-prod"
                assert _flag(c, "role") == "roles/cloudtasks.enqueuer"
                assert _flag(c, "member") == _JOBS_SA
                continue
            if c[:3] == ["iam", "service-accounts", "add-iam-policy-binding"]:
                # Jobs SA may act as the tasks-invoker SA ONLY (task OIDC token).
                assert c[3] == _TASKS_INVOKER.removeprefix("serviceAccount:")
                assert _flag(c, "role") == "roles/iam.serviceAccountUser"
                assert _flag(c, "member") == _JOBS_SA
                continue
            assert _flag(c, "role") == "roles/run.invoker"


def test_jobs_sa_can_enqueue_my_heatmap_rebuilds(gcloud_calls):
    """2026-11-11: `backfill --enqueue` (and the drain's post-import rebuild)
    run as the JOBS SA, which had neither cloudtasks.tasks.create on the queue
    nor actAs on the tasks-invoker SA → every enqueue 403'd (6/6 failed). The
    deploy must assert both resource-scoped bindings."""
    queue = [c for c in gcloud_calls if c[:3] == ["tasks", "queues", "add-iam-policy-binding"]]
    assert queue and queue[0][3] == "common-trails-artefact-rebuild-prod"
    assert _flag(queue[0], "member") == _JOBS_SA
    assert _flag(queue[0], "role") == "roles/cloudtasks.enqueuer"
    assert _flag(queue[0], "location") == "europe-west1"
    act_as = [c for c in gcloud_calls if c[:3] == ["iam", "service-accounts", "add-iam-policy-binding"]]
    assert act_as and act_as[0][3] == _TASKS_INVOKER.removeprefix("serviceAccount:")
    assert _flag(act_as[0], "member") == _JOBS_SA
    assert _flag(act_as[0], "role") == "roles/iam.serviceAccountUser"


def test_iam_bindings_run_after_jobs_are_updated(gcloud_calls):
    """Bindings target jobs that exist → they come after the image sync."""
    idx = {tuple(c[:4]): i for i, c in enumerate(gcloud_calls)}
    for job in _SCHEDULED_JOBS:
        assert idx[("run", "jobs", "add-iam-policy-binding", job)] > idx[("run", "jobs", "update", job)]


def test_deploy_no_longer_touches_strava_resources(gcloud_calls):
    """Strava API sync removed: no gcloud call may reference its jobs, its
    health-check scheduler, its webhook queue or its secrets. (Those prod
    resources are orphaned and deleted by hand — never by the deploy.)"""
    flat = "\n".join(arg for call in gcloud_calls for arg in call).lower()
    assert "strava" not in flat.replace("/imports/strava-archive", "")
    assert not any(
        c[:3] == ["scheduler", "jobs", "update"] and "health-check" in " ".join(c)
        for c in gcloud_calls
    )


def test_deploy_needs_no_operator_input(tmp_path):
    """The mandatory STRAVA_WEBHOOK_SUBSCRIPTION_ID is gone: the script must
    get past its input checks with a bare environment (it then stops at the
    missing image tag, exit 2 — before any gcloud call)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("STRAVA_")}
    proc = subprocess.run(
        ["bash", str(_DEPLOY_SH)], env=env, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 2
    assert "image tag required" in proc.stderr
    assert "STRAVA" not in proc.stderr


def test_drain_job_carries_public_heatmap_base_url(gcloud_calls):
    """P2-1: the "traces on the map" email builds the calque URL from
    HEATMAP_PUBLIC_BASE_URL; the drain job must carry the tiles.* CDN origin."""
    env = _job_env_update(gcloud_calls, "common-trails-ingest-pending-archives-prod")
    assert "HEATMAP_PUBLIC_BASE_URL=https://tiles.chemins-communs.fr" in env


def test_build_job_asserts_heatmap_bucket(gcloud_calls):
    """P2-1: without HEATMAP_GCS_BUCKET the build succeeds but uploads nothing."""
    env = _job_env_update(gcloud_calls, "common-trails-build-pmtiles-prod")
    assert "HEATMAP_GCS_BUCKET=common-trails-heatmap-prod" in env
    assert "HEATMAP_PUBLIC_BASE_URL=https://tiles.chemins-communs.fr" in env


def _find_deploy_workflow() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".github" / "workflows" / "deploy-gcp.yml"
        if candidate.is_file():
            return candidate
    return None


@pytest.mark.skipif(_find_deploy_workflow() is None, reason=".github not mounted here")
def test_cd_web_healthcheck_hits_real_readiness_route():
    """P1-2: /api/health/live does not exist — the SPA catch-all 200s it."""
    wf = _find_deploy_workflow().read_text(encoding="utf-8")
    assert "https://chemins-communs.fr/api/health/live" not in wf
    assert "https://chemins-communs.fr/readyz" in wf


def test_heatmap_rebuild_backstop_is_weekly_not_daily(gcloud_calls):
    """Cost (Paul 2026-11-07): the full PMTiles rebuild backstop runs once a week (Saturday 11:00 Paris, watchable);
    uploads already trigger an event-driven rebuild."""
    updates = [
        c
        for c in gcloud_calls
        if c[:4] == ["scheduler", "jobs", "update", "http"] and c[4] == "common-trails-rebuild-pmtiles-daily-prod"
    ]
    assert updates, "rebuild scheduler schedule is not asserted by the deploy"
    schedule = _flag(updates[0], "schedule")
    assert schedule == "0 11 * * 6", f"rebuild backstop must stay weekly: {schedule!r}"
    assert _flag(updates[0], "time-zone") == "Europe/Paris"
