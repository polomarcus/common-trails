"""Strava job-lifecycle hygiene — PR D of the strava-tough-review audit.

Covers:
- /import_all queue guard is user-scoped (Sev-2 #5)
- Phase 1 metadata pagination has a hard ceiling (High #5)
- OAuth state TTL constant is the single source of truth (Sev-2 #6 drift)

The stale-RUNNING cutoff change (Sev-2 #4) is clock-dependent and
deliberately not asserted here — the constant change is reviewable.
"""
import re

import app.api.integrations_strava as strava_mod

# ── /import_all queue guard is per-user ──────────────────────────────────────


class TestImportAllUserScopedQueue:
    """Audit Sev-2 #5: the Cloud Run Job's queue guard must filter by user_id
    so user A's RUNNING import doesn't force user B's job into a 6h wait.

    A full end-to-end DB integration test is intentionally not included here
    because the ImportJob ORM model is migration-tied and pre-existing test
    suites have known column-drift failures (`gps_total`, `skip_gps_upgrade`).
    The source-grep below pins the SQL shape — that's the actual invariant.
    """

    def test_job_runner_queue_guard_filters_by_user(self):
        """The SQL in import_strava.py:run filters RUNNING jobs by user_id.

        Regression test for Sev-2 #5: prior code was global, so any other
        user's RUNNING job would force a 6h queue wait.
        """
        from pathlib import Path
        src = Path(__file__).resolve().parents[1] / "app/jobs/import_strava.py"
        text = src.read_text()
        # The queue-guard SELECT must filter by user_id AND status, not status alone.
        assert re.search(
            r"SELECT\s+id\s+FROM\s+import_jobs\s+WHERE\s+user_id\s*=\s*:uid\s+AND\s+status\s*=\s*'RUNNING'",
            text,
        ), "queue guard SQL must be user-scoped (WHERE user_id = :uid AND status = 'RUNNING')"


# ── Phase 3 reuses the API path's short-session helper (no local duplicate) ─


class TestJobPhase3UsesApiHelper:
    """2026-05-27 OOM regression guard.

    The Cloud Run Job used to define its own `_run_gps_upgrade` that
    held the outer DB session across `await` AND ran `_update_heat_edges`
    inline serial. The API path had already been refactored (PR #314 +
    PR-E) to short-lived sessions per batch + Cloud-Tasks-deferred heat
    compute, but the Job's stale copy was the one that hit OOM on a
    1449-activity re-import. Fix: delete the local copy, reuse
    `app.api.integrations_strava._run_gps_upgrade`.

    This test locks in the structural property so a future "let's just
    duplicate it locally for clarity" PR can't re-introduce the bug.
    See [[project_ingestion_audit_2026_05_27]] S1.1.
    """

    def test_job_does_not_redefine_run_gps_upgrade(self):
        """`app.jobs.import_strava._run_gps_upgrade` must NOT exist as a
        module-level function. The Job imports it from
        `app.api.integrations_strava` inside `run_import`."""
        import app.jobs.import_strava as job_mod
        assert not hasattr(job_mod, "_run_gps_upgrade"), (
            "app/jobs/import_strava.py must NOT redefine _run_gps_upgrade — "
            "import it from app.api.integrations_strava (short-session + "
            "Cloud-Tasks heat-compute pattern). 2026-05-27 OOM regression."
        )

    def test_job_imports_run_gps_upgrade_from_api(self):
        """The string `from app.api.integrations_strava import` must be
        present near the Phase 3 call site, importing `_run_gps_upgrade`."""
        from pathlib import Path
        src = Path(__file__).resolve().parents[1] / "app/jobs/import_strava.py"
        text = src.read_text()
        assert re.search(
            r"from\s+app\.api\.integrations_strava\s+import\s+\([^)]*_run_gps_upgrade",
            text,
            re.DOTALL,
        ) or re.search(
            r"from\s+app\.api\.integrations_strava\s+import\s+[^,\n]*_run_gps_upgrade",
            text,
        ), (
            "Job must import _run_gps_upgrade from app.api.integrations_strava — "
            "do not reintroduce the local copy."
        )


# ── Phase 1 pagination has a max-page ceiling ────────────────────────────────


class TestPhase1PageCap:
    """A malformed Strava response that never decrements page size must
    not spin forever. _PHASE1_MAX_PAGES = 250 bounds the loop."""

    def test_phase1_max_pages_constant_set(self):
        assert hasattr(strava_mod, "_PHASE1_MAX_PAGES")
        # 250 pages × 200 per_page = 50k activities — well above any real account.
        assert strava_mod._PHASE1_MAX_PAGES == 250

    def test_phase1_loop_has_page_cap_guard(self):
        """Source-level check: _run_strava_import's Phase 1 while-loop must
        compare `page > _PHASE1_MAX_PAGES` and `break`. Regression test for
        the unbounded-loop bug (audit High #5)."""
        from pathlib import Path
        src = Path(strava_mod.__file__).read_text()
        # Locate _run_strava_import and check the Phase 1 cap guard sits inside.
        m = re.search(
            r"async def _run_strava_import\([\s\S]+?(?=\nasync def |\ndef |\nclass |\n@router\.)",
            src,
        )
        assert m, "could not locate _run_strava_import in source"
        body = m.group(0)
        assert "page > _PHASE1_MAX_PAGES" in body, (
            "Phase 1 loop must guard against unbounded pagination: "
            "`if page > _PHASE1_MAX_PAGES: break`"
        )
        # A capture_message call (or at least a log.error) must fire so the
        # rare cap-hit case is observable in prod.
        assert "Phase 1 hit page cap" in body, (
            "Phase 1 cap must log/capture when hit so it's observable"
        )


# ── OAuth state TTL — constant is the single source of truth ────────────────


class TestOAuthStateTTL:
    """OAuth state is now a signed JWT (PR #349) rather than a DB row.
    The constant stays — it bounds the JWT's `exp` claim. The two
    previous tests (SQL-literal drift, DB round-trip) are now in
    `test_strava_oauth_state_jwt.py` which exercises the JWT helpers
    directly."""

    def test_constant_is_30_minutes(self):
        assert strava_mod._OAUTH_STATE_TTL == 1800
