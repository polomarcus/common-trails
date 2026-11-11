"""Non-regression pins for the microservice-split deploy contract.

The light-WEB / heavy-WORKER split (2026-07-13) is realised entirely by
`scripts/deploy-prod.sh`: two Cloud Run services from the same image, differing
only by memory + where the `INTERNAL_*_HANDLER_URL` env vars point. The *only*
fragile part is the OIDC-audience wiring — Cloud Tasks signs `audience =
INTERNAL_<x>_HANDLER_URL` (set by the WEB) and the WORKER verifies the token's
`aud` against the SAME env var. If a future edit points a heavy handler back at
the WEB url, or changes the WEB url host, or drops a handler env the code still
verifies against, the heat/artefact chain silently 401s or lands on the
wrong (512Mi) service.

These tests fail on exactly those regressions. They parse the real deploy
script + the real handler sources (no inline mirror of behaviour), so they pin
BOTH ends of the contract: the deploy sets the URL, the handler verifies the
same env-var name.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


def _find_deploy_sh() -> Path | None:
    """Locate ``scripts/deploy-prod.sh`` robustly across environments.

    The file lives OUTSIDE the ``./backend`` build context, so the layout
    differs by where the tests run:

    - Host / full checkout: ``<repo>/backend/tests/…`` → the script is at
      ``<repo>/scripts/deploy-prod.sh`` (a few parents up).
    - CI / dockerised backend (``docker-compose*.yml``): the code is at
      ``/app`` and ``scripts/`` is bind-mounted read-only at ``/scripts``.
      Since ``/`` is a parent of ``/app/tests``, checking
      ``<parent>/scripts/deploy-prod.sh`` for each parent finds ``/scripts``.

    Returns the resolved path, or ``None`` if it genuinely isn't mounted
    anywhere (so the caller can skip rather than error at setup).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "scripts" / "deploy-prod.sh"
        if candidate.is_file():
            return candidate
    return None


def _find_api_dir() -> Path:
    """Locate the ``app/api`` package dir — robust to host vs container.

    Container: code at ``/app`` → ``/app/app/api``. Host: ``backend/tests``
    → ``backend/app/api``. Both are ``<tests-parent>/app/api``.
    """
    return Path(__file__).resolve().parents[1] / "app" / "api"


_DEPLOY_SH = _find_deploy_sh()
_API_DIR = _find_api_dir()

# If the deploy script isn't reachable (e.g. a stripped container without the
# /scripts mount), skip the whole module with a clear reason rather than error
# at setup. In CI (docker-compose.backend.yml) + on the host checkout it IS
# reachable, so these run for real there.
pytestmark = pytest.mark.skipif(
    _DEPLOY_SH is None,
    reason="scripts/deploy-prod.sh not reachable here (mount ./scripts to run)",
)

# The HEAVY Cloud Tasks handlers that MUST run on the 2Gi worker.
_HEAVY_HANDLER_ENVS = (
    "INTERNAL_ARTEFACT_HANDLER_URL",
    "INTERNAL_MY_HEATMAP_HANDLER_URL",
)

# Env of the removed matched pipeline: no code reads these any more, so the
# authoritative service env-file must not carry them (a var that "does
# nothing" is how the next reader gets misled).
_RETIRED_ENVS = (
    "HEATMAP_DISPLAY_SOURCE",
    "HEATMAP_KEEP_GRID_FALLBACK",
    "HEATMAP_GRID_FALLBACK_MIN_UC",
    "OVERPASS_ENABLED",
    "CLOUD_TASKS_QUEUE",
    "INTERNAL_HEAT_HANDLER_URL",
    "OSM_TILE_CACHE_MAX",
    "OSM_GRID_CACHE_MAX",
    "OSM_GRID_CACHE_MAX_SEGS_PER_ENTRY",
)

# The live web .run.app URL is an OIDC + custom-domain anchor: it must not
# change host when the split is introduced (only the worker is new).
_WEB_RUN_URL_LITERAL = (
    "https://common-trails-api-prod-301616827398.europe-west1.run.app"
)


@pytest.fixture(scope="module")
def deploy_sh() -> str:
    assert _DEPLOY_SH.exists(), f"deploy script missing at {_DEPLOY_SH}"
    return _DEPLOY_SH.read_text(encoding="utf-8")


def _env_value(script: str, key: str) -> str:
    """Return the RHS of a `KEY: "..."` line in the env-file heredoc."""
    m = re.search(rf'^{re.escape(key)}:\s*"([^"]*)"', script, re.MULTILINE)
    assert m, f"{key} not found in deploy-prod.sh env-file block"
    return m.group(1)


def test_web_run_url_unchanged(deploy_sh: str):
    """RUN_URL (web) must still resolve to the exact live .run.app host —
    that URL is what the domain mapping already points at. Changing its host = the 2026-06-04 401 loop."""
    # The script derives it from PROJECT_NUMBER; reconstruct + compare.
    proj_num = re.search(r'PROJECT_NUMBER="(\d+)"', deploy_sh)
    assert proj_num, "PROJECT_NUMBER not defined"
    expected = (
        f"https://common-trails-api-prod-{proj_num.group(1)}"
        ".europe-west1.run.app"
    )
    assert expected == _WEB_RUN_URL_LITERAL, (
        "WEB .run.app host changed — this breaks the custom-domain mapping. "
        "If Cloud Run genuinely reassigned it, update _WEB_RUN_URL_LITERAL "
        "in this test."
    )


def test_worker_url_derived_from_same_project_number(deploy_sh: str):
    """WORKER_RUN_URL must be the same deterministic
    {service}-{PROJECT_NUMBER}.{region}.run.app form as the web — that is
    what makes the first-deploy URL predictable without a describe."""
    assert 'WORKER_SERVICE="common-trails-worker-${ENV}"' in deploy_sh
    assert (
        'WORKER_RUN_URL="https://${WORKER_SERVICE}-${PROJECT_NUMBER}'
        '.${REGION}.run.app"'
    ) in deploy_sh


@pytest.mark.parametrize("env_key", _HEAVY_HANDLER_ENVS)
def test_heavy_handlers_point_at_worker(deploy_sh: str, env_key: str):
    """Artefact / my-heatmap handler URLs must target WORKER_RUN_URL —
    Cloud Tasks binds the OIDC audience to this value, so it decides which
    service (and which memory tier) runs the heavy work."""
    value = _env_value(deploy_sh, env_key)
    assert value.startswith("${WORKER_RUN_URL}/"), (
        f"{env_key} must point at the WORKER (2Gi) service, got {value!r}. "
        "Pointing it at the web (RUN_URL) sends heavy work back onto the "
        "512Mi public service and defeats the split."
    )


def test_no_strava_api_sync_wiring_left(deploy_sh: str):
    """The Strava API sync (OAuth connect, webhook, resync jobs) is removed:
    the deploy must not set its env vars / secrets nor sync its jobs — the
    entrypoints no longer exist in the image. Comments are ignored."""
    code = "\n".join(
        line for line in deploy_sh.splitlines() if not line.lstrip().startswith("#")
    )
    deploy_keys = set(re.findall(r'^([A-Z0-9_]+):\s*"', code, re.MULTILINE))
    assert not {k for k in deploy_keys if "STRAVA" in k}
    assert not re.search(r'^SECRETS=.*STRAVA', code, re.MULTILINE | re.IGNORECASE)
    for job in ("import-strava", "strava-subscribe", "resync-strava", "strava-health-check"):
        assert job not in code, f"{job} still referenced by deploy-prod.sh"


def test_both_services_deployed_with_correct_memory(deploy_sh: str):
    """The split is only real if BOTH services actually get deployed, web at
    512Mi and worker at 2Gi."""
    assert re.search(
        r'deploy_service\s+"\$WEB_SERVICE"\s+"512Mi"', deploy_sh
    ), "WEB service must be deployed at 512Mi"
    assert re.search(
        r'deploy_service\s+"\$WORKER_SERVICE"\s+"2Gi"', deploy_sh
    ), "WORKER service must be deployed at 2Gi"


@pytest.mark.parametrize("env_key", _RETIRED_ENVS)
def test_retired_matched_env_is_not_deployed(deploy_sh: str, env_key: str):
    """Neither the services' env-file (``KEY: "…"``) nor a job's
    ``--update-env-vars`` list (``KEY=…``) sets a retired variable."""
    assert not re.search(rf'^{env_key}:\s*"', deploy_sh, re.MULTILINE), env_key
    assert not re.search(rf'(?<![A-Z0-9_]){env_key}=', deploy_sh), env_key


def test_deploy_sets_every_env_the_handlers_verify(deploy_sh: str):
    """Cross-file pin: every `audience_env=` a /internal handler verifies
    against MUST be set by the deploy script. This is the guard against a
    handler verifying an env the deploy forgot to set (→ the 500
    'not configured for OIDC verification' path) — or a rename drifting the
    two ends apart. Parses the real handler sources, not a copy."""
    verified_envs: set[str] = set()
    for src in _API_DIR.glob("internal_*.py"):
        text = src.read_text(encoding="utf-8")
        verified_envs.update(re.findall(r'audience_env="([A-Z0-9_]+)"', text))
    # NOTE: export.py used to host /internal/export/build, but the on-demand /
    # async export pipeline was removed (exports are pre-computed artifacts
    # only), so it no longer verifies any audience env.
    assert _HEAVY_HANDLER_ENVS[0] in verified_envs, (
        "sanity: artefact handler should verify INTERNAL_ARTEFACT_HANDLER_URL"
    )

    deploy_keys = set(re.findall(r'^([A-Z0-9_]+):\s*"', deploy_sh, re.MULTILINE))
    required = verified_envs
    missing = required - deploy_keys
    assert not missing, (
        f"deploy-prod.sh does not set env(s) a /internal handler verifies "
        f"against: {sorted(missing)}. A handler verifying an unset audience "
        f"env 500s ('not configured for OIDC verification')."
    )


def test_uploads_bucket_cors_allows_resumable_browser_upload(deploy_sh: str):
    """The browser's chunked upload to a GCS resumable session is cross-origin
    from the site: it SENDS Content-Range and must READ Range on each 308 to
    resume. Pin the checked-in CORS SSOT + that the deploy actually applies it
    to the uploads bucket, for the same origin the API binds sessions to."""
    import json

    cors = json.loads((_DEPLOY_SH.parent / "uploads-bucket-cors.json").read_text(encoding="utf-8"))
    public_url = re.search(r'^PUBLIC_URL="([^"]+)"', deploy_sh, re.MULTILINE).group(1)
    assert _env_value(deploy_sh, "FRONTEND_URL") == "${PUBLIC_URL}"  # session origin
    rule = next(r for r in cors if public_url in r["origin"])
    assert {"PUT", "POST", "GET"} <= set(rule["method"])
    assert {"Content-Type", "Content-Range", "Range", "X-Goog-Resumable"} <= set(rule["responseHeader"])
    # `--cors-file` REPLACES the list: the JSON must stay a strict SUPERSET of
    # the live config it replaced (read 2026-11-07 from the prod bucket), so
    # the existing single signed PUT keeps working byte-for-byte.
    live_before = {
        "maxAgeSeconds": 3600, "method": ["PUT", "OPTIONS"],
        "origin": ["https://chemins-communs.fr"], "responseHeader": ["Content-Type"],
    }
    assert set(live_before["origin"]) <= set(rule["origin"])
    assert set(live_before["method"]) <= set(rule["method"])
    assert set(live_before["responseHeader"]) <= set(rule["responseHeader"])
    assert rule["maxAgeSeconds"] >= live_before["maxAgeSeconds"]
    assert re.search(
        r'gcloud storage buckets update "gs://\$\{UPLOADS_BUCKET_NAME\}".*--cors-file="\$CORS_FILE"',
        deploy_sh,
    ), "deploy-prod.sh must assert the uploads bucket CORS from the checked-in JSON"
    assert 'UPLOADS_BUCKET_NAME="${PROJECT}-common-trails-uploads-${ENV}"' in deploy_sh
    assert _env_value(deploy_sh, "UPLOADS_BUCKET") == "${PROJECT}-common-trails-uploads-${ENV}"
