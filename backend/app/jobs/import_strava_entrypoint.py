"""Bootstrap entrypoint for the Strava import Job.

Why this exists
---------------
The Cloud Run Job container that runs ``python -m app.jobs.import_strava``
can crash *before* user code reaches any normal exception handler — for
example when a top-level ``raise`` in a transitively-imported module
fires during Python's import machinery. The 2026-05-26 incident was
exactly this: ``app/api/auth.py:40`` raised ``RuntimeError("JWT_SECRET
must be set...")`` at module-import time, the process exited, the
container restarted three times, all retries crashed identically, and
the ``import_jobs`` row stayed forever on ``status='RUNNING'`` because
nothing in the user code path got to update it. The per-user queue
lock then blocked every subsequent import for that user.

The fix: a wrapper module that
1. Imports only stdlib + ``sqlalchemy.create_engine`` (extremely
   unlikely to fail in a container that successfully built).
2. Captures ``IMPORT_JOB_ID`` from the environment before doing
   anything else.
3. Tries to import + run ``app.jobs.import_strava.main`` inside a
   ``try/except``.
4. On *any* exception — including ``RuntimeError`` raised at import
   time of a downstream module — opens a direct DB connection via
   ``DATABASE_URL`` and writes ``status='FAILED'`` + the traceback
   to the row identified by ``IMPORT_JOB_ID``, then exits 1.

Defense in depth: ``app/jobs/import_strava.py`` still has its own
try/finally around the normal happy path. This wrapper only catches
what escapes that — boot-time crashes and unhandled re-raises.

What this *cannot* catch:
- OOM kills (kernel terminates Python with SIGKILL — no signal handler
  can intercept it)
- Container quota/scheduler-side terminations
- DATABASE_URL missing or unreachable (the emergency write itself fails)

Those cases still need the reactive zombie cleanup at the *next* Job
launch (also part of this PR — see ``run_import`` in import_strava.py).
"""
from __future__ import annotations

import logging
import os
import sys
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _mark_failed_via_direct_sql(job_id: str, error_msg: str) -> None:
    """Emergency mark-FAILED that bypasses every ``app.*`` import.

    Uses only ``sqlalchemy.create_engine`` + the ``DATABASE_URL`` env
    var, because those are the only things we can trust haven't been
    poisoned by whatever crashed at boot.

    Failures here are logged but never re-raised — there's nothing else
    to fall back to, and we don't want to mask the original crash in
    the container exit code.
    """
    try:
        from sqlalchemy import create_engine, text
    except Exception as e:
        log.error("Emergency cleanup: sqlalchemy import failed: %s", e)
        return

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log.error("Emergency cleanup: DATABASE_URL missing — cannot mark job FAILED")
        return

    try:
        # connect_timeout=10 prevents an unreachable Cloud SQL VPC from
        # hanging this emergency path for minutes — at which point the
        # container's outer 2-h Job timeout would kill us before we
        # could mark the row FAILED, defeating the entire purpose of
        # the wrapper. The whole point is to fail fast and write the
        # row, even when something is wrong with the network.
        # pool_pre_ping is dead code on a fresh single-use engine
        # (first connect is always new), so we drop it.
        engine = create_engine(
            db_url,
            connect_args={"connect_timeout": 10},
        )
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE import_jobs
                    SET status = 'FAILED',
                        last_error = :err,
                        updated_at = NOW()
                    WHERE id = :job_id AND status IN ('PENDING', 'RUNNING')
                    """
                ),
                # Truncate the error so an enormous traceback can't
                # blow out the TEXT column (Postgres TEXT has no hard
                # limit but readability dies past ~2 KB anyway).
                {"err": error_msg[:1500], "job_id": job_id},
            )
        engine.dispose()
        log.warning("Emergency-marked job %s as FAILED", job_id)
    except Exception as e:
        log.error("Emergency cleanup: DB write failed: %s", e)


def _bootstrap() -> None:
    job_id = os.environ.get("IMPORT_JOB_ID", "")
    if not job_id:
        log.error("IMPORT_JOB_ID env var missing — cannot run import")
        sys.exit(1)

    try:
        # Heavy imports happen inside the try block so a module-level
        # RuntimeError in any of app.api.auth, app.db.session, etc.
        # is caught here instead of killing the process.
        from app.jobs.import_strava import main as _main
        _main()
    except SystemExit:
        # main() may exit explicitly on completion or fast-failure
        # (e.g. job not found). Let it propagate — exit code is
        # already meaningful.
        raise
    except BaseException as exc:  # noqa: BLE001 — last-resort net
        err_summary = f"BOOT/RUNTIME crash: {type(exc).__name__}: {exc}"
        tb = traceback.format_exc()
        log.error("%s\n%s", err_summary, tb)
        _mark_failed_via_direct_sql(job_id, f"{err_summary}\n\n{tb}")
        sys.exit(1)


if __name__ == "__main__":
    _bootstrap()
