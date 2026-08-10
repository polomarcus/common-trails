"""Strava OAuth integration — official API only.

LEGAL NOTICE:
- Uses Strava official OAuth 2.0 Authorization Code flow only.
- Imports personal activities ONLY (no Strava heatmap — proprietary, forbidden).
- TEST_MODE=true: all responses are stubs, no network calls are made.
- ENABLE_STRAVA_INTEGRATION=false: all endpoints return 501.
"""
import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from typing import Annotated
from urllib.parse import urlencode

import sentry_sdk
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import (
    AuthenticatedUser,
    _create_token,
    _decode_token,
    _set_auth_cookie,
    get_current_user,
    user_exists,
)
from app.api.schemas import DisconnectResponse, ImportJobResponse
from app.config import FRONTEND_URL, TEST_MODE
from app.db.models import Activity, ActivityPhoto, ImportJob, IntegrationAccount, User
from app.db.session import SessionLocal, get_db
from app.services.secrets import decrypt_token, encrypt_token
from app.services.strava_client import (
    RATELIMIT_DAY_CAP_THRESHOLD_S,
    STRAVA_API_BASE,
    StravaRateLimited,
    cached_count_athlete_activities,
    get_activity_photos,
    get_activity_stream,
    get_http_client,
    stream_to_geojson,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["strava"])

# ── Config ───────────────────────────────────────────────────────────────────

STRAVA_ENABLED = os.environ.get("ENABLE_STRAVA_INTEGRATION", "false").lower() == "true"

STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
STRAVA_REDIRECT_URI = os.environ.get(
    "STRAVA_REDIRECT_URI", "http://localhost:8787/integrations/strava/callback"
)
STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"

# ── Reconnect-required threshold ──────────────────────────────────────────────
# After this many CONSECUTIVE definitive auth failures (Strava 400/401 on the
# token endpoint = revoked / expired refresh_token), the account is treated as
# needing a fresh OAuth. At the crossing we emit exactly ONE
# `strava_reconnect_required` notification (see `record_refresh_failure`), and
# `GET /integrations/strava/status` starts returning `reconnect_required=true`.
# Any successful sync resets `sync_failures = 0` (see `_mark_account_synced`),
# which re-arms the one-shot emit. Set >1 so a single transient Strava 5xx that
# happens to surface as a 401 doesn't nag the user; 3 = benchmark against
# StatsHunter's "silently reconnect once in years" bar without false alarms.
STRAVA_RECONNECT_THRESHOLD = int(os.environ.get("STRAVA_RECONNECT_THRESHOLD", "3"))

# OAuth state is a self-encoding signed JWT (audit 2026-05-27 S3.3).
# The old DB-backed implementation (`oauth_states` table) had two failure
# modes that the stateless approach eliminates:
#
#   1. Cloud Run scale-to-zero — if the user lingered on the Strava
#      consent screen long enough for the writing instance to terminate
#      and a fresh instance handle /callback, the in-memory cache miss
#      forced a DB lookup. Worked, but added a DB round-trip on the hot
#      OAuth path. The TTL got bumped 600→1800s specifically to absorb
#      cold-start latency.
#
#   2. Operational risk — every successful OAuth round-trip writes +
#      deletes a row, and an expired-state GC scan on every /connect.
#      Friends-beta scale this was fine, but for "what if 50 people
#      click Connect at once" it was extra contention on the same pool
#      that was already tight (PR #310 / #314).
#
# Replacement: HMAC-SHA256 signed JWT carrying `{sub, aud, iat, exp}`.
# `aud="strava-oauth-state"` separates this token from the user-auth
# JWT (also HS256 signed by JWT_SECRET) so a stolen / replayed user
# token can't be passed off as state, or vice versa.
#
# Replay protection: not needed at this layer. Strava's `code` is
# single-use server-side — a replayed callback with the same state but
# fresh code is impossible (Strava rejects the code). A replayed
# callback with the SAME (state, code) pair: Strava rejects the code
# at exchange time → we never reach the IntegrationAccount mutation.
#
# OPERATIONAL NOTE — JWT_SECRET coupling:
# Reusing JWT_SECRET for OAuth state means a JWT_SECRET leak forges
# BOTH user sessions AND OAuth state. Rotation is also linked:
# rotating JWT_SECRET invalidates every in-flight OAuth handshake
# (acceptable — the user just re-clicks Connect) AS WELL AS every
# active user session. Don't rotate without scheduling around both.
# When this trade-off stops being acceptable, introduce a separate
# STRAVA_OAUTH_STATE_SECRET and update `_encode/_decode_oauth_state`.
_OAUTH_STATE_TTL = 1800  # seconds (30 min) — keep the slow-mobile budget
_OAUTH_STATE_AUD = "strava-oauth-state"

# The old `oauth_states` table is dropped in migration 0049.

# Hard ceiling on the Phase-1 metadata pagination loop. Mirrors
# strava_client._COUNT_MAX_PAGES so a malformed Strava response (every page
# returning a full 200 items) cannot spin forever. 250 pages × 200 = 50 000
# activities — well above any realistic account. PR D of the
# strava-tough-review audit (High #5).
_PHASE1_MAX_PAGES = 250


def _encode_oauth_state(value: str) -> str:
    """Build a signed JWT that carries `value` (a user_id or the
    `_STRAVA_LOGIN_SENTINEL`) round-trip through Strava's /callback.

    `aud` is set to `_OAUTH_STATE_AUD` so a malicious or accidental
    swap with a user-auth JWT (which signs with the same JWT_SECRET
    but different `aud`/no-aud) fails verification. JWT_SECRET is
    already a hard-required prod secret — no new key to rotate.
    """
    from datetime import UTC, datetime, timedelta

    from jose import jwt

    from app.api.auth import JWT_ALGORITHM, JWT_SECRET
    now = datetime.now(UTC)
    payload = {
        "sub": value,
        "aud": _OAUTH_STATE_AUD,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=_OAUTH_STATE_TTL)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_oauth_state(state: str | None) -> str | None:
    """Verify the JWT signature, audience, expiry. Returns the
    embedded `sub` value (user_id or `_STRAVA_LOGIN_SENTINEL`) on
    success, None on any failure (forged, expired, mis-audienced,
    truncated, swapped with a user-auth token).

    Returning None on all failure modes preserves the contract of the
    old `_pop_oauth_state` — the /callback CSRF guard branch fires
    identically.

    SECURITY: passing `audience=...` to `jwt.decode` is NOT sufficient
    in `python-jose` — a JWT without an `aud` claim at all is accepted
    silently. We explicitly verify post-decode that the claim is
    present AND equals our expected audience. Without this, a user-
    auth JWT (signed by the same JWT_SECRET, no aud claim) could be
    passed as OAuth state and the /callback CSRF guard would bind a
    Strava account to whoever's session JWT the attacker stole.
    """
    if not state:
        return None
    from jose import JWTError, jwt

    from app.api.auth import JWT_ALGORITHM, JWT_SECRET
    try:
        payload = jwt.decode(
            state,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            audience=_OAUTH_STATE_AUD,
            options={"require": ["aud", "exp", "sub"]},
        )
    except JWTError:
        return None
    if payload.get("aud") != _OAUTH_STATE_AUD:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None

# Strava sport_type → our sport
from app.services.strava_utils import classify_strava_sport_or_skip, decode_polyline


def _require_strava() -> None:
    if not STRAVA_ENABLED and not TEST_MODE:
        raise HTTPException(
            status_code=501,
            detail="Strava integration is disabled. Set ENABLE_STRAVA_INTEGRATION=true.",
        )


# ── Schemas ──────────────────────────────────────────────────────────────────

class StravaStatusResponse(BaseModel):
    connected: bool
    athlete_name: str | None = None
    athlete_id: str | None = None
    last_synced_at: str | None = None
    # True once sync_failures has crossed STRAVA_RECONNECT_THRESHOLD — the
    # refresh_token is dead and the user must re-OAuth. The frontend surfaces
    # a "reconnect Strava" banner off this flag (FIX 2b) instead of the user
    # discovering weeks later that their stats silently stopped updating.
    reconnect_required: bool = False
    sync_failures: int = 0
    # True when the current account's email is still SYNTHETIC
    # (strava_<id>@strava.local) — i.e. a pure Strava-login account that can't
    # log in by email or receive notifications. The frontend surfaces an
    # "add your email" prompt off this flag (account unification). Independent
    # of `connected` so it also nudges a synthetic user after disconnect.
    needs_email: bool = False


class StravaPreviewResponse(BaseModel):
    athlete_name: str | None = None
    athlete_id: str | None = None
    estimated_activity_count: int = 0
    already_imported_count: int = 0


class ImportAllRequest(BaseModel):
    contribute_heatmap: bool = True


# ── Polyline decoder ─────────────────────────────────────────────────────────



# ── Token refresh ────────────────────────────────────────────────────────────


def record_refresh_failure(acct: IntegrationAccount, db: Session) -> int:
    """Record ONE definitive Strava auth failure on ``acct`` and, at the
    threshold crossing, emit a one-shot ``strava_reconnect_required``
    notification.

    "Definitive" means the refresh_token is dead (revoked or expired) —
    Strava answered the token endpoint with 400/401, or a subsequent
    activities call 401'd even after a fresh refresh. Transient failures
    (network blips, advisory-lock contention, 5xx) must NOT call this;
    they don't move the account toward the reconnect banner.

    Returns the new ``sync_failures`` count.

    One-shot semantics: the notification fires ONLY on the run where the
    counter first reaches ``STRAVA_RECONNECT_THRESHOLD`` (``==``, not
    ``>=``). Further failures keep incrementing but stay quiet, so a
    permanently-dead grant produces a single toast, not a daily one. A
    later successful sync resets the counter to 0 (``_mark_account_synced``
    / the resync success path), which re-arms this emit for any future
    revocation. This mirrors the StatsHunter benchmark: the user is told
    to reconnect exactly once, only when it's genuinely needed.
    """
    acct.sync_failures = (acct.sync_failures or 0) + 1
    failures = acct.sync_failures
    db.commit()

    if failures == STRAVA_RECONNECT_THRESHOLD:
        from app.services.notifications import emit_notification
        emit_notification(
            user_id=acct.user_id,
            kind="strava_reconnect_required",
            title="Reconnexion Strava requise",
            body=(
                "Votre connexion Strava a expiré — reconnectez Strava depuis "
                "la page Strava pour continuer à synchroniser vos activités."
            ),
            meta={
                "provider": "strava",
                "sync_failures": failures,
                "threshold": STRAVA_RECONNECT_THRESHOLD,
            },
        )
        log.warning(
            "Strava reconnect required for user=%s (sync_failures=%d) — "
            "emitted strava_reconnect_required notification",
            acct.user_id, failures,
        )
    return failures


async def refresh_strava_token(acct: IntegrationAccount, db: Session) -> str | None:
    """Refresh Strava OAuth token if expired. Returns valid access_token or None.

    Updates acct.access_token, refresh_token, expires_at in DB on success.
    Returns None on failure (token revoked, network error, no refresh_token).

    ## Per-user advisory lock

    Strava rotates the refresh_token on every `/oauth/token` call. With
    three concurrent callers — bulk import (Cloud Run Job), monthly
    resync, webhook worker — two callers fired within the 6h expiry
    window POST the SAME refresh_token; Strava issues a new one to the
    first caller and *invalidates* the second's. The loser then writes
    a stale refresh_token to our DB, and every subsequent webhook for
    that user fails until they re-OAuth.

    `pg_try_advisory_xact_lock(hashtext('strava-refresh:' || user_id))`
    serializes refreshes per-user across all callers and all instances.
    The lock is held only for the duration of THIS transaction (auto-
    released on commit/rollback/crash — no row to clean up). The 2nd
    caller blocks until the 1st commits the new token, re-reads
    `acct.expires_at`, sees it's now valid, and returns the freshly-
    refreshed token without re-POSTing to Strava.

    `pg_try_advisory_xact_lock` returns False if it can't acquire
    immediately; we then sleep briefly and re-read the row (no busy
    polling — at most 2 attempts).
    """
    if not acct.refresh_token:
        return None

    # Fast path: token still valid → no Strava call → no lock needed.
    if acct.expires_at and acct.expires_at > time.time() + 300:  # 5min buffer
        return decrypt_token(acct.access_token)

    if TEST_MODE:
        acct.expires_at = int(time.time()) + 3600
        db.commit()
        return decrypt_token(acct.access_token)

    # Per-user advisory lock. hashtext() narrows to 32 bits, so feed
    # pg_try_advisory_xact_lock(int) which is the canonical signature.
    # Using xact (transactional) variant ensures the lock releases on
    # commit OR crash, with no row-level cleanup required.
    from sqlalchemy import text as sa_text
    lock_key = f"strava-refresh:{acct.user_id}"
    for _attempt in range(2):
        got_lock_row = db.execute(
            sa_text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
            {"k": lock_key},
        ).first()
        got_lock = bool(got_lock_row and got_lock_row[0])
        if got_lock:
            break
        # Another caller is refreshing right now. Wait briefly, then
        # re-read the account row (DB session caches it). The other
        # caller commits → expires_at updates → we may not need to
        # refresh at all.
        log.info("Strava refresh lock busy for user=%s — waiting", acct.user_id)
        await asyncio.sleep(1.5)
        db.refresh(acct)
        if acct.expires_at and acct.expires_at > time.time() + 300:
            return decrypt_token(acct.access_token)
    else:
        # Both attempts contended. Give up — caller treats this as a
        # transient failure and Cloud Tasks (webhook) or the next job
        # retry will pick it up.
        log.warning("Strava refresh lock unavailable for user=%s after retries", acct.user_id)
        return None

    # Re-check expiry inside the lock — another caller may have just
    # refreshed between the fast-path check and lock acquisition.
    db.refresh(acct)
    if acct.expires_at and acct.expires_at > time.time() + 300:
        return decrypt_token(acct.access_token)

    import httpx
    try:
        client = await get_http_client()
        resp = await client.post(STRAVA_TOKEN_URL, data={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": decrypt_token(acct.refresh_token),
        }, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        # DEFINITIVE vs TRANSIENT. Strava answers a dead refresh_token
        # (revoked in the athlete's Strava settings, or expired) with
        # 400/401 (`invalid_grant`). That will NEVER succeed on retry —
        # the only cure is a fresh OAuth. Record it toward the reconnect
        # threshold + fire the one-shot user prompt (FIX 2b). A 429 or 5xx
        # is transient (rate-limit / Strava outage): return None WITHOUT
        # counting, so the next webhook / weekly resync just retries.
        status = exc.response.status_code
        if status in (400, 401):
            log.warning(
                "Strava refresh definitively rejected (status=%s) for user %s — "
                "recording auth failure", status, acct.user_id,
            )
            with contextlib.suppress(Exception):
                record_refresh_failure(acct, db)
        else:
            log.warning(
                "Token refresh transient error (status=%s) for user %s",
                status, acct.user_id,
            )
        return None
    except Exception as exc:
        # Network error, timeout, malformed JSON — transient. Do not count.
        log.warning("Token refresh failed (transient) for user %s: %s", acct.user_id, exc)
        return None

    new_access = data["access_token"]
    new_refresh = data.get("refresh_token")
    acct.access_token = encrypt_token(new_access)
    if new_refresh:
        acct.refresh_token = encrypt_token(new_refresh)
    acct.expires_at = data.get("expires_at")
    db.commit()
    return new_access


# ── DB helpers ───────────────────────────────────────────────────────────────

def _get_strava_account(db: Session, user_id: str) -> IntegrationAccount | None:
    return db.query(IntegrationAccount).filter(
        IntegrationAccount.user_id == user_id,
        IntegrationAccount.provider == "strava",
    ).first()


def _find_or_create_strava_user(
    db: Session, strava_id: str, athlete_name: str | None,
) -> str:
    """Return existing user_id for this Strava account, or create a new user."""
    acct = db.query(IntegrationAccount).filter(
        IntegrationAccount.external_user_id == strava_id,
        IntegrationAccount.provider == "strava",
    ).first()
    if acct:
        # Verify the linked user still exists (handles orphaned accounts)
        try:
            user = db.query(User).filter(User.id == acct.user_id).first()
        except Exception:
            db.rollback()
            user = None
        if user:
            return acct.user_id
        # Orphaned — delete stale account and create fresh
        log.warning("Orphaned IntegrationAccount %s — recreating", acct.id)
        db.delete(acct)
        db.flush()
        acct = None

    email = f"strava_{strava_id}@strava.local"
    # Re-use existing strava-login user if email already taken
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return existing.id

    user_id = str(uuid.uuid4())
    user = User(
        id=user_id,
        email=email,
        username=athlete_name or f"Strava {strava_id}",
        hashed_password=None,
    )
    db.add(user)
    db.flush()
    return user_id


def _try_self_merge_on_connect(
    db: Session, survivor_user_id: str, conflicting: IntegrationAccount
) -> bool:
    """Link-time SELF-MERGE for the account-unification split (#477).

    Called when a logged-in user CONNECTS a Strava athlete already bound to a
    DIFFERENT account. Returns True iff the conflict is a credential-less
    SYNTHETIC ``@strava.local`` account (the split-self case) and it was merged
    into the current (survivor) user — the caller then proceeds to (re)bind the
    athlete. Returns False for a real credentialed account: the anti-theft guard
    stands and the caller must refuse the relink.

    The merge is transactional + idempotent (``account_merge.merge_synthetic_account``).
    """
    from app.services.account_merge import (
        AccountMergeRefused,
        is_synthetic_account,
        merge_synthetic_account,
    )

    other = db.query(User).filter(User.id == conflicting.user_id).first()
    if not is_synthetic_account(other):
        return False
    try:
        merge_synthetic_account(db, survivor_user_id, conflicting.user_id, commit=True)
    except AccountMergeRefused:
        # Belt-and-suspenders: is_synthetic_account already gated this.
        db.rollback()
        return False
    except Exception:
        db.rollback()
        log.error("self-merge on Strava connect failed", exc_info=True)
        return False
    log.info(
        "Strava connect self-merged synthetic account %s into %s",
        conflicting.user_id, survivor_user_id,
    )
    return True


def _job_to_response(job: ImportJob, db: Session | None = None) -> ImportJobResponse:
    """Project an ImportJob row to its API shape.

    Hot path: the frontend polls this every 5 s during an import. Must
    be a single-row read — no COUNT, no JOIN, no aggregate.

    Previously ran a `COUNT(*) FROM activities` filtered by user/sport
    on every poll. During a 1400-activity import that contended with
    active ingest writes and starved the 5+3 connection pool, wedging
    every endpoint within ~30 s. Since migration 0045, `gps_total` is
    persisted on the row by `_run_strava_import` at end-of-Phase-2 and
    decremented by `_run_gps_upgrade` as it progresses.

    The `db` parameter is retained for source compatibility but unused.
    """
    del db  # was used for the COUNT we just removed

    # Parse cursor JSON for phase tracking fields
    phase: str | None = None
    current_page: int | None = None
    photos_imported: int = 0
    last_failed_phase: str | None = None
    if job.cursor:
        try:
            cursor_data = json.loads(job.cursor)
            phase = cursor_data.get("phase")
            current_page = cursor_data.get("current_page")
            photos_imported = cursor_data.get("photos_imported", 0)
            last_failed_phase = cursor_data.get("last_failed_phase")
        except (json.JSONDecodeError, TypeError):
            pass

    return ImportJobResponse(
        job_id=job.id,
        status=job.status,
        total_count=job.total_count or 0,
        imported_count=job.imported_count or 0,
        skipped_count=job.skipped_count or 0,
        failed_count=job.failed_count or 0,
        last_error=job.last_error,
        gps_upgraded_count=job.gps_upgraded_count or 0,
        # gps_total = activities pending GPS upgrade (snapshot at ingest end)
        # + activities already upgraded. Backwards-compat with the previous
        # computed shape ("all Strava activities ingested as polyline").
        gps_total=(job.gps_total or 0) + (job.gps_upgraded_count or 0),
        phase=phase,
        current_page=current_page,
        photos_imported=photos_imported,
        last_failed_phase=last_failed_phase,
    )


# ── Cloud Run Job trigger ─────────────────────────────────────────────────────

GCP_PROJECT = os.environ.get("GCP_PROJECT", "")
GCP_REGION = os.environ.get("GCP_REGION", "")
CLOUD_RUN_JOB_NAME = os.environ.get(
    "IMPORT_JOB_NAME", "common-trails-import-strava-prod"
)

# Loud warning at import time if Cloud Run Job dispatch isn't configured.
# The audit 2026-05-25 found that for ~a week these env vars were unset
# on prod (terraform set GOOGLE_CLOUD_PROJECT, the code reads GCP_PROJECT)
# so `_trigger_cloud_run_job` returned False silently and every import
# silently fell back to the in-process BackgroundTasks path that #314
# was supposed to deprecate. With this warning, the next "silent"
# regression becomes a logged WARNING the operator can grep for.
if not (GCP_PROJECT and GCP_REGION) and not TEST_MODE:
    logging.getLogger(__name__).warning(
        "Cloud Run Job delegation disabled (GCP_PROJECT=%r, GCP_REGION=%r) — "
        "Strava imports will run IN-PROCESS on the API worker via "
        "BackgroundTasks. This pool-saturation path was the cause of the "
        "2026-05-17 outage. Set both env vars in terraform.",
        GCP_PROJECT, GCP_REGION,
    )


def _trigger_cloud_run_job(job_id: str) -> bool:
    """Trigger the Cloud Run Job with IMPORT_JOB_ID override.

    Uses the Cloud Run Admin REST API + metadata server token (lightweight,
    no google-cloud-run SDK needed).

    Returns True if the job was triggered, False if unavailable (local dev).
    """
    if not GCP_PROJECT or not GCP_REGION:
        return False

    try:
        import httpx

        # Get access token from GCE metadata server (available on Cloud Run)
        token_resp = httpx.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
            timeout=5.0,
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        # Trigger the job via REST API
        url = (
            f"https://run.googleapis.com/v2/projects/{GCP_PROJECT}"
            f"/locations/{GCP_REGION}/jobs/{CLOUD_RUN_JOB_NAME}:run"
        )
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "overrides": {
                    "containerOverrides": [{
                        "env": [{"name": "IMPORT_JOB_ID", "value": job_id}],
                    }],
                },
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        log.info("Triggered Cloud Run Job %s for import %s", CLOUD_RUN_JOB_NAME, job_id)
        return True
    except Exception as exc:
        log.warning("Cloud Run Job trigger failed, falling back to BackgroundTasks: %s", exc)
        return False


# ── Background import task ────────────────────────────────────────────────────

def _job_update(job_id: str, **fields) -> None:
    """Persist a partial update to import_jobs in a short-lived session.

    Use for every progress write inside ``_run_strava_import`` so we
    don't hold a pool connection during the long async I/O between
    writes (Strava API calls, rate-limit sleeps). The pool is 5+3 on
    db-f1-micro; holding one connection for the entire job duration
    while async I/O is in flight starves every other request.

    ``cursor`` is merged with the existing JSON object rather than
    overwritten, so callers can update individual phase-tracking fields
    without re-reading.
    """
    db = SessionLocal()
    try:
        job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
        if not job:
            return
        if "cursor_merge" in fields:
            existing: dict = {}
            if job.cursor:
                try:
                    existing = json.loads(job.cursor)
                except (json.JSONDecodeError, TypeError):
                    existing = {}
            existing.update(fields.pop("cursor_merge"))
            job.cursor = json.dumps(existing)
        for k, v in fields.items():
            setattr(job, k, v)
        db.commit()
    finally:
        db.close()


def _job_read_cursor(job_id: str) -> dict:
    """Read the cursor JSON in a short-lived session. Returns {} on miss."""
    db = SessionLocal()
    try:
        job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
        if not job or not job.cursor:
            return {}
        try:
            return json.loads(job.cursor)
        except (json.JSONDecodeError, TypeError):
            return {}
    finally:
        db.close()


async def _run_strava_import(job_id: str, user_id: str, access_token: str) -> None:
    """Fetch all Strava activities and ingest them.

    Uses short-lived DB sessions for every state update — no session is
    held across an ``await`` boundary. This is the structural fix for
    the 2026-05-17 pool-exhaustion incident where the outer session
    was held for the entire job (Phase 1+2+3+4, potentially hours) and
    starved the 5+3 pool whenever a frontend poll or background request
    came in.
    """
    from app.services.ingest import ingest_activities_bulk
    from app.services.provenance import STRAVA_API_SOURCE

    # Bootstrap: read heatmap preference + early-return if job missing.
    cursor_data = _job_read_cursor(job_id)
    if not cursor_data and not _job_exists(job_id):
        return
    contribute_heatmap = cursor_data.get("contribute_heatmap", True)

    # Safety guard: never make external calls in TEST_MODE
    if TEST_MODE:
        _job_update(job_id, status="COMPLETED", imported_count=3)
        return

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            # ── Phase 1: fetch all activity metadata (paginated) ──
            _job_update(job_id, cursor_merge={"phase": "discovering"})
            all_activities: list[dict] = []
            page = 1
            while True:
                resp = await client.get(
                    f"{STRAVA_API_BASE}/athlete/activities",
                    params={"page": page, "per_page": 200},
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=20.0,
                )
                if resp.status_code == 401:
                    _job_update(
                        job_id,
                        status="FAILED",
                        last_error="Token Strava expiré — reconnectez Strava.",
                    )
                    return
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    log.warning("Strava rate limit on page %d, waiting %ds", page, retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                if not resp.is_success:
                    break
                activities = resp.json()
                if not activities:
                    break
                all_activities.extend(activities)
                last_page = len(activities) < 200
                _job_update(
                    job_id,
                    total_count=len(all_activities),
                    cursor_merge={"current_page": page},
                )
                if last_page:
                    break
                page += 1
                if page > _PHASE1_MAX_PAGES:
                    # Defensive bound. Real accounts never hit this; a malformed
                    # Strava response (each page = 200 items, never decreasing)
                    # would otherwise spin forever. PR D of the
                    # strava-tough-review audit (High #5).
                    log.error(
                        "Phase 1 hit page cap (%d) for job %s — aborting pagination",
                        _PHASE1_MAX_PAGES, job_id,
                    )
                    with contextlib.suppress(Exception):
                        sentry_sdk.capture_message(
                            f"strava_import: Phase 1 page cap hit ({_PHASE1_MAX_PAGES})",
                            level="warning",
                        )
                    break
                await asyncio.sleep(1.5)  # respect Strava rate limits

            # ── Phase 2: bulk import using summary polylines ──
            _job_update(job_id, cursor_merge={"phase": "ingesting"})

            # Prepare activity data list (pure CPU, no DB/API calls).
            # Two filters before each activity reaches activities_data:
            #   1. Privacy gate — skip if act["private"] is True. The
            #      user marked it private on Strava; we honor that even
            #      though they opted into our import at the account
            #      level. Same policy as the webhook worker.
            #   2. Sport gate — `classify_strava_sport_or_skip` returns
            #      None for out-of-scope types (Yoga/Workout/Virtual/
            #      Swim/Ski/etc.) so we don't pollute the heatmap with
            #      Zwift's Watopia coords. See [[feedback_skip_beats_pollute_heatmap]].
            activities_data: list[dict] = []
            phase2_private_skipped = 0
            phase2_sport_skipped = 0
            for act in all_activities:
                if act.get("private") is True:
                    phase2_private_skipped += 1
                    continue

                sport_type = act.get("sport_type") or act.get("type", "")
                # Pass the activity NAME for the SSOT gravel/mtb refinement
                # (a generic "Ride" named "Gravel ..." / "VTT ..." → gravel/mtb).
                sport = classify_strava_sport_or_skip(sport_type, act.get("name"))
                if sport is None:
                    phase2_sport_skipped += 1
                    continue

                geometry_geojson: str | None = None
                polyline = act.get("map", {}).get("summary_polyline") or ""
                if polyline:
                    coords = decode_polyline(polyline)
                    if len(coords) >= 2:
                        geometry_geojson = json.dumps({
                            "type": "LineString",
                            "coordinates": coords,
                        })

                activities_data.append({
                    "provider": "strava",
                    "provider_activity_id": str(act["id"]),
                    "sport": sport,
                    "name": act.get("name"),
                    "geometry_geojson": geometry_geojson,
                    "distance_m": act.get("distance"),
                    "elevation_gain_m": act.get("total_elevation_gain"),
                    "moving_time": act.get("moving_time"),
                    "total_photo_count": act.get("total_photo_count", 0),
                    "activity_date": act.get("start_date_local") or act.get("start_date"),
                })

            if phase2_private_skipped or phase2_sport_skipped:
                log.info(
                    "Strava bulk Phase 2: skipped %d private + %d out-of-scope sport activities (kept %d)",
                    phase2_private_skipped, phase2_sport_skipped, len(activities_data),
                )

            # Progress callback fires per batch (every 50 activities). Uses
            # its own short-lived session — no contention with ingest.
            def _on_progress(c: int, s: int, f: int) -> None:
                _job_update(job_id, imported_count=c, skipped_count=s, failed_count=f)

            result = ingest_activities_bulk(
                user_id=user_id,
                activities_data=activities_data,
                contribute_heatmap=contribute_heatmap,
                on_progress=_on_progress,
                # Strava-API provenance — personal-only, never feeds the
                # community heatmap (Strava 2026 API Policy §5.4/§5.10).
                source=STRAVA_API_SOURCE,
            )

            # End-of-Phase-2 snapshot of activities still on polyline geometry.
            # Stored on the job row so the /jobs/{id} poll endpoint doesn't
            # have to re-COUNT every 5 s (which was the second pool-starvation
            # path, fixed alongside the outer-session shortening above).
            gps_total_snapshot = _count_polyline_activities(user_id)

            _job_update(
                job_id,
                status="COMPLETED",
                imported_count=result["created"],
                skipped_count=result["skipped"],
                failed_count=result["failed"],
                gps_total=gps_total_snapshot,
            )
            # Mark the account as freshly synced so the daily health-check
            # cron (sync_failures > 5 AND last_synced_at < NOW() - 7d) sees
            # this as a recent successful sync and doesn't false-trip on
            # webhook-only accounts whose `last_synced_at` would otherwise
            # be NULL forever. Audit 2026-05-29 ST-S2.8.
            _mark_account_synced(user_id)
            _emit_bulk_import_complete(
                user_id, job_id, result, gps_total_snapshot,
            )

            # ── Phase 3: GPS upgrade (fetch full streams, rate-limited) ──
            _job_update(job_id, cursor_merge={"phase": "gps_upgrade"})
            await _run_gps_upgrade(job_id, user_id, access_token)

            # ── Phase 4: Photo import ──
            _job_update(job_id, cursor_merge={"phase": "photo_import"})
            await _run_photo_import(job_id, user_id, access_token)

            # All phases done
            _job_update(job_id, cursor_merge={"phase": "done"})

    except Exception as exc:
        # Every step in this handler opens its own short-lived session
        # OR is purely in-memory (sentry / log / emit_notification, the
        # last of which already swallows internally). On db-f1-micro
        # under pool pressure `_job_read_cursor` and `_job_update` can
        # raise themselves (connection drop, statement timeout). Pre-PR
        # an unhandled raise from `_job_read_cursor` at line 727 would
        # escape `_run_strava_import` un-Sentried — the row would stay
        # RUNNING forever (only the daily health-check cron at 3 h
        # would clean it up). Wrap each side-effect so a secondary DB
        # blip can't hide the original cause. Audit 2026-05-29 ST-S1.5.
        last_phase: str | None = None
        try:
            last_phase = _job_read_cursor(job_id).get("phase")
        except Exception:
            log.warning("ST-S1.5 _job_read_cursor failed (job=%s)", job_id, exc_info=True)
        # Sentry: capture FIRST so a tag-set raise can't skip the actual
        # capture call. `contextlib.suppress` only catches exceptions
        # escaping the whole block — if `set_tag` raised, every later
        # statement (including capture_exception) would be skipped.
        try:
            sentry_sdk.capture_exception(exc)
            sentry_sdk.set_tag("strava.phase", last_phase or "unknown")
            sentry_sdk.set_tag("strava.job_id", job_id)
        except Exception:
            log.warning("ST-S1.5 sentry capture failed (job=%s)", job_id, exc_info=True)
        # Persist the failed phase on the cursor so the frontend can surface
        # which phase failed in the done/error card.
        cursor_patch: dict = {}
        if last_phase:
            cursor_patch["last_failed_phase"] = last_phase
        try:
            _job_update(
                job_id, status="FAILED", last_error=str(exc),
                cursor_merge=cursor_patch,
            )
        except Exception:
            log.warning(
                "ST-S1.5 _job_update FAILED-write failed; row may stay RUNNING (job=%s)",
                job_id, exc_info=True,
            )
        log.error(
            "Strava import failed (phase=%s job=%s)", last_phase, job_id,
            exc_info=True,
        )
        try:
            from app.services.notifications import emit_notification
            emit_notification(
                user_id=user_id,
                kind="strava_import_failed",
                title="Import Strava interrompu",
                body=str(exc)[:200],
                meta={"job_id": job_id, "phase": last_phase},
            )
        except Exception:
            log.warning("ST-S1.5 emit_notification failed (job=%s)", job_id, exc_info=True)


def _job_exists(job_id: str) -> bool:
    """Cheap existence check in a short-lived session."""
    db = SessionLocal()
    try:
        return db.query(ImportJob.id).filter(ImportJob.id == job_id).first() is not None
    finally:
        db.close()


def _mark_account_synced(user_id: str) -> None:
    """Stamp `last_synced_at = NOW()` AND reset `sync_failures = 0` on
    the user's Strava IntegrationAccount in a short-lived session.

    Called after any successful Strava-driven ingest (bulk import,
    webhook event, manual resync). Pre-PR `last_synced_at` was only
    written by `resync_strava.py` — but resync is decommissioned in
    favour of webhooks, so post-#341 accounts that connect and use
    webhooks ONLY would have `last_synced_at = NULL` forever. The
    health-check cron's `(sync_failures > 5 AND last_synced_at < NOW()
    - 7 days)` gate suppresses alerts on "recently successful" syncs;
    with NULL last_synced_at, the suppression never engages and stale
    sync_failures would alert. Audit 2026-05-29 ST-S2.8.

    Failures are swallowed and logged — a missed timestamp must never
    break the import path that called it.
    """
    from datetime import UTC, datetime
    db = SessionLocal()
    try:
        db.query(IntegrationAccount).filter(
            IntegrationAccount.user_id == user_id,
            IntegrationAccount.provider == "strava",
        ).update(
            {"last_synced_at": datetime.now(UTC), "sync_failures": 0},
            synchronize_session=False,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to mark account synced (user=%s): %s", user_id, exc)
        with contextlib.suppress(Exception):
            db.rollback()
    finally:
        db.close()


def _count_polyline_activities(user_id: str) -> int:
    """Count user activities still on summary-polyline geometry.

    Called ONCE at end-of-Phase-2 ingest (not on every poll). Result is
    stored on `import_jobs.gps_total` and read from there by the poll
    endpoint.
    """
    db = SessionLocal()
    try:
        return db.query(Activity).filter(
            Activity.user_id == user_id,
            Activity.provider == "strava",
            Activity.geometry_source == "polyline",
            Activity.geometry_geojson.isnot(None),
        ).count()
    finally:
        db.close()


def _emit_bulk_import_complete(
    user_id: str,
    job_id: str,
    result: dict,
    gps_total: int,
) -> None:
    """Emit `strava_bulk_import_complete` after Phase 2 lands.

    Closes the silent-promise gap (audit 2026-05-27 S2.1): the UI tells
    the user "Vous recevrez une notification" but neither Phase 2 (bulk
    ingest) nor a job with `gps_total=0 + photos_count=0` ever emitted
    one — the only positive-path notifications were on the Phase 3 GPS
    upgrade and Phase 4 photos. Now Phase 2 always fires a toast so the
    user knows the bulk import landed and their activities are
    searchable. Phase 3/4 continue to emit their own follow-ups when
    they have work to do.

    Body is tailored to whether there's GPS-upgrade work pending. When
    `gps_total > 0` we hint that the trace polish continues in the
    background so the user doesn't think the import is somehow
    incomplete.
    """
    from app.services.notifications import emit_notification

    created = int(result.get("created", 0) or 0)
    skipped = int(result.get("skipped", 0) or 0)
    failed = int(result.get("failed", 0) or 0)
    # French agreement: 0 and 1 are both singular (Académie rule), 2+
    # are plural. So "0 activité importée" and "1 activité importée"
    # but "2 activités importées".
    def _fr_s(n: int) -> str:
        return "s" if n >= 2 else ""

    body = f"{created} activité{_fr_s(created)} importée{_fr_s(created)}"
    if skipped:
        body += f", {skipped} ignorée{_fr_s(skipped)}"
    if failed:
        body += f", {failed} erreur{_fr_s(failed)}"
    if gps_total > 0:
        body += " — l'amélioration GPS continue en arrière-plan."
    emit_notification(
        user_id=user_id,
        kind="strava_bulk_import_complete",
        title="Import Strava terminé",
        body=body,
        meta={
            "job_id": job_id,
            "imported_count": created,
            "skipped_count": skipped,
            "failed_count": failed,
            "gps_total": gps_total,
        },
    )


# ── Phase 3: GPS stream upgrade ──────────────────────────────────────────────

def _gps_next_batch(user_id: str, batch_size: int) -> list[tuple[str, str, str]]:
    """Read the next batch of polyline-only activities in a short session.

    Returns `[(activity_id, provider_activity_id, sport), …]` — only the
    fields we need to fetch and update. No ORM objects escape the session
    (would carry the connection with them).
    """
    db = SessionLocal()
    try:
        rows = db.query(
            Activity.id,
            Activity.provider_activity_id,
            Activity.sport,
        ).filter(
            Activity.user_id == user_id,
            Activity.provider == "strava",
            Activity.geometry_source == "polyline",
            Activity.geometry_geojson.isnot(None),
            Activity.provider_activity_id.isnot(None),
        ).limit(batch_size).all()
        return [(r.id, r.provider_activity_id, r.sport or "road") for r in rows]
    finally:
        db.close()


def _gps_apply_batch(
    job_id: str,
    upgrades: list[tuple[str, str | None]],  # (activity_id, geojson_str|None)
    upgraded_delta: int,
    last_error: str | None = None,
) -> None:
    """Persist batch updates in a short-lived session.

    `upgrades` carries the per-activity outcome — geojson_str=None means
    "mark as `stream` source, no geometry update" (failure path). This
    runs entirely outside the awaitable HTTP and rate-limit-pause paths
    so the pool connection is held only for the SQL writes themselves.
    """
    if not upgrades and not upgraded_delta and not last_error:
        return
    from app.services.ingest import _geom_from_geojson_sql
    db = SessionLocal()
    try:
        for activity_id, geojson_str in upgrades:
            act = db.query(Activity).filter(Activity.id == activity_id).first()
            if not act:
                continue
            if geojson_str:
                act.geometry_geojson = geojson_str
                act.geometry = _geom_from_geojson_sql(geojson_str)
            act.geometry_source = "stream"
        if upgraded_delta or last_error:
            job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
            if job:
                if upgraded_delta:
                    job.gps_upgraded_count = (job.gps_upgraded_count or 0) + upgraded_delta
                if last_error:
                    job.last_error = last_error
        db.commit()
    finally:
        db.close()


def _read_job_progress(job_id: str) -> tuple[int, int, int]:
    """Read (gps_upgraded_count, gps_total, failed_count) for the post-phase
    notification — short session, returns once."""
    db = SessionLocal()
    try:
        job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
        if not job:
            return 0, 0, 0
        return (
            int(job.gps_upgraded_count or 0),
            int(job.gps_total or 0),
            int(job.failed_count or 0),
        )
    finally:
        db.close()


async def _run_gps_upgrade(job_id: str, user_id: str, access_token: str) -> None:
    """Fetch full GPS streams for polyline-only activities (rate-limited).

    Runs after Phase 2 completes. Updates geometry with high-resolution
    GPS data and re-ingests edges for better routing graph coverage.

    Per-batch session pattern (matches `ingest_activities_bulk` after
    PR #311 + `_run_strava_import` after the 2026-05-17 hotfix): no
    pool connection is held across the ``await asyncio.gather`` HTTP
    fan-out or the rate-limit ``asyncio.sleep`` between batches.

    Heat-edge updates per upgraded activity used to run inline here,
    serialising ~280k UPSERTs (1400 activities × ~200 edges each) inside
    the user-visible "GPS upgrade" phase. PR-E moves them to Cloud Tasks
    (`enqueue_heat_compute` — same infra GPX uploads use). Phase 3
    completes when streams are persisted; heat edges trickle in async.
    """
    from app.services.cloud_tasks import enqueue_heat_compute

    BATCH_SIZE = 25   # concurrent requests per batch
    BATCH_PAUSE = 3   # seconds between batches → ~150 req/min (Strava limit: 200/15min)

    try:
        while True:
            batch = _gps_next_batch(user_id, BATCH_SIZE)
            if not batch:
                break

            async def _fetch_one(
                activity_id: str, provider_activity_id: str, sport: str,
            ) -> tuple[str, str, dict | None, Exception | None]:
                try:
                    stream = await get_activity_stream(access_token, int(provider_activity_id))
                    return activity_id, sport, stream, None
                except Exception as exc:
                    return activity_id, sport, None, exc  # noqa: B023 — exc is local to this except, not a loop var

            results = await asyncio.gather(*[
                _fetch_one(act_id, prov_id, sport)
                for act_id, prov_id, sport in batch
            ])

            stop = False
            stop_error: str | None = None
            upgrades: list[tuple[str, str | None]] = []
            heat_task_activity_ids: list[str] = []
            upgraded_delta = 0
            rate_limited_retry_after: int | None = None

            for activity_id, _sport, stream, exc in results:
                if exc is not None:
                    if isinstance(exc, StravaRateLimited):
                        # Rate-limited — don't mark; will retry next loop.
                        # The whole batch breaks so we sleep before retrying.
                        # Honour the largest Retry-After seen in this batch.
                        rate_limited_retry_after = max(
                            rate_limited_retry_after or 0, exc.retry_after,
                        )
                        log.warning(
                            "GPS upgrade: rate limited, will pause %ds (Retry-After header)",
                            exc.retry_after,
                        )
                        continue
                    err_str = str(exc)
                    if "401" in err_str:
                        log.warning("GPS upgrade: token expired, stopping")
                        stop = True
                        stop_error = "GPS upgrade: token Strava expiré"
                        break
                    # Other error — mark stream so we don't loop on it.
                    upgrades.append((activity_id, None))
                    upgraded_delta += 1
                    continue

                geojson = stream_to_geojson(stream) if stream else None
                if geojson:
                    geojson_str = json.dumps(geojson)
                    upgrades.append((activity_id, geojson_str))
                    # Queue a heat-compute task for this upgraded activity
                    # (deferred until AFTER _gps_apply_batch commits the new
                    # geometry — the receiver reads it back from the DB).
                    heat_task_activity_ids.append(activity_id)
                else:
                    upgrades.append((activity_id, None))
                upgraded_delta += 1

            # Persist batch results (short-lived session)
            _gps_apply_batch(job_id, upgrades, upgraded_delta, stop_error)

            # Heat-edge recompute is deferred to Cloud Tasks — the receiver
            # opens its own short-lived session and runs after the geometry
            # commit above is visible. Falls through to inline in dev/TEST.
            for activity_id in heat_task_activity_ids:
                try:
                    enqueue_heat_compute(activity_id, user_id)
                except Exception:  # noqa: BLE001 — enqueue failure is non-fatal
                    log.warning(
                        "GPS upgrade: enqueue_heat_compute failed for activity=%s",
                        activity_id, exc_info=True,
                    )

            if stop:
                break

            # Rate-limit sleep: no session held while we wait.
            if rate_limited_retry_after is not None:
                # Day-cap detection: Strava's per-window rate limit resets
                # within 15 min. Anything longer means we hit the 1000/day
                # cap and the real wait is hours — bail and let the next
                # monthly resync pick this up rather than burn the worker.
                if rate_limited_retry_after > RATELIMIT_DAY_CAP_THRESHOLD_S:
                    log.warning(
                        "GPS upgrade: Strava day-cap likely hit (retry_after=%ds), "
                        "aborting job — resync_strava will resume next month",
                        rate_limited_retry_after,
                    )
                    _gps_apply_batch(
                        job_id, [], 0,
                        f"GPS upgrade: Strava daily cap (retry_after={rate_limited_retry_after}s)",
                    )
                    break
                await asyncio.sleep(rate_limited_retry_after)
            else:
                await asyncio.sleep(BATCH_PAUSE)

        # Post-phase notification — single short session.
        from app.services.notifications import emit_notification
        upgraded, total_raw, errors = _read_job_progress(job_id)
        total = total_raw or upgraded
        emit_notification(
            user_id=user_id,
            kind="strava_gps_upgrade_complete",
            title="Amélioration GPS terminée",
            body=(
                f"{upgraded} activité{'s' if upgraded != 1 else ''} traitée{'s' if upgraded != 1 else ''}"
                + (f", {errors} erreur{'s' if errors != 1 else ''}" if errors else "")
            ),
            meta={
                "job_id": job_id,
                "upgraded_count": upgraded,
                "total_count": total,
                "failed_count": errors,
            },
        )
    except Exception as exc:
        # Sentry capture FIRST so a tag-set raise can't preempt the actual
        # stack capture. Wrapped so a sentry SDK error can't escape this
        # handler. Audit follow-up to PR #357 — same pattern as the
        # `_run_strava_import` outer except.
        try:
            sentry_sdk.capture_exception(exc)
            sentry_sdk.set_tag("strava.phase", "gps_upgrade")
            sentry_sdk.set_tag("strava.job_id", job_id)
        except Exception:
            log.warning("sentry capture failed in _run_gps_upgrade (job=%s)", job_id, exc_info=True)
        # Record the failed phase on the cursor so the frontend can surface it.
        try:
            _job_update(job_id, cursor_merge={"last_failed_phase": "gps_upgrade"})
        except Exception:
            log.warning("_job_update failed in _run_gps_upgrade except (job=%s)", job_id, exc_info=True)
        log.error("GPS upgrade failed (job=%s): %s", job_id, exc, exc_info=True)


# ── Phase 4: Photo import ────────────────────────────────────────────────────

def _photos_next_batch(
    user_id: str, batch_size: int,
) -> list[tuple[str, str, str | None]]:
    """Read the next batch of activities still missing photos in a short
    session.

    Returns `[(activity_id, provider_activity_id, activity_name), …]` —
    only the fields we need to fetch and insert. No ORM objects escape
    the session (would carry the connection with them).
    """
    db = SessionLocal()
    try:
        rows = db.query(
            Activity.id,
            Activity.provider_activity_id,
            Activity.name,
        ).outerjoin(
            ActivityPhoto,
            Activity.id == ActivityPhoto.activity_id,
        ).filter(
            Activity.user_id == user_id,
            Activity.provider == "strava",
            Activity.total_photo_count > 0,
            Activity.provider_activity_id.isnot(None),
            ActivityPhoto.id.is_(None),
        ).limit(batch_size).all()
        return [(r.id, r.provider_activity_id, r.name) for r in rows]
    finally:
        db.close()


def _photos_apply_batch(
    job_id: str,
    user_id: str,
    inserts: list[tuple[str, str, dict]],  # (activity_id, activity_name, photo_dict)
    failed_delta: int = 0,
    last_error: str | None = None,
) -> int:
    """Persist a batch of photos in a short-lived session.

    Returns the number of ``ActivityPhoto`` rows actually inserted (after
    skipping malformed payloads + already-imported duplicates). Updates
    the job's ``cursor.photos_imported`` counter atomically with the
    INSERTs so a crash mid-loop never double-counts.
    """
    if not inserts and not failed_delta and not last_error:
        return 0
    db = SessionLocal()
    inserted = 0
    try:
        for activity_id, activity_name, photo in inserts:
            location = photo.get("location")
            if not location or len(location) < 2:
                continue

            urls = photo.get("urls", {})
            url_medium = urls.get("600") or urls.get(600, "")
            url_thumb = urls.get("100") or urls.get(100, "")
            if not url_medium or not url_thumb:
                continue

            strava_photo_id = photo.get("unique_id")
            if strava_photo_id:
                existing = db.query(ActivityPhoto).filter(
                    ActivityPhoto.user_id == user_id,
                    ActivityPhoto.strava_photo_id == strava_photo_id,
                ).first()
                if existing:
                    continue

            db.add(ActivityPhoto(
                activity_id=activity_id,
                user_id=user_id,
                strava_photo_id=strava_photo_id,
                url_thumb=url_thumb,
                url_medium=url_medium,
                lat=location[0],
                lon=location[1],
                caption=photo.get("caption") or None,
                activity_name=activity_name,
            ))
            inserted += 1

        if inserted or failed_delta or last_error:
            job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
            if job:
                if inserted:
                    cursor: dict = {}
                    if job.cursor:
                        try:
                            cursor = json.loads(job.cursor)
                        except (json.JSONDecodeError, TypeError):
                            cursor = {}
                    cursor["photos_imported"] = int(cursor.get("photos_imported", 0)) + inserted
                    job.cursor = json.dumps(cursor)
                if failed_delta:
                    job.failed_count = (job.failed_count or 0) + failed_delta
                if last_error:
                    job.last_error = last_error
        db.commit()
        return inserted
    finally:
        db.close()


def _read_photo_progress(job_id: str) -> tuple[int, int]:
    """Read (photos_imported, failed_count) for the post-phase
    notification — short session, returns once."""
    db = SessionLocal()
    try:
        job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
        if not job:
            return 0, 0
        photos_imported = 0
        if job.cursor:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                cursor = json.loads(job.cursor)
                photos_imported = int(cursor.get("photos_imported", 0) or 0)
        return photos_imported, int(job.failed_count or 0)
    finally:
        db.close()


async def _run_photo_import(job_id: str, user_id: str, access_token: str) -> None:
    """Fetch photos for activities that have them. Runs after GPS upgrade.

    Per-batch session pattern (matches ``_run_gps_upgrade`` after PR #314):
    no pool connection is held across the ``await asyncio.gather`` HTTP
    fan-out or the ``await asyncio.sleep`` between batches. On db-f1-micro
    the pool is 5+3 — Phase 4 used to pin one connection for the whole
    phase duration. See ``/tmp/strava-tough-review.md`` Sev-1 #2.
    """
    BATCH_SIZE = 25
    BATCH_PAUSE = 3

    try:
        while True:
            batch = _photos_next_batch(user_id, BATCH_SIZE)
            if not batch:
                break

            async def _fetch_photos(
                activity_id: str, provider_activity_id: str, activity_name: str | None,
            ) -> tuple[str, str | None, list[dict] | None, Exception | None]:
                try:
                    photos = await get_activity_photos(access_token, int(provider_activity_id))
                    return activity_id, activity_name, photos, None
                except Exception as exc:
                    return activity_id, activity_name, None, exc  # noqa: B023

            results = await asyncio.gather(*[
                _fetch_photos(act_id, prov_id, name)
                for act_id, prov_id, name in batch
            ])

            stop = False
            stop_error: str | None = None
            rate_limited_retry_after: int | None = None
            inserts: list[tuple[str, str, dict]] = []

            for activity_id, activity_name, photos, exc in results:
                if exc is not None:
                    # Typed Strava rate-limit exception from PR #332.
                    # Honour the largest Retry-After seen in this batch;
                    # break the for-loop and sleep AFTER persisting
                    # whatever successful inserts accumulated so far.
                    if isinstance(exc, StravaRateLimited):
                        rate_limited_retry_after = max(
                            rate_limited_retry_after or 0, exc.retry_after,
                        )
                        log.warning(
                            "Photo import: rate limited, will pause %ds (Retry-After) — "
                            "skipping rest of batch",
                            exc.retry_after,
                        )
                        break
                    err_str = str(exc)
                    if "401" in err_str:
                        log.warning("Photo import: token expired, stopping")
                        stop = True
                        stop_error = "Photo import: token Strava expiré"
                        break
                    log.warning("Photo import: error for activity %s: %s", activity_id, exc)
                    continue

                if not photos:
                    continue

                for photo in photos:
                    inserts.append((activity_id, activity_name or "", photo))

            # Persist batch results (short-lived session).
            _photos_apply_batch(job_id, user_id, inserts, last_error=stop_error)

            if stop:
                break

            # Rate-limit pause OUTSIDE the per-row loop. Day-cap detection
            # mirrors the GPS-upgrade behaviour: if Strava asks for >15 min,
            # the daily 1000-request cap is hit — bail rather than thrash.
            if rate_limited_retry_after is not None:
                if rate_limited_retry_after > RATELIMIT_DAY_CAP_THRESHOLD_S:
                    log.warning(
                        "Photo import: Strava day-cap likely hit (retry_after=%ds), "
                        "aborting — resync_strava will resume next month",
                        rate_limited_retry_after,
                    )
                    break
                await asyncio.sleep(rate_limited_retry_after)
            else:
                await asyncio.sleep(BATCH_PAUSE)

        # Post-phase notification — single short session.
        photos_count, _errors = _read_photo_progress(job_id)
        if photos_count > 0:
            from app.services.notifications import emit_notification
            emit_notification(
                user_id=user_id,
                kind="strava_photo_import_complete",
                title="Import des photos terminé",
                body=f"{photos_count} photo{'s' if photos_count != 1 else ''} importée{'s' if photos_count != 1 else ''}",
                meta={"job_id": job_id, "photos_imported": photos_count},
            )
    except Exception as exc:
        # PR #334 Sentry hooks layered on top of #331's helper-based
        # _run_photo_import. NB: #331 removed the outer SessionLocal,
        # so the previous `finally: db.close()` is gone — short sessions
        # are now opened and closed inside the helpers themselves.
        # Capture-first ordering + per-step suppression: same pattern as
        # PR #357 fix on the `_run_strava_import` outer except.
        try:
            sentry_sdk.capture_exception(exc)
            sentry_sdk.set_tag("strava.phase", "photo_import")
            sentry_sdk.set_tag("strava.job_id", job_id)
        except Exception:
            log.warning("sentry capture failed in _run_photo_import (job=%s)", job_id, exc_info=True)
        try:
            _job_update(job_id, cursor_merge={"last_failed_phase": "photo_import"})
        except Exception:
            log.warning("_job_update failed in _run_photo_import except (job=%s)", job_id, exc_info=True)
        log.error("Photo import failed (job=%s): %s", job_id, exc, exc_info=True)


# Sentinel value for strava-login flow (vs. existing connect flow)
_STRAVA_LOGIN_SENTINEL = "__strava_login__"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/login")
async def strava_login(request: Request) -> RedirectResponse:
    """Redirect to Strava OAuth for login/signup (no existing account needed)."""
    _require_strava()

    # The state IS the JWT — Strava round-trips it verbatim. No DB
    # write, no instance-local cache. PR #349 / audit S3.3.
    state = _encode_oauth_state(_STRAVA_LOGIN_SENTINEL)

    if TEST_MODE:
        return RedirectResponse(
            url=f"/integrations/strava/callback?code=stub_code_test&scope=read,activity:read_all&state={state}"
        )

    redirect_uri = STRAVA_REDIRECT_URI
    if redirect_uri.startswith("http://localhost"):
        base = str(request.base_url).rstrip("/")
        redirect_uri = f"{base}/integrations/strava/callback"

    params = urlencode({
        "client_id": STRAVA_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": "read,activity:read_all",
        "state": state,
    })
    return RedirectResponse(url=f"{STRAVA_AUTH_URL}?{params}")


@router.get("/connect")
async def strava_connect(
    request: Request,
    token: str | None = None,
) -> RedirectResponse:
    """Redirect to Strava OAuth authorization page."""
    _require_strava()

    # Read token from query param, Authorization header, or httpOnly cookie
    raw_token = token
    if raw_token is None:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            raw_token = auth_header[7:]
    if raw_token is None:
        raw_token = request.cookies.get("auth_token")
    if raw_token is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = _decode_token(raw_token)
    user_id = payload.get("sub")
    db = SessionLocal()
    try:
        if not user_exists(user_id, db):
            raise HTTPException(status_code=401, detail="User not found")
    finally:
        db.close()

    state = _encode_oauth_state(user_id)

    if TEST_MODE:
        return RedirectResponse(
            url=f"/integrations/strava/callback?code=stub_code_test&scope=read,activity:read_all&state={state}"
        )

    redirect_uri = STRAVA_REDIRECT_URI
    if redirect_uri.startswith("http://localhost"):
        base = str(request.base_url).rstrip("/")
        redirect_uri = f"{base}/integrations/strava/callback"

    params = urlencode({
        "client_id": STRAVA_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": "read,activity:read_all",
        "state": state,
    })
    return RedirectResponse(url=f"{STRAVA_AUTH_URL}?{params}")


@router.get("/callback")
async def strava_callback(
    code: str,
    request: Request,
    state: str | None = None,
) -> RedirectResponse:
    """Handle Strava OAuth callback. Exchanges code for tokens."""
    _require_strava()

    state_value: str | None = _decode_oauth_state(state)
    is_login_flow = state_value == _STRAVA_LOGIN_SENTINEL
    platform_user_id: str | None = None if is_login_flow else state_value

    db = SessionLocal()
    try:
        if TEST_MODE or code == "stub_code_test":
            stub_strava_id = "strava_stub_user_42"
            stub_athlete_name = "Test Athlete"

            if is_login_flow:
                # Strava-login: find or create user, then issue JWT
                user_id = _find_or_create_strava_user(db, stub_strava_id, stub_athlete_name)
                token_key = user_id
            else:
                # CSRF guard: state was missing, forged, expired, or replayed
                # → state_value is None → platform_user_id is None. Without
                # this check the TEST_MODE branch silently bound an account
                # to a fresh stub_session, masking the security guarantee
                # the state mechanism is meant to provide. Mirror the prod
                # ghost-fallback behavior below at line 900+.
                if not platform_user_id:
                    existing_acct = db.query(IntegrationAccount).filter(
                        IntegrationAccount.external_user_id == stub_strava_id,
                        IntegrationAccount.provider == "strava",
                    ).first()
                    if existing_acct:
                        platform_user_id = existing_acct.user_id
                    else:
                        log.warning(
                            "Strava callback (TEST_MODE): state lost / forged, no existing link — rejecting"
                        )
                        raise HTTPException(
                            status_code=400,
                            detail="Session expirée. Reconnectez-vous et réessayez.",
                        )
                token_key = platform_user_id
                # SECURITY (mirror of the prod path): never steal an athlete
                # link. Refuse if this athlete is already bound to a different
                # account — no relink, no migration.
                conflicting = db.query(IntegrationAccount).filter(
                    IntegrationAccount.external_user_id == stub_strava_id,
                    IntegrationAccount.provider == "strava",
                    IntegrationAccount.user_id != platform_user_id,
                ).first()
                if conflicting and not _try_self_merge_on_connect(
                    db, platform_user_id, conflicting
                ):
                    log.warning(
                        "Strava athlete %s already linked to user %s — refusing relink to %s (TEST_MODE)",
                        stub_strava_id, conflicting.user_id, platform_user_id,
                    )
                    return RedirectResponse(
                        url=f"{FRONTEND_URL}/strava?status=conflict&reason=strava_linked_other"
                    )

            # Upsert integration account
            acct = _get_strava_account(db, token_key)
            if not acct:
                acct = IntegrationAccount(
                    id=str(uuid.uuid4()),
                    user_id=token_key,
                    provider="strava",
                    access_token="stub_access_token",
                    refresh_token="stub_refresh_token",
                    expires_at=9_999_999_999,
                    external_user_id=stub_strava_id,
                    athlete_name=stub_athlete_name,
                )
                db.add(acct)
            else:
                acct.access_token = "stub_access_token"
                acct.refresh_token = "stub_refresh_token"
                acct.expires_at = 9_999_999_999
                acct.external_user_id = stub_strava_id
                if not acct.athlete_name:
                    acct.athlete_name = stub_athlete_name
            db.commit()

            if is_login_flow:
                user = db.query(User).filter(User.id == token_key).first()
                jwt_token = _create_token(token_key, user.email if user else "")
                redirect = RedirectResponse(
                    url=f"{FRONTEND_URL}/strava?status=connected&user_id={token_key}"
                )
                _set_auth_cookie(redirect, jwt_token)
                return redirect
            return RedirectResponse(url=f"{FRONTEND_URL}/strava?status=connected")

        try:
            client = await get_http_client()
            resp = await client.post(
                STRAVA_TOKEN_URL,
                data={
                    "client_id": STRAVA_CLIENT_ID,
                    "client_secret": STRAVA_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            # Capture-first so a sentry SDK error can't preempt the
            # actual stack capture (matches PR #357 fix). Wrapped in
            # try/except so a sentry blip can't swap the 502 we want
            # to surface to the user for an opaque InternalError.
            try:
                sentry_sdk.capture_exception(exc)
                sentry_sdk.set_tag("strava.phase", "oauth_token_exchange")
            except Exception:
                log.warning("sentry capture failed during oauth_token_exchange", exc_info=True)
            log.error("Strava token exchange failed", exc_info=True)
            raise HTTPException(status_code=502, detail=f"Strava token exchange failed: {exc}") from exc

        strava_user_id = str(data["athlete"]["id"])
        athlete = data.get("athlete", {})
        athlete_name = athlete.get("firstname") or athlete.get("username") or None

        if is_login_flow:
            user_id = _find_or_create_strava_user(db, strava_user_id, athlete_name)
            token_key = user_id
        else:
            if not platform_user_id:
                # State lost (Cloud Run scaled to zero, different instance, etc.)
                # Fallback: if this Strava account is already linked, reuse that user
                existing_acct = db.query(IntegrationAccount).filter(
                    IntegrationAccount.external_user_id == strava_user_id,
                    IntegrationAccount.provider == "strava",
                ).first()
                if existing_acct:
                    log.info("State lost but Strava %s already linked to user %s — reusing", strava_user_id, existing_acct.user_id)
                    platform_user_id = existing_acct.user_id
                else:
                    log.warning("Strava connect callback: state lost, no existing link for Strava %s — rejecting", strava_user_id)
                    raise HTTPException(status_code=400, detail="Session expirée. Reconnectez-vous et réessayez.")
            token_key = platform_user_id
            # SECURITY — link-on-connect never steals an athlete link. If this
            # Strava athlete is already bound to a DIFFERENT Chemins Communs
            # account, REFUSE: do not relink, do not migrate activities, do not
            # delete the other user (the old "ghost migration" did exactly that,
            # which was an account-hijack vector — a logged-in user could absorb
            # any account whose Strava athlete they could authorize). Keep the
            # existing link intact and surface a clear conflict to the frontend.
            conflicting = db.query(IntegrationAccount).filter(
                IntegrationAccount.external_user_id == strava_user_id,
                IntegrationAccount.provider == "strava",
                IntegrationAccount.user_id != platform_user_id,
            ).first()
            # ACCOUNT UNIFICATION (#477): if the athlete is bound to a
            # credential-less SYNTHETIC strava_<id>@strava.local account, that is
            # the SAME person who logged in with Strava before logging in with
            # email — self-merge it into the current user instead of refusing.
            # A real credentialed account is NEVER absorbed (anti-theft stands).
            if conflicting and not _try_self_merge_on_connect(
                db, platform_user_id, conflicting
            ):
                log.warning(
                    "Strava athlete %s already linked to user %s — refusing relink to %s",
                    strava_user_id, conflicting.user_id, platform_user_id,
                )
                return RedirectResponse(
                    url=f"{FRONTEND_URL}/strava?status=conflict&reason=strava_linked_other"
                )

        acct = _get_strava_account(db, token_key)
        if not acct:
            acct = IntegrationAccount(
                id=str(uuid.uuid4()),
                user_id=token_key,
                provider="strava",
                access_token=encrypt_token(data["access_token"]),
                refresh_token=encrypt_token(data["refresh_token"]),
                expires_at=data["expires_at"],
                external_user_id=strava_user_id,
                athlete_name=athlete_name,
            )
            db.add(acct)
        else:
            acct.access_token = encrypt_token(data["access_token"])
            acct.refresh_token = encrypt_token(data["refresh_token"])
            acct.expires_at = data["expires_at"]
            acct.external_user_id = strava_user_id
            acct.athlete_name = athlete_name
        db.commit()

        if is_login_flow:
            user = db.query(User).filter(User.id == token_key).first()
            jwt_token = _create_token(token_key, user.email if user else "")
            redirect = RedirectResponse(
                url=f"{FRONTEND_URL}/strava?status=connected&user_id={token_key}"
            )
            _set_auth_cookie(redirect, jwt_token)
            return redirect
        return RedirectResponse(url=f"{FRONTEND_URL}/strava?status=connected")
    finally:
        db.close()


@router.get("/status", response_model=StravaStatusResponse)
async def strava_status(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StravaStatusResponse:
    """Return whether Strava is connected for the current user."""
    needs_email = (current_user.email or "").lower().endswith("@strava.local")
    acct = _get_strava_account(db, current_user.user_id)
    if not acct:
        return StravaStatusResponse(connected=False, needs_email=needs_email)
    failures = acct.sync_failures or 0
    return StravaStatusResponse(
        connected=True,
        athlete_name=acct.athlete_name,
        athlete_id=acct.external_user_id,
        last_synced_at=acct.last_synced_at.isoformat() if acct.last_synced_at else None,
        reconnect_required=failures >= STRAVA_RECONNECT_THRESHOLD,
        sync_failures=failures,
        needs_email=needs_email,
    )


@router.get("/preview", response_model=StravaPreviewResponse)
async def strava_preview(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StravaPreviewResponse:
    """Return preview info for the consent screen before import."""
    user_id = current_user.user_id
    acct = _get_strava_account(db, user_id)
    if not acct:
        raise HTTPException(status_code=400, detail="Strava non connecté.")

    already_imported = db.query(Activity).filter(
        Activity.user_id == user_id,
        Activity.provider == "strava",
    ).count()

    estimated = 0
    if TEST_MODE:
        estimated = 42
    else:
        # Primary: paginate /athlete/activities so EVERY sport type counts
        # (/stats only exposes ride+run+swim, missing hike, walk, AlpineSki…).
        # `cached_count_athlete_activities` adds a 5-min per-user cache so
        # consent-screen re-renders don't re-paginate 25 pages of history.
        token = decrypt_token(acct.access_token)
        accurate = await cached_count_athlete_activities(user_id, token)
        if accurate is not None:
            estimated = accurate
        else:
            # Conservative fallback: /stats sum of ride+run+swim. Under-counts
            # for users with hike/walk/ski but better than 500-ing the preview.
            log.warning(
                "Falling back to /stats for activity estimate (user_id=%s)",
                user_id,
            )
            try:
                client = await get_http_client()
                resp = await client.get(
                    f"{STRAVA_API_BASE}/athletes/{acct.external_user_id}/stats",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10.0,
                )
                if resp.is_success:
                    stats = resp.json()
                    for key in ("all_ride_totals", "all_run_totals", "all_swim_totals"):
                        estimated += (stats.get(key) or {}).get("count", 0)
            except Exception as exc:
                # Capture-first so a sentry tag-set error can't skip the
                # capture itself (matches PR #357 fix). Whole sentry call
                # wrapped because this handler proceeds with the request
                # either way — sentry-blip should not become user-facing.
                try:
                    sentry_sdk.capture_exception(exc)
                    sentry_sdk.set_tag("strava.phase", "preview_stats_fallback")
                except Exception:
                    log.warning("sentry capture failed in preview_stats fallback", exc_info=True)
                log.warning("Failed to fetch Strava athlete stats fallback: %s", exc)

    return StravaPreviewResponse(
        athlete_name=acct.athlete_name,
        athlete_id=acct.external_user_id,
        estimated_activity_count=estimated,
        already_imported_count=already_imported,
    )


@router.post("/resync", response_model=ImportJobResponse)
async def strava_resync(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
) -> ImportJobResponse:
    """Re-trigger a full Strava import."""
    _require_strava()
    user_id = current_user.user_id

    if TEST_MODE:
        actual_count = db.query(Activity).filter(Activity.user_id == user_id).count()
        job = ImportJob(
            id=str(uuid.uuid4()), user_id=user_id, provider="strava",
            status="COMPLETED", imported_count=0, failed_count=0,
        )
        db.add(job)
        db.commit()
        resp = _job_to_response(job, db)
        resp.skipped_count = actual_count
        return resp

    acct = _get_strava_account(db, user_id)
    if not acct:
        raise HTTPException(status_code=400, detail="Strava non connecté.")

    job = ImportJob(
        id=str(uuid.uuid4()), user_id=user_id, provider="strava",
        status="RUNNING", imported_count=0, failed_count=0,
    )
    db.add(job)
    db.commit()
    if _trigger_cloud_run_job(job.id):
        log.info("Import %s delegated to Cloud Run Job", job.id)
    else:
        log.info("Import %s running in-process (BackgroundTasks fallback)", job.id)
        background_tasks.add_task(_run_strava_import, job.id, user_id, decrypt_token(acct.access_token))
    return _job_to_response(job, db)


@router.post("/disconnect", response_model=DisconnectResponse)
async def strava_disconnect(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DisconnectResponse:
    """Remove stored Strava tokens for the current user."""
    _require_strava()
    acct = _get_strava_account(db, current_user.user_id)
    if acct:
        db.delete(acct)
        db.commit()
    return DisconnectResponse(status="disconnected")


@router.post("/import_all", response_model=ImportJobResponse)
async def strava_import_all(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    body: ImportAllRequest | None = None,
) -> ImportJobResponse:
    """Start a background import of all Strava activities."""
    _require_strava()
    user_id = current_user.user_id
    contribute_heatmap = body.contribute_heatmap if body else True

    # Return existing running/pending job (prevent duplicate imports)
    existing = db.query(ImportJob).filter(
        ImportJob.user_id == user_id,
        ImportJob.provider == "strava",
        ImportJob.status.in_(["RUNNING", "PENDING"]),
    ).first()
    if existing:
        # Stale guard: if job has been "RUNNING" longer than the Cloud Run Job
        # wall-clock cap (now 7200 s — see infra/terraform/main.tf import_strava
        # job timeout), mark as failed and allow restart. The cutoff sits below
        # the cap so we never declare a still-healthy job stale on the very
        # next /import_all poll. PR D of the strava-tough-review audit
        # (Sev-2 #4).
        if existing.status == "RUNNING" and existing.created_at:
            from datetime import UTC, datetime
            age = (datetime.now(UTC) - existing.created_at).total_seconds()
            if age > 5400:  # 90 min
                existing.status = "FAILED"
                existing.last_error = "Timeout — import bloqué depuis plus de 90 min."
                db.commit()
            else:
                return _job_to_response(existing, db)
        else:
            return _job_to_response(existing, db)

    job_id = str(uuid.uuid4())

    if TEST_MODE:
        actual_count = db.query(Activity).filter(Activity.user_id == user_id).count()
        job = ImportJob(
            id=job_id, user_id=user_id, provider="strava",
            status="COMPLETED", imported_count=0, failed_count=0,
        )
        db.add(job)
        db.commit()
        resp = _job_to_response(job, db)
        resp.skipped_count = actual_count
        return resp

    acct = _get_strava_account(db, user_id)
    if not acct:
        raise HTTPException(
            status_code=400,
            detail="Compte Strava non connecté. Reconnectez Strava depuis la page d'accueil.",
        )

    # Persist the user's heatmap-contribution choice on the account so
    # subsequent webhook events read it directly (PR #348 / audit S2.7).
    # Pre-PR-348 the webhook worker queried the user's most-recent
    # Activity row on every event — ~500 redundant SELECTs/week at
    # friends-beta scale. Now stored once at import-start time.
    #
    # IMPORTANT: this dirty attribute change is flushed by the
    # `db.commit()` below that also writes the ImportJob row. A future
    # refactor that splits the ImportJob save out (e.g. into a service
    # layer) MUST also retain this commit boundary — otherwise the
    # Cloud Run Job spins up before the new preference lands and the
    # next webhook event reads the stale value.
    acct.contribute_heatmap = contribute_heatmap

    # Store heatmap preference in cursor field as JSON
    cursor_data = json.dumps({"contribute_heatmap": contribute_heatmap})
    job = ImportJob(
        id=job_id, user_id=user_id, provider="strava",
        status="RUNNING", imported_count=0, failed_count=0,
        cursor=cursor_data,
    )
    db.add(job)
    db.commit()
    if _trigger_cloud_run_job(job_id):
        log.info("Import %s delegated to Cloud Run Job", job_id)
    else:
        log.info("Import %s running in-process (BackgroundTasks fallback)", job_id)
        background_tasks.add_task(_run_strava_import, job_id, user_id, decrypt_token(acct.access_token))
    return _job_to_response(job, db)


@router.get("/jobs/active", response_model=ImportJobResponse | None)
async def get_active_import_job(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ImportJobResponse | None:
    """Get the user's currently running import job, if any."""
    _require_strava()
    job = db.query(ImportJob).filter(
        ImportJob.user_id == current_user.user_id,
        ImportJob.provider == "strava",
        ImportJob.status.in_(["RUNNING", "PENDING"]),
    ).order_by(ImportJob.created_at.desc()).first()
    if not job:
        return None
    return _job_to_response(job, db)


@router.get("/jobs/{job_id}", response_model=ImportJobResponse)
async def get_import_job(
    job_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ImportJobResponse:
    """Get the current status of an import job."""
    _require_strava()
    job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return _job_to_response(job, db)
