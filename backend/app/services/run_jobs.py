"""Best-effort Cloud Run Job triggers from the web / job runtime.

Event-driven kicks for prod Cloud Run Jobs:

* ``trigger_ingest_archives_job`` — when a Strava-archive upload is finalised
  (``POST /imports/strava-archive/complete``) we fire a single ``jobs:run`` REST
  call so the archive is drained promptly instead of waiting for the daily
  backstop scheduler.
* ``trigger_build_pmtiles_job`` — after the archive drain ingests >=1 activity
  (heat computation on) we fire the ``common-trails-build-pmtiles`` job so the
  new contribution appears on the static ``heatmap-display.pmtiles`` served from
  GCS. Without this a contribution updates ``heat_edges``/``heat_edges_agg`` but
  never reaches the map until the next unrelated rebuild.

Auth mirrors ``app.api.admin._fetch_executions``: the in-container GCE metadata
server mints an access token for the calling runtime SA. This only works on
Cloud Run; locally / in tests the helpers no-op (see the env gate) so the
network is never touched.
"""
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

# ── build-pmtiles trigger debounce ────────────────────────────────────────────
# The build-pmtiles Cloud Run Job is EXPENSIVE (a full display rebuild). The
# ``/imports/files`` endpoint fires it best-effort on every batch-end so a
# fresh contribution appears on the map — but nothing stopped an abusive
# (registered) user looping tiny 1-point uploads to fire N rebuild JOBS ($$$).
# Debounce the trigger to at most once per cooldown window: the FIRST trigger in
# a window fires, the rest are suppressed. Correctness is preserved — the daily
# backstop scheduler and the next real upload AFTER the cooldown both still
# refresh the display, so a suppressed trigger only DELAYS a rebuild, never
# drops it. Process-local (per Cloud Run instance) — good enough to defang the
# per-user request loop; the goal is cost-bounding, not global exactness.
# Cooldown is env-overridable via BUILD_PMTILES_DEBOUNCE_S (default 600s = 10min).
_build_pmtiles_last_trigger: float = 0.0
_build_pmtiles_debounce_lock = threading.Lock()


def build_pmtiles_debounce_ok(now: float | None = None) -> bool:
    """Return True at most once per ``BUILD_PMTILES_DEBOUNCE_S`` window.

    Records the trigger time on a True result. ``now`` (monotonic seconds) is
    injectable for deterministic tests. A cooldown of 0 disables debouncing
    (every call returns True) — handy to opt out in an environment that wants
    every upload to refresh immediately.
    """
    global _build_pmtiles_last_trigger
    cooldown = float(os.environ.get("BUILD_PMTILES_DEBOUNCE_S", "600"))
    t = time.monotonic() if now is None else now
    with _build_pmtiles_debounce_lock:
        if (
            cooldown > 0
            and _build_pmtiles_last_trigger
            and (t - _build_pmtiles_last_trigger) < cooldown
        ):
            return False
        _build_pmtiles_last_trigger = t
        return True


def reset_build_pmtiles_debounce() -> None:
    """Test hook: clear the debounce state so a suite starts from a clean window."""
    global _build_pmtiles_last_trigger
    with _build_pmtiles_debounce_lock:
        _build_pmtiles_last_trigger = 0.0

_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)


# The env vars the event-driven job triggers below depend on. A prod deploy
# that uses `gcloud run ... --set-env-vars` (which REPLACES the whole env)
# instead of `--update-env-vars` wipes these, silently disabling the
# event-driven archive drain + PMTiles rebuild — they then only run via the
# daily backstop scheduler, and the regression is invisible. `_is_enabled`
# returns False with no signal. Surface it loudly at startup instead.
_EXPECTED_JOB_CONFIG_ENV = (
    "GCP_PROJECT",
    "GCP_REGION",
    "INGEST_ARCHIVES_JOB_NAME",
    "BUILD_PMTILES_JOB_NAME",
)


def warn_on_missing_job_config() -> list[str]:
    """Log a loud WARNING naming any expected job-trigger env var that is unset.

    Returns the list of missing var names (so a caller / test can inspect it).
    Does NOT raise and does NOT crash startup — the daily backstop scheduler
    still drains archives and rebuilds PMTiles, so a missing var degrades
    gracefully. This only makes a silent config-drift regression visible in
    the logs (a `--set-env-vars` wipe is the classic cause).
    """
    missing = [k for k in _EXPECTED_JOB_CONFIG_ENV if not os.environ.get(k)]
    if missing:
        log.warning(
            "Event-driven job triggers DEGRADED — missing env var(s): %s. The "
            "archive drain + PMTiles rebuild will only run via the daily "
            "backstop scheduler until these are restored (a `gcloud run ... "
            "--set-env-vars` wipe is the usual cause — use --update-env-vars).",
            ", ".join(missing),
        )
    return missing


def _is_enabled(job_env: str) -> bool:
    """True iff the job target ``job_env`` is configured AND we are not in
    TEST_MODE.

    Same discipline as ``cloud_tasks._is_enabled`` — tests and local dev never
    reach the metadata server or the Cloud Run API.
    """
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return False
    if not os.environ.get("GCP_PROJECT") or not os.environ.get("GCP_REGION"):
        return False
    return bool(os.environ.get(job_env))


def _trigger_job(job: str, label: str) -> bool:
    """Fire-and-forget: request one execution of Cloud Run Job ``job``.

    Returns True on a 2xx from ``jobs:run``, else False. NEVER raises — every
    I/O failure is logged and swallowed so callers stay best-effort.
    """
    project = os.environ["GCP_PROJECT"]
    region = os.environ["GCP_REGION"]

    try:
        import httpx

        token_resp = httpx.get(
            _METADATA_TOKEN_URL,
            headers={"Metadata-Flavor": "Google"},
            timeout=3.0,
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]

        url = (
            f"https://run.googleapis.com/v2/projects/{project}"
            f"/locations/{region}/jobs/{job}:run"
        )
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
        )
    except Exception as exc:
        log.warning("Failed to trigger %s job %s: %s", label, job, exc)
        return False

    if resp.is_success:
        log.info("Triggered %s job %s (%s)", label, job, resp.status_code)
        return True

    log.error(
        "%s job %s trigger returned %s: %s",
        label, job, resp.status_code, resp.text[:500],
    )
    return False


def trigger_ingest_archives_job() -> bool:
    """Fire-and-forget: request one execution of the ingest-pending-archives
    Cloud Run Job.

    Returns True on a 2xx from ``jobs:run``, else False. NEVER raises — every
    I/O failure is logged and swallowed so ``/complete`` stays best-effort (the
    daily backstop scheduler drains anything a missed trigger leaves behind).
    """
    if not _is_enabled("INGEST_ARCHIVES_JOB_NAME"):
        return False
    return _trigger_job(os.environ["INGEST_ARCHIVES_JOB_NAME"], "ingest-archives")


def trigger_build_pmtiles_job() -> bool:
    """Fire-and-forget: request one execution of the build-pmtiles Cloud Run
    Job so the static ``heatmap-display.pmtiles`` on GCS is rebuilt from the
    fresh ``heat_edges_agg``.

    Returns True on a 2xx from ``jobs:run``, else False. NEVER raises — every
    I/O failure is logged and swallowed so the archive drain stays best-effort
    (the ingested ``heat_edges`` are already committed; a missed ping is caught
    by the next drain / an unrelated rebuild). The build-pmtiles job is proven
    to run on db-f1-micro with no tier bump.
    """
    if not _is_enabled("BUILD_PMTILES_JOB_NAME"):
        return False
    return _trigger_job(os.environ["BUILD_PMTILES_JOB_NAME"], "build-pmtiles")
