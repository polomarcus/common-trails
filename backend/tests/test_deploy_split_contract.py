"""Non-regression pins for the microservice-split deploy contract.

The light-WEB / heavy-WORKER split (2026-07-13) is realised entirely by
`scripts/deploy-prod.sh`: two Cloud Run services from the same image, differing
only by memory + where the `INTERNAL_*_HANDLER_URL` env vars point. The *only*
fragile part is the OIDC-audience wiring — Cloud Tasks signs `audience =
INTERNAL_<x>_HANDLER_URL` (set by the WEB) and the WORKER verifies the token's
`aud` against the SAME env var. If a future edit points a heavy handler back at
the WEB url, or changes the WEB url host, or drops a handler env the code still
verifies against, the webhook/heat/artefact chain silently 401s or lands on the
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

# The three HEAVY Cloud Tasks handlers that MUST run on the 2Gi worker.
_HEAVY_HANDLER_ENVS = (
    "INTERNAL_HEAT_HANDLER_URL",
    "INTERNAL_ARTEFACT_HANDLER_URL",
    "INTERNAL_STRAVA_WEBHOOK_HANDLER_URL",
)
# The light handler that deliberately STAYS on the web (avoids re-pointing its
# Cloud Scheduler OIDC audience).
_LIGHT_HANDLER_ENV = "INTERNAL_STRAVA_HEALTH_HANDLER_URL"

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
    that URL is the OIDC audience the health-check scheduler + the domain
    mapping already point at. Changing its host = the 2026-06-04 401 loop."""
    # The script derives it from PROJECT_NUMBER; reconstruct + compare.
    proj_num = re.search(r'PROJECT_NUMBER="(\d+)"', deploy_sh)
    assert proj_num, "PROJECT_NUMBER not defined"
    expected = (
        f"https://common-trails-api-prod-{proj_num.group(1)}"
        ".europe-west1.run.app"
    )
    assert expected == _WEB_RUN_URL_LITERAL, (
        "WEB .run.app host changed — this breaks the health-check scheduler "
        "OIDC audience + the custom-domain mapping. If Cloud Run genuinely "
        "reassigned it, update _WEB_RUN_URL_LITERAL in this test WITH the "
        "matching re-issue of the scheduler audience."
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
    """Heat / artefact / webhook handler URLs must target WORKER_RUN_URL —
    Cloud Tasks binds the OIDC audience to this value, so it decides which
    service (and which memory tier) runs the heavy work."""
    value = _env_value(deploy_sh, env_key)
    assert value.startswith("${WORKER_RUN_URL}/"), (
        f"{env_key} must point at the WORKER (2Gi) service, got {value!r}. "
        "Pointing it at the web (RUN_URL) sends heavy work back onto the "
        "512Mi public service and defeats the split."
    )


def test_health_handler_stays_on_web(deploy_sh: str):
    """The daily health-check is light + Cloud-Scheduler-driven; it stays on
    the web (RUN_URL) so we don't have to re-point the scheduler audience."""
    value = _env_value(deploy_sh, _LIGHT_HANDLER_ENV)
    assert value.startswith("${RUN_URL}/"), (
        f"{_LIGHT_HANDLER_ENV} must stay on the WEB (RUN_URL), got {value!r}."
    )


def test_both_services_deployed_with_correct_memory(deploy_sh: str):
    """The split is only real if BOTH services actually get deployed, web at
    512Mi and worker at 2Gi."""
    assert re.search(
        r'deploy_service\s+"\$WEB_SERVICE"\s+"512Mi"', deploy_sh
    ), "WEB service must be deployed at 512Mi"
    assert re.search(
        r'deploy_service\s+"\$WORKER_SERVICE"\s+"2Gi"', deploy_sh
    ), "WORKER service must be deployed at 2Gi"


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
        "sanity: heat handler should verify INTERNAL_HEAT_HANDLER_URL"
    )

    deploy_keys = set(re.findall(r'^([A-Z0-9_]+):\s*"', deploy_sh, re.MULTILINE))
    required = verified_envs
    missing = required - deploy_keys
    assert not missing, (
        f"deploy-prod.sh does not set env(s) a /internal handler verifies "
        f"against: {sorted(missing)}. A handler verifying an unset audience "
        f"env 500s ('not configured for OIDC verification')."
    )
