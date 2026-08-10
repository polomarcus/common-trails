---
title: 'Monthly Strava Auto-Resync'
slug: 'monthly-strava-auto-resync'
created: '2026-03-22'
updated: '2026-03-22'
status: 'complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['FastAPI', 'SQLAlchemy', 'Cloud Run Job', 'Cloud Scheduler', 'Terraform', 'Strava OAuth2']
files_to_modify:
  - 'backend/app/db/models.py'
  - 'backend/app/jobs/resync_strava.py'
  - 'backend/app/api/integrations_strava.py'
  - 'backend/app/api/admin.py'
  - 'backend/alembic/versions/0023_add_last_synced_at.py'
  - 'infra/terraform/main.tf'
  - 'frontend/app/map/page.tsx'
  - 'frontend/app/admin/page.tsx'
code_patterns: ['Cloud Run Job entrypoint', 'Strava OAuth token refresh', 'Alembic migration', 'Terraform Cloud Scheduler']
test_patterns: ['pytest integration', 'TEST_MODE stub']
review_findings_addressed: 12
---

# Tech-Spec: Monthly Strava Auto-Resync

**Created:** 2026-03-22 | **Reviewed:** 2026-03-22

## Overview

### Problem Statement

Users' Strava data goes stale after initial import. New activities are only imported when the user manually triggers a sync. This means the community heatmap doesn't reflect recent rides, and users must remember to come back and click "Synchroniser Strava" regularly.

### Solution

A dedicated Cloud Run Job (`resync-strava`) triggered monthly by Cloud Scheduler that:
1. Acquires a DB-level advisory lock to prevent concurrent runs
2. Iterates all users with a valid Strava `IntegrationAccount`
3. Refreshes expired OAuth tokens via Strava's `refresh_token` grant
4. Incrementally imports only new activities (using Strava API `after` parameter with `last_synced_at` timestamp)
5. Processes users in small batches with delays to respect Strava's rate limits (100 req/15min, 1000/day)
6. Records `last_synced_at` on `IntegrationAccount` for incremental tracking
7. Caps per-user API pages to prevent a single heavy user from exhausting rate budget

Users see "Dernière mise à jour le X" in the account menu. Admins see a sync health dashboard with per-user sync status, delay, and a manual "resync now" trigger.

### Scope

**In Scope:**
- `last_synced_at` + `sync_failures` fields on `IntegrationAccount` (Alembic migration)
- Strava OAuth token refresh logic (reusable helper)
- New Cloud Run Job entrypoint: `app/jobs/resync_strava.py`
- Batch processing with rate-limit-aware delays + per-user page cap
- DB advisory lock to prevent concurrent resync runs
- Terraform: new `google_cloud_run_v2_job` + `google_cloud_scheduler_job` + API enablement
- User-facing "Dernière mise à jour le X" label in account menu
- Admin endpoint `GET /admin/sync-status` + `POST /admin/resync` + frontend admin panel section
- Update `ImportJob` model usage for tracking resync jobs

**Out of Scope:**
- Strava webhooks (push-based real-time sync)
- Time-filtered heatmap (last 30/60 days) — depends on heatmap refactor
- Email/push notifications on sync failure
- Garmin/Komoot auto-resync (future — same pattern)

## Context for Development

### Codebase Patterns

- **Cloud Run Job pattern**: `app/jobs/import_strava.py` is the reference — standalone entrypoint that reads env vars, connects to DB, runs async logic. Called via `python -m app.jobs.resync_strava`.
- **Strava API auth**: tokens stored in `IntegrationAccount` with `access_token`, `refresh_token`, `expires_at`. Current code does NOT auto-refresh — a 401 marks the job as FAILED.
- **Import flow**: `_run_strava_import()` in `integrations_strava.py` (in-process) and `run_import()` in `jobs/import_strava.py` (Cloud Run Job) are parallel implementations. The resync job should reuse the Cloud Run Job pattern.
- **Strava API pagination**: `GET /api/v3/athlete/activities?page=N&per_page=100&after=EPOCH` — the `after` param filters to activities after a Unix timestamp.
- **Strava rate limits**: 100 requests per 15 minutes, 1000 per day. Each user's discovery phase takes 1+ requests (1 per 100 activities). GPS upgrade phase takes 1 request per activity.
- **Admin pattern**: `app/api/admin.py` has `require_admin` dependency, `GET /admin/dashboard` returns stats dict. Frontend at `app/admin/page.tsx`.
- **Terraform pattern**: existing `google_cloud_run_v2_job.import_strava` in `main.tf` — new job follows same structure.
- **`ingest_activity()` session**: opens its own `SessionLocal()` internally (ingest.py:963). The resync job must update `last_synced_at` only AFTER all `ingest_activity()` calls for that user succeed, since they use separate DB sessions.

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `backend/app/jobs/import_strava.py` | Existing Cloud Run Job for single-user import — template for resync job |
| `backend/app/api/integrations_strava.py` | Strava OAuth, token exchange, `_run_strava_import()`, `STRAVA_TOKEN_URL` |
| `backend/app/db/models.py:214-228` | `IntegrationAccount` model — add `last_synced_at`, `sync_failures` |
| `backend/app/db/models.py:231-251` | `ImportJob` model — reuse for tracking resync jobs |
| `backend/app/services/ingest.py:938` | `ingest_activity()` — reuse, note: owns its own DB session |
| `backend/app/api/admin.py` | Admin dashboard endpoint — add sync status + manual resync trigger |
| `infra/terraform/main.tf:61-72` | `local.required_apis` — must add `cloudscheduler.googleapis.com` |
| `infra/terraform/main.tf:532-604` | Existing Cloud Run Job terraform — template for resync job |
| `frontend/app/map/page.tsx:7010-7030` | User menu button — add "dernière MAJ" |
| `frontend/app/admin/page.tsx` | Admin dashboard frontend |

### Technical Decisions

1. **Separate Cloud Run Job** (not reuse `import_strava`): The resync job iterates ALL users, handles token refresh, and manages batch timing. Different enough from single-user import to warrant its own entrypoint.
2. **Incremental via `after` param**: Strava's `GET /athlete/activities?after=EPOCH` returns only activities after the timestamp. Combined with `last_synced_at`, this minimizes API calls.
3. **Token refresh before import**: Call `POST https://www.strava.com/oauth/token` with `grant_type=refresh_token` when `expires_at < now()`. Update stored tokens in DB. Skip user if refresh fails (token revoked).
4. **Batch size = 5 users, 60s pause between batches**: Conservative to stay well within Strava's 100 req/15min. Each user needs ~1-3 requests for discovery (capped at MAX_PAGES_PER_USER=5, i.e. 500 activities max per resync).
5. **`last_synced_at` on IntegrationAccount** (not ImportJob): This is the canonical "when did we last successfully sync this user" — simpler than querying the latest completed ImportJob.
6. **GPS upgrade skipped in resync**: Monthly resync only imports activity metadata + polyline geometry. Full GPS stream upgrade is expensive (1 req/activity) and can be done on-demand by the user. This keeps the batch fast and within rate limits.
7. **DB advisory lock** (`pg_try_advisory_lock(hash)`) at job start prevents overlapping resync runs if Cloud Scheduler fires while a previous run is still going. If lock not acquired, exit immediately with log.
8. **`sync_failures` counter**: Tracks consecutive failed refresh attempts per user. Users with `sync_failures >= 3` are skipped in future resyncs (dead tokens). Counter resets to 0 on successful sync. Admin can see this in the dashboard.
9. **Keep provider = `"strava"`** on ImportJob (not `"strava-resync"`): The existing running-job guard in `/import_all` checks `provider == "strava" AND status == "RUNNING"`. Using the same provider prevents a manual import and a resync from running simultaneously for the same user. Add a `source` field or use the cursor JSON to distinguish resync from manual.

## Implementation Plan

### Tasks

#### Task 1: Alembic migration — add `last_synced_at` + `sync_failures` to `IntegrationAccount`

**File:** `backend/alembic/versions/0023_add_last_synced_at.py`

```python
def upgrade():
    # Add last_synced_at (DateTime with timezone, nullable) to integration_accounts
    op.add_column("integration_accounts",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("integration_accounts",
        sa.Column("sync_failures", sa.Integer, server_default="0", nullable=False))

    # Backfill last_synced_at from the latest COMPLETED import job per user,
    # falling back to integration_accounts.created_at
    op.execute("""
        UPDATE integration_accounts ia
        SET last_synced_at = COALESCE(
            (SELECT MAX(ij.updated_at)
             FROM import_jobs ij
             WHERE ij.user_id = ia.user_id
               AND ij.provider = 'strava'
               AND ij.status = 'COMPLETED'),
            ia.created_at
        )
        WHERE ia.provider = 'strava'
          AND ia.last_synced_at IS NULL
    """)

def downgrade():
    op.drop_column("integration_accounts", "sync_failures")
    op.drop_column("integration_accounts", "last_synced_at")
```

**File:** `backend/app/db/models.py` line ~226

Add to `IntegrationAccount`:
```python
last_synced_at = Column(DateTime(timezone=True), nullable=True)
sync_failures = Column(Integer, default=0, server_default="0", nullable=False)
```

#### Task 2: Token refresh helper

**File:** `backend/app/api/integrations_strava.py`

Create a reusable function:
```python
async def refresh_strava_token(acct: IntegrationAccount, db: Session) -> str | None:
    """Refresh Strava OAuth token if expired. Returns valid access_token or None.

    Updates acct.access_token, refresh_token, expires_at in DB on success.
    Returns None on failure (token revoked, network error).
    """
    import time
    if acct.expires_at and acct.expires_at > time.time() + 300:  # 5min buffer
        return acct.access_token

    if not acct.refresh_token:
        return None

    if TEST_MODE:
        # Stub: pretend token is refreshed
        acct.expires_at = int(time.time()) + 3600
        db.commit()
        return acct.access_token

    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(STRAVA_TOKEN_URL, data={
                "client_id": STRAVA_CLIENT_ID,
                "client_secret": STRAVA_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": acct.refresh_token,
            }, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None

    acct.access_token = data["access_token"]
    acct.refresh_token = data.get("refresh_token", acct.refresh_token)
    acct.expires_at = data.get("expires_at")
    db.commit()
    return acct.access_token
```

Also use this in the existing `_run_strava_import()` and `run_import()` on 401 response — attempt one refresh before marking as FAILED (bonus improvement, not strictly required for this spec).

#### Task 3: Cloud Run Job entrypoint — `resync_strava.py`

**File:** `backend/app/jobs/resync_strava.py`

New standalone entrypoint (`python -m app.jobs.resync_strava`):

```
Flow:
1. Connect to DB
2. Acquire advisory lock: SELECT pg_try_advisory_lock(hashtext('resync_strava'))
   - If False: log "Another resync is running, exiting" and exit(0)
3. Query all IntegrationAccount WHERE provider = 'strava'
     AND refresh_token IS NOT NULL
     AND sync_failures < 3
4. Process in batches of BATCH_SIZE (env var, default 5)
5. For each user in batch:
   a. Call refresh_strava_token() — if None:
      - Increment acct.sync_failures, db.commit()
      - Log warning, skip user
   b. Create ImportJob(provider="strava", status="RUNNING")
      - Store {"source": "resync"} in cursor JSON to distinguish from manual
   c. Fetch activities with after=int(last_synced_at.timestamp()) (or 0 if NULL)
      - Cap at MAX_PAGES_PER_USER=5 (500 activities) to bound rate usage
      - If NULL last_synced_at: log warning "first resync for user X, capped at 500 activities"
   d. Ingest new activities via ingest_activity() (polyline only, no GPS upgrade, no photos)
   e. On success:
      - Update IntegrationAccount.last_synced_at = now()
      - Reset acct.sync_failures = 0
      - Mark ImportJob as COMPLETED
   f. On failure:
      - Keep last_synced_at unchanged (so next resync retries from same point)
      - Mark ImportJob as FAILED with last_error
6. Between batches: sleep BATCH_DELAY_SECONDS (env var, default 60)
7. On Strava 429 (rate limited): sleep for Retry-After header value (default 900s), then continue
8. Release advisory lock (automatic on session close)
9. Log summary: total users, synced, skipped (failures>=3), failed, new activities imported
```

Key details:
- On 401 AFTER token refresh succeeded: this means the fresh token was immediately invalid — increment `sync_failures`, skip user
- `ingest_activity()` owns its own DB session — `last_synced_at` update must happen in the resync job's session AFTER all activities for that user are ingested
- TEST_MODE: skip Strava API calls, create stub ImportJob with status=COMPLETED, still update last_synced_at

#### Task 4: Update user-facing "dernière MAJ" in account menu

**File:** `backend/app/api/integrations_strava.py` — update `/status` endpoint

Add `last_synced_at` (ISO string or null) to the status response dict.

**File:** `frontend/app/map/page.tsx`

1. Fetch `last_synced_at` from `GET /integrations/strava/status` response (already fetched on mount)
2. Store in state: `const [lastSyncedAt, setLastSyncedAt] = useState<string | null>(null)`
3. Display below the Strava button in the user menu dropdown:
   ```
   Dernière mise à jour le {new Date(lastSyncedAt).toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' })}
   ```
   In small grey text (fontSize 11, color #888), only if `lastSyncedAt` is not null.

#### Task 5: Admin sync status endpoint + manual trigger + frontend

**File:** `backend/app/api/admin.py`

**Endpoint 1:** `GET /admin/sync-status`
```python
@router.get("/sync-status")
async def admin_sync_status(
    _admin: Annotated[AuthenticatedUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Per-user Strava sync status for admin monitoring."""
    from app.db.models import IntegrationAccount
    import time

    accounts = db.query(IntegrationAccount).filter(
        IntegrationAccount.provider == "strava"
    ).all()

    now = datetime.now(UTC)
    users = []
    for acct in accounts:
        delay_days = (now - acct.last_synced_at).days if acct.last_synced_at else None
        users.append({
            "athlete_name": acct.athlete_name or "Unknown",
            "last_synced_at": acct.last_synced_at.isoformat() if acct.last_synced_at else None,
            "delay_days": delay_days,
            "sync_failures": acct.sync_failures,
            "token_expired": bool(acct.expires_at and acct.expires_at < time.time()),
            "disabled": acct.sync_failures >= 3,
        })

    # Sort: most stale first
    users.sort(key=lambda u: u["delay_days"] if u["delay_days"] is not None else 9999, reverse=True)

    return {
        "total_connected": len(users),
        "stale_30d": sum(1 for u in users if (u["delay_days"] or 9999) > 30),
        "token_expired": sum(1 for u in users if u["token_expired"]),
        "disabled": sum(1 for u in users if u["disabled"]),
        "users": users,
    }
```

Note: no `user_id` in response — admin sees athlete_name + status only (no PII leak).

**Endpoint 2:** `POST /admin/resync`
```python
@router.post("/resync")
async def admin_trigger_resync(
    _admin: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> dict:
    """Manually trigger the resync Cloud Run Job."""
    # Reuse _trigger_cloud_run_job() pattern from integrations_strava.py
    # but target the resync job name instead of import job
    # Falls back to BackgroundTasks in dev
    from app.api.integrations_strava import GCP_PROJECT, GCP_REGION
    import os
    resync_job_name = os.environ.get("RESYNC_JOB_NAME", "common-trails-resync-strava-prod")
    # ... same Cloud Run REST API trigger pattern ...
    return {"status": "triggered"}
```

**File:** `frontend/app/admin/page.tsx`

Add a "Sync Status" section to the admin dashboard:
- Summary cards: total connected, stale >30d, expired tokens, disabled (failures >= 3)
- Table: athlete name, last synced date, delay (days), sync_failures count, token status (green/red badge), disabled badge
- "Resync Now" button that calls `POST /admin/resync`

#### Task 6: Terraform — Cloud Run Job + Cloud Scheduler + API enablement

**File:** `infra/terraform/main.tf`

**6a. Enable Cloud Scheduler API** (add to `local.required_apis` at line 61):
```hcl
"cloudscheduler.googleapis.com",
```

**6b. New Cloud Run Job** `resync-strava` (after the existing `import_strava` job block):
```hcl
resource "google_cloud_run_v2_job" "resync_strava" {
  depends_on = [google_project_service.apis]
  name       = "common-trails-resync-strava-${var.env}"
  location   = var.region

  template {
    template {
      service_account = google_service_account.jobs.email
      max_retries     = 0  # No retries — advisory lock prevents concurrent runs anyway
      timeout         = "7200s"  # 2 hours (batches of 5 users × 60s delays)

      containers {
        image = var.api_image

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }

        command = ["python", "-m", "app.jobs.resync_strava"]

        env {
          name  = "BATCH_SIZE"
          value = "5"
        }

        env {
          name  = "BATCH_DELAY_SECONDS"
          value = "60"
        }

        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.secret_id
              version = "latest"
            }
          }
        }

        env {
          name = "STRAVA_CLIENT_ID"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.strava_client_id.secret_id
              version = "latest"
            }
          }
        }

        env {
          name = "STRAVA_CLIENT_SECRET"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.strava_client_secret.secret_id
              version = "latest"
            }
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.main.connection_name]
        }
      }
    }
  }

  labels = {
    env     = var.env
    purpose = "resync"
  }
}
```

**6c. Cloud Scheduler:**
```hcl
resource "google_cloud_scheduler_job" "resync_strava_monthly" {
  depends_on = [google_project_service.apis]
  name       = "resync-strava-monthly-${var.env}"
  region     = var.region
  schedule   = "0 3 1 * *"  # 1st of every month at 3 AM
  time_zone  = "Europe/Paris"

  http_target {
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.resync_strava.name}:run"
    http_method = "POST"
    oauth_token {
      service_account_email = google_service_account.jobs.email
    }
  }
}
```

### Acceptance Criteria

**AC1: Token refresh works**
- Given a user with an expired Strava token (`expires_at < now()`)
- When the resync job processes this user
- Then it calls `POST /oauth/token` with `grant_type=refresh_token`, updates stored tokens, and proceeds with import

**AC2: Incremental import uses `after` param**
- Given a user with `last_synced_at = 2026-03-01T00:00:00Z`
- When the resync job fetches their activities
- Then the Strava API call includes `after=1772006400` (epoch of 2026-03-01)
- And only activities after that date are imported

**AC3: Batch rate limiting respected**
- Given 12 Strava-connected users (all with sync_failures < 3)
- When the resync job runs with BATCH_SIZE=5
- Then it processes users in 3 batches (5, 5, 2) with 60s pauses between batches

**AC4: Failed token refresh increments sync_failures**
- Given a user whose Strava refresh token was revoked
- When token refresh returns 400/401
- Then `sync_failures` is incremented, user is skipped, logged as warning, job continues to next user

**AC5: Users with sync_failures >= 3 are skipped**
- Given a user with `sync_failures = 3`
- When the resync job queries eligible users
- Then this user is excluded from the batch

**AC6: last_synced_at updated only on success**
- Given a successful incremental import for a user
- When the import completes with no errors
- Then `IntegrationAccount.last_synced_at` is set to `now()` and `sync_failures` reset to 0
- Given a failed import (Strava API error mid-import)
- Then `last_synced_at` remains unchanged (next run retries from the same point)

**AC7: Per-user page cap prevents rate exhaustion**
- Given a user with NULL `last_synced_at` and 2000 activities on Strava
- When the resync job processes this user
- Then it fetches at most MAX_PAGES_PER_USER=5 pages (500 activities)
- And logs a warning that the user was capped

**AC8: Concurrent runs prevented**
- Given a resync job already running (holding the advisory lock)
- When Cloud Scheduler fires again (or admin triggers manually)
- Then the second instance fails to acquire the lock, logs a message, and exits cleanly

**AC9: User sees last sync date**
- Given a user with `last_synced_at = 2026-03-15`
- When they open the account menu on /map
- Then they see "Dernière mise à jour le 15 mars 2026" near the Strava section

**AC10: Admin sees sync health**
- Given an admin viewing /admin
- When the sync status section loads
- Then they see a table with: athlete name, last synced date, delay in days, sync_failures, token status
- And summary counts: total connected, stale >30d, expired tokens, disabled
- And no user_id UUIDs are exposed in the response

**AC11: Admin can trigger manual resync**
- Given an admin on the /admin page
- When they click "Resync Now"
- Then `POST /admin/resync` triggers the Cloud Run Job (or background task in dev)

**AC12: Cloud Scheduler triggers monthly**
- Given the Terraform is deployed with `cloudscheduler.googleapis.com` enabled
- When the 1st of the month arrives at 3 AM Paris time
- Then Cloud Scheduler triggers the `resync-strava` Cloud Run Job

**AC13: TEST_MODE safety**
- Given `TEST_MODE=true`
- When the resync job runs
- Then no external HTTP calls are made (Strava API is not contacted)
- And `refresh_strava_token()` returns a stub token without network call

## Additional Context

### Dependencies

- **Strava API**: `GET /api/v3/athlete/activities` with `after` param, `POST /oauth/token` with `grant_type=refresh_token`
- **GCP**: Cloud Run Jobs API, Cloud Scheduler API (`cloudscheduler.googleapis.com` — must be added to `required_apis`)
- **Heatmap refactor**: The resync job will contribute to heatmap via existing `ingest_activity()` → `_update_heat_edges()`. When the heatmap edge clustering refactor lands, resync will automatically benefit.

### Testing Strategy

- **Unit/integration tests** (`backend/tests/test_resync_strava.py`):
  - Test `refresh_strava_token()` with mock httpx: expired token → refreshed, revoked token → returns None
  - Test `refresh_strava_token()` in TEST_MODE: returns stub without network
  - Test resync job in TEST_MODE: no external calls, creates ImportJob, updates last_synced_at
  - Test incremental logic: mock IntegrationAccount with last_synced_at, verify `after` param sent
  - Test batch splitting: 12 users with BATCH_SIZE=5 → 3 batches
  - Test sync_failures >= 3 exclusion
  - Test per-user page cap (MAX_PAGES_PER_USER=5)
  - Test advisory lock: simulate lock held → job exits cleanly
- **Admin endpoint tests**:
  - `GET /admin/sync-status` returns correct structure, requires admin, no user_id in response
  - `POST /admin/resync` requires admin, returns status
- **No E2E test needed**: Cloud Scheduler → Cloud Run Job is infra-level, tested via Terraform plan

### Notes

- **Future: Strava webhooks** — Strava offers webhook subscriptions (`POST /api/v3/push_subscriptions`) for real-time activity create/update/delete events. This would replace monthly polling with instant sync. Consider as a future enhancement once user base grows.
- **Future: Garmin/Komoot resync** — Same pattern applies. The resync job could be generalized with a `provider` param.
- **Monthly schedule is conservative** — Can be changed to weekly via Terraform `schedule` field if users want fresher data. The batch delays ensure rate limits are respected regardless of frequency.
- **Cost**: Cloud Scheduler = free (up to 3 jobs). Cloud Run Job = pay per execution (~$0.01/run for small user base).
- **Recovering disabled users**: Users with `sync_failures >= 3` can be re-enabled by reconnecting Strava (which resets the IntegrationAccount) or via a future admin action to reset `sync_failures`.
