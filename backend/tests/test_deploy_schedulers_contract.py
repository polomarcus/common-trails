"""Non-regression pins for the prod Cloud Scheduler wiring in deploy-prod.sh.

2026-11-07 ops audit:
  * P0-1 — the drain / rebuild-pmtiles / resync-reminder schedulers call
    ``POST …/jobs/<job>:run`` as ``common-trails-tasks-invoker@`` but that SA had
    no ``roles/run.invoker`` on those jobs → PERMISSION_DENIED on every run since
    creation (the backstops never fired once, silently).
  * P0-2 — ``strava-health-check-daily-prod`` signed its OIDC token for the
    custom domain while the web verifies ``aud`` against
    ``INTERNAL_STRAVA_HEALTH_HANDLER_URL`` (the .run.app URL) → 401 every day.
  * P2-1 — the drain job lacked ``HEATMAP_PUBLIC_BASE_URL`` (the "calque" email
    linked storage.googleapis.com) and ``HEATMAP_GCS_BUCKET`` on build-pmtiles
    was never asserted.

These tests EXECUTE the real script with a stub ``gcloud`` on PATH that records
every invocation (no inline mirror of the script's logic), then assert on the
calls it would make against prod.
"""

from __future__ import annotations

import os
import re
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
        f'#!/usr/bin/env bash\n{{ echo "{_SEP}"; for a in "$@"; do printf "%s\\n" "$a"; done; }} >> "{log}"\nexit 0\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {
        **os.environ,
        "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
        "DEPLOY_YES": "1",
        "STRAVA_WEBHOOK_SUBSCRIPTION_ID": "349136",
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
            assert _flag(c, "role") == "roles/run.invoker"


def test_iam_bindings_run_after_jobs_are_updated(gcloud_calls):
    """Bindings target jobs that exist → they come after the image sync."""
    idx = {tuple(c[:4]): i for i, c in enumerate(gcloud_calls)}
    for job in _SCHEDULED_JOBS:
        assert idx[("run", "jobs", "add-iam-policy-binding", job)] > idx[("run", "jobs", "update", job)]


def test_health_check_scheduler_audience_matches_handler_env(gcloud_calls):
    """P0-2: the scheduler's OIDC audience must equal, byte-for-byte, the
    INTERNAL_STRAVA_HEALTH_HANDLER_URL the web verifies against."""
    script = _DEPLOY_SH.read_text(encoding="utf-8")
    proj_num = re.search(r'PROJECT_NUMBER="(\d+)"', script).group(1)
    expected = f"https://common-trails-api-prod-{proj_num}.europe-west1.run.app/internal/strava/health-check"
    # The service env declares the same URL (resolved form).
    assert re.search(
        r'^INTERNAL_STRAVA_HEALTH_HANDLER_URL:\s*"\$\{RUN_URL\}/internal/strava/health-check"',
        script,
        re.MULTILINE,
    )
    updates = [
        c
        for c in gcloud_calls
        if c[:4] == ["scheduler", "jobs", "update", "http"] and c[4] == "strava-health-check-daily-prod"
    ]
    assert updates, "health-check scheduler is not asserted by the deploy"
    call = updates[0]
    assert _flag(call, "oidc-token-audience") == expected
    assert _flag(call, "uri") == expected
    assert "chemins-communs.fr" not in (_flag(call, "oidc-token-audience") or "")
    assert _flag(call, "oidc-service-account-email") == _TASKS_INVOKER.split(":", 1)[1]


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
    """Cost (Paul 2026-11-07): the full PMTiles rebuild backstop runs once a week;
    uploads already trigger an event-driven rebuild."""
    updates = [
        c
        for c in gcloud_calls
        if c[:4] == ["scheduler", "jobs", "update", "http"] and c[4] == "common-trails-rebuild-pmtiles-daily-prod"
    ]
    assert updates, "rebuild scheduler schedule is not asserted by the deploy"
    schedule = _flag(updates[0], "schedule")
    assert schedule == "30 4 * * 1", f"rebuild backstop must stay weekly: {schedule!r}"
