"""Bootstrap entrypoint for the Strava monthly resync Job.

Mirrors ``import_strava_entrypoint.py`` but adapted for resync semantics:

- The resync Job has no ``IMPORT_JOB_ID`` env var because it operates on
  *all* connected accounts (monthly cron), not a single user-triggered
  job. So we can't write a row to FAILED — there is no row.
- Instead, on boot-time crash we capture the traceback to Sentry (if
  configured) and exit 1 with a clear log message. The monthly cron
  itself will retry next month, but if the bug is repeatable Sentry
  will see it before then.

Why this exists in the same shape as the import entrypoint: defense in
depth for the same class of crashes (module-import RuntimeError, missing
env, ImportError). The 2026-05-26 ``JWT_SECRET`` incident affected
*both* Jobs; only the user-facing one was a blocker (silent zombie row),
but the resync would have died silently too if it had run that day.
"""
from __future__ import annotations

import logging
import os
import sys
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _init_sentry_lazy() -> bool:
    """Initialise Sentry from the SENTRY_DSN env var, if present.

    `app/main.py` is where `sentry_sdk.init` runs for the api service,
    but Cloud Run Jobs never import `app.main` — so a Job that crashes
    at import time has *no Sentry client*. Without this lazy init, the
    `sentry_sdk.capture_exception` call below silently no-ops (the SDK
    has a default no-op transport when uninitialised).

    The init also benefits the per-user `except` block in
    `resync_strava.py:_resync_user` — that one was also dead until
    today. Both now route through this lazy init.

    Returns True if Sentry is configured and ready, False otherwise
    (no DSN, SDK missing, or init raised). Caller may then choose to
    rely purely on stderr logging.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("ENV", "production"),
            # Boot-time crashes are not performance traces; cheap defaults.
            traces_sample_rate=0.0,
        )
        return True
    except Exception as init_exc:
        log.error("Sentry lazy init failed: %s", init_exc)
        return False


def _capture_to_sentry(exc: BaseException, traceback_str: str) -> None:
    """Best-effort Sentry capture for a boot-time crash.

    Imported lazily because ``sentry_sdk`` could itself be the thing
    that crashed at import time. Failure here is logged but never
    re-raised.
    """
    if not _init_sentry_lazy():
        log.error("Sentry not configured — relying on stderr log only "
                  "(original error: %s\n%s)", exc, traceback_str)
        return
    try:
        import sentry_sdk
        sentry_sdk.set_tag("strava.phase", "resync_boot_crash")
        sentry_sdk.capture_exception(exc)
    except Exception as cap_exc:
        log.error("Sentry capture failed: %s\n(original error: %s\n%s)",
                  cap_exc, exc, traceback_str)


def _bootstrap() -> None:
    try:
        # Heavy imports inside try — module-level RuntimeError in any
        # transitive dependency is caught here.
        from app.jobs.resync_strava import main as _main
        _main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 — last-resort net
        err = f"resync_strava boot/runtime crash: {type(exc).__name__}: {exc}"
        tb = traceback.format_exc()
        log.error("%s\n%s", err, tb)
        _capture_to_sentry(exc, tb)
        # ENV is sometimes useful in the exit log for log-based alerting.
        log.error("ENV=%s exiting 1", os.environ.get("ENV", "<unset>"))
        sys.exit(1)


if __name__ == "__main__":
    _bootstrap()
