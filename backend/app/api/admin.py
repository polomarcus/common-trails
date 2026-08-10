"""Admin dashboard endpoint — lightweight global stats for monitoring.

Protected: requires authenticated user with is_admin=True.
"""
import asyncio
import logging
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, text
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user
from app.db.models import Activity, DfciEdge, HeatCell, HeatEdge, IntegrationAccount, Route, TrailEdge, User
from app.db.session import get_db

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(user: Annotated[AuthenticatedUser, Depends(get_current_user)]) -> AuthenticatedUser:
    """Dependency that ensures the current user is an admin."""
    if not user.is_admin:
        raise HTTPException(403, "Admin access required")
    return user


def _heat_edge_stats(db: Session) -> dict:
    """Heavy ``heat_edges`` aggregates for the admin dashboard, bounded + fail-soft.

    On the live db-f1-micro these are full scans of the ~5 M-row ``heat_edges``
    table. An unbounded ``SELECT count(*) FROM heat_edges`` full scan severed
    the f1-micro connection in the ``build_pmtiles`` path (#442, "server closed
    the connection unexpectedly"); the SAME pattern on this now-live, un-frozen
    admin endpoint (#443) must never hang the request or drop the pooled
    connection. Each scan runs under a ``SET LOCAL statement_timeout`` (scoped
    to this transaction only, so it can't leak onto the next checkout — same
    guard as the MVT tile endpoint in ``api/heatmap.py``) and inside a
    try/except that degrades to a ``-1`` "unknown" sentinel instead of 500-ing
    the whole dashboard. A bounded miss is strictly better than a severed
    connection.
    """
    timeout_ms = int(os.environ.get("ADMIN_STAT_TIMEOUT_MS", "15000"))
    edges = cells = contributors = osm_tagged = -1
    dead_ends = total_vertices = -1

    try:
        db.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
        edges = db.query(func.count(HeatEdge.id)).scalar() or 0
        cells = db.query(func.count(HeatCell.id)).scalar() or 0
        contributors = db.query(func.coalesce(func.max(HeatEdge.user_count), 0)).scalar() or 0
        # Raw SQL: osm_way_id is a migration-added column the ORM doesn't expose.
        osm_tagged = db.execute(
            text("SELECT count(*) FROM heat_edges WHERE osm_way_id IS NOT NULL")
        ).scalar() or 0
    except Exception:
        log.warning("admin: heat_edges count scan aborted (statement_timeout / OOM guard)", exc_info=True)
        db.rollback()

    # Data quality: % of heat_edges tagged with an OSM way (good render path)
    # vs grid-fallback rows (NULL osm_way_id). PR #247 alerting fires > 0.20.
    if edges > 0 and osm_tagged >= 0:
        grid_fallback = edges - osm_tagged
        osm_way_id_pct = round(100.0 * osm_tagged / edges, 1)
        grid_fallback_pct = round(100.0 * grid_fallback / edges, 1)
    else:
        grid_fallback = -1 if edges < 0 else 0
        osm_way_id_pct = grid_fallback_pct = 0.0

    # Graph health: dead-end vertices + connectivity. O(n) endpoint aggregation
    # over the whole table — independently guarded so a slow plan here can't
    # sever the connection either.
    try:
        db.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
        dead_ends = db.execute(text("""
            WITH endpoints AS (
                SELECT round(ST_X(ST_StartPoint(geometry))::numeric, 5) AS x,
                       round(ST_Y(ST_StartPoint(geometry))::numeric, 5) AS y FROM heat_edges
                UNION ALL
                SELECT round(ST_X(ST_EndPoint(geometry))::numeric, 5),
                       round(ST_Y(ST_EndPoint(geometry))::numeric, 5) FROM heat_edges
            )
            SELECT COUNT(*) FROM (
                SELECT x, y FROM endpoints GROUP BY x, y HAVING COUNT(*) = 1
            ) dead
        """)).scalar() or 0
        total_vertices = db.execute(text("""
            WITH endpoints AS (
                SELECT round(ST_X(ST_StartPoint(geometry))::numeric, 5) AS x,
                       round(ST_Y(ST_StartPoint(geometry))::numeric, 5) AS y FROM heat_edges
                UNION ALL
                SELECT round(ST_X(ST_EndPoint(geometry))::numeric, 5),
                       round(ST_Y(ST_EndPoint(geometry))::numeric, 5) FROM heat_edges
            )
            SELECT COUNT(DISTINCT (x, y)) FROM endpoints
        """)).scalar() or 0
    except Exception:
        log.warning("admin: heat_edges graph-health scan aborted (statement_timeout / OOM guard)", exc_info=True)
        db.rollback()
        dead_ends = total_vertices = -1

    return {
        "heatmap": {
            "edges": edges,
            "cells": cells,
            "max_contributors_on_edge": contributors,
            "osm_tagged": osm_tagged,
            "grid_fallback": grid_fallback,
            "osm_way_id_pct": osm_way_id_pct,
            "grid_fallback_pct": grid_fallback_pct,
        },
        "graph_health": {
            "total_vertices": total_vertices,
            "dead_ends": dead_ends,
            "dead_end_pct": round(dead_ends / total_vertices * 100, 1) if total_vertices > 0 else 0,
        },
    }


@router.get("/dashboard")
async def admin_dashboard(
    _admin: Annotated[AuthenticatedUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Global platform stats for the admin monitoring page."""
    # Users
    total_users = db.query(func.count(User.id)).scalar() or 0
    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    signups_7d = db.query(func.count(User.id)).filter(User.created_at >= week_ago).scalar() or 0
    signups_30d = db.query(func.count(User.id)).filter(User.created_at >= month_ago).scalar() or 0
    # "Active" = uploaded an activity in the window. Single source of truth for engagement.
    active_7d = db.query(func.count(func.distinct(Activity.user_id))).filter(Activity.created_at >= week_ago).scalar() or 0
    active_30d = db.query(func.count(func.distinct(Activity.user_id))).filter(Activity.created_at >= month_ago).scalar() or 0

    # Activities
    total_activities = db.query(func.count(Activity.id)).scalar() or 0
    total_distance = db.query(func.coalesce(func.sum(Activity.distance_m), 0)).scalar()
    total_elevation = db.query(func.coalesce(func.sum(Activity.elevation_gain_m), 0)).scalar()

    # Provider split (strava vs file vs anything else). The model stores
    # the provider as a non-null string ('strava' for OAuth import, 'file'
    # for manual GPX/ZIP upload — see Activity.provider in db/models.py).
    provider_rows = (
        db.query(Activity.provider, func.count(Activity.id))
        .group_by(Activity.provider)
        .all()
    )
    activities_by_provider = {row[0] or "unknown": row[1] for row in provider_rows}

    # Activity breakdown by sport
    sport_rows = (
        db.query(Activity.sport, func.count(Activity.id), func.coalesce(func.sum(Activity.distance_m), 0))
        .group_by(Activity.sport)
        .all()
    )
    activities_by_sport = {
        row[0] or "unknown": {"count": row[1], "distance_m": float(row[2])}
        for row in sport_rows
    }

    # Routes
    total_routes = db.query(func.count(Route.id)).scalar() or 0
    route_vis = (
        db.query(Route.visibility, func.count(Route.id))
        .group_by(Route.visibility)
        .all()
    )
    routes_by_visibility = {row[0]: row[1] for row in route_vis}

    # Heatmap + graph-health aggregates — the only full scans of the ~5 M-row
    # heat_edges table on this endpoint. Bounded + fail-soft (see helper) so a
    # pathological plan on db-f1-micro can never sever the pooled connection.
    heat_stats = _heat_edge_stats(db)

    # DFCI + trails
    total_dfci = db.query(func.count(DfciEdge.id)).scalar() or 0
    total_trails = db.query(func.count(TrailEdge.id)).scalar() or 0

    # DB size (pg_database_size)
    try:
        db_size = db.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))")).scalar()
    except Exception:
        db_size = "N/A"

    # Cloud Run injects K_REVISION + K_SERVICE into every container — no
    # gcloud / IAM dance needed to surface "which revision is live" on the
    # admin page. K_REVISION is empty for local dev (we still send it so
    # the frontend can render a "local" pill).
    revision = os.environ.get("K_REVISION", "")
    service = os.environ.get("K_SERVICE", "")

    # Number of Strava connections (cheap count; the per-account detail
    # lives on /admin/sync-status). Surfacing this on the dashboard
    # answers "how many friends linked Strava" without a second fetch.
    strava_connected = db.query(func.count(IntegrationAccount.id)).filter(
        IntegrationAccount.provider == "strava"
    ).scalar() or 0

    return {
        "users": {
            "total": total_users,
            "signups_7d": signups_7d,
            "signups_30d": signups_30d,
            "active_7d": active_7d,
            "active_30d": active_30d,
        },
        "activities": {
            "total": total_activities,
            "total_distance_km": round(float(total_distance) / 1000, 1),
            "total_elevation_m": round(float(total_elevation)),
            "by_sport": activities_by_sport,
            "by_provider": activities_by_provider,
        },
        "routes": {
            "total": total_routes,
            "by_visibility": routes_by_visibility,
        },
        "integrations": {
            "strava_connected": strava_connected,
        },
        "heatmap": heat_stats["heatmap"],
        "graph_health": heat_stats["graph_health"],
        "network": {
            "dfci_edges": total_dfci,
            "trail_edges": total_trails,
        },
        "database": {
            "size": db_size,
        },
        "system": {
            "revision": revision,
            "service": service,
            "is_prod": bool(revision),
        },
    }


def _heatmap_freshness(db: Session) -> dict:
    """Level-1 "is it alive + fresh?" panel — ALL cheap, indexed reads.

    Deliberately touches only small / indexed tables (``activities``,
    ``heat_edges_agg``, ``import_jobs``, ``integration_accounts``) — NEVER a
    raw ``heat_edges`` scan (off-limits on this now-live db-f1-micro endpoint).
    Each probe is individually fail-soft so a hiccup degrades one field to
    ``null`` rather than 500-ing the panel.
    """
    # Strava reconnect threshold — same SSOT constant the /integrations/strava
    # /status endpoint uses (local import avoids a module-load import cycle).
    from app.api.integrations_strava import STRAVA_RECONNECT_THRESHOLD

    def _row(sql: str):
        try:
            return db.execute(text(sql)).first()
        except Exception:
            log.warning("admin freshness probe failed: %s", sql.split("FROM")[-1].strip(), exc_info=True)
            db.rollback()
            return None

    # Last activity: most-recent INGEST (created_at) + most-recent RIDE
    # (activity_date). Both cheap on the small beta activities table.
    r = _row("SELECT MAX(created_at), MAX(activity_date) FROM activities")
    last_activity_at = r[0].isoformat() if r and r[0] else None
    last_activity_date = r[1].isoformat() if r and r[1] else None

    # Heatmap rebuild freshness — reuse the /readyz agg-freshness logic.
    r = _row("SELECT COUNT(*), MAX(updated_at) FROM heat_edges_agg")
    heat_edges_agg = int(r[0] or 0) if r else 0
    heat_agg_updated_at = r[1].isoformat() if r and r[1] else None

    # Last resync run + status — the latest Strava import job (tiny table).
    r = _row(
        "SELECT status, updated_at FROM import_jobs "
        "WHERE provider = 'strava' ORDER BY updated_at DESC LIMIT 1"
    )
    last_resync_status = r[0] if r else None
    last_resync_at = r[1].isoformat() if r and r[1] else None

    # Strava integration health — same rule as /integrations/strava/status:
    # reconnect_required once sync_failures crosses the threshold.
    r = _row(
        "SELECT COUNT(*), COALESCE(MAX(sync_failures), 0) "
        "FROM integration_accounts WHERE provider = 'strava'"
    )
    strava_connected = int(r[0] or 0) if r else 0
    strava_sync_failures = int(r[1] or 0) if r else 0

    return {
        "last_activity_at": last_activity_at,
        "last_activity_date": last_activity_date,
        "heat_agg_updated_at": heat_agg_updated_at,
        "heat_edges_agg": heat_edges_agg,
        "last_resync_at": last_resync_at,
        "last_resync_status": last_resync_status,
        "strava_connected": strava_connected,
        "strava_sync_failures": strava_sync_failures,
        "strava_reconnect_required": strava_sync_failures >= STRAVA_RECONNECT_THRESHOLD,
        "reconnect_threshold": STRAVA_RECONNECT_THRESHOLD,
        "webhook_subscription_present": bool(
            os.environ.get("STRAVA_WEBHOOK_SUBSCRIPTION_ID", "").strip()
        ),
    }


@router.get("/heatmap-metrics")
async def admin_heatmap_metrics(
    _admin: Annotated[AuthenticatedUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=365)] = 90,
) -> dict:
    """Heatmap MONITORING: Level-1 freshness object + Level-2 evolution series.

    ``freshness`` — cheap live "is it alive + fresh?" probe (see
    ``_heatmap_freshness``). ``series`` — the last ``limit`` ``heatmap_metrics``
    snapshots in CHRONOLOGICAL (ascending) order, ready to plot. Both paths are
    light on db-f1-micro: the series is an indexed ``ORDER BY captured_at DESC
    LIMIT N`` (never a scan), the freshness object touches only small/indexed
    tables — the raw 5 M-row ``heat_edges`` table is never scanned here.
    """
    freshness = _heatmap_freshness(db)

    series: list[dict] = []
    try:
        rows = db.execute(
            text(
                "SELECT captured_at, heat_edges, agg_ways, activities, "
                "contributors, network_km, grid_fallback_pct, source "
                "FROM heatmap_metrics ORDER BY captured_at DESC LIMIT :lim"
            ),
            {"lim": limit},
        ).fetchall()
        # Chronological ascending for the sparkline (fetched DESC for the index).
        for row in reversed(rows):
            series.append({
                "captured_at": row[0].isoformat() if row[0] else None,
                "heat_edges": int(row[1] or 0),
                "agg_ways": int(row[2] or 0),
                "activities": int(row[3] or 0),
                "contributors": int(row[4] or 0),
                "network_km": float(row[5]) if row[5] is not None else 0.0,
                "grid_fallback_pct": float(row[6]) if row[6] is not None else None,
                "source": row[7],
            })
    except Exception:
        log.warning("admin: heatmap_metrics series read failed", exc_info=True)
        db.rollback()

    return {"freshness": freshness, "series": series, "count": len(series)}


@router.get("/routes/recent")
async def admin_recent_routes(
    _admin: Annotated[AuthenticatedUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Most recently created routes with their author. For "what got built lately" on the admin page."""
    rows = (
        db.query(
            Route.id,
            Route.name,
            Route.sport,
            Route.visibility,
            Route.distance_m,
            Route.elevation_gain_m,
            Route.created_at,
            User.username,
        )
        # User.id is UUID, Route.owner_id is String(36); cast for the JOIN.
        .outerjoin(User, cast(User.id, String) == Route.owner_id)
        .filter(Route.deleted_at.is_(None))
        .order_by(Route.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "limit": limit,
        "items": [
            {
                "id": str(r[0]),
                "name": r[1],
                "sport": r[2],
                "visibility": r[3],
                "distance_m": r[4],
                "elevation_gain_m": r[5],
                "created_at": r[6].isoformat() if r[6] else None,
                "username": r[7],
            }
            for r in rows
        ],
    }


@router.get("/activities/recent")
async def admin_recent_activities(
    _admin: Annotated[AuthenticatedUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Most recently uploaded activities with their author + provider. Lets the admin see GPX vs Strava ingress in real time."""
    rows = (
        db.query(
            Activity.id,
            Activity.name,
            Activity.sport,
            Activity.provider,
            Activity.distance_m,
            Activity.elevation_gain_m,
            Activity.activity_date,
            Activity.created_at,
            User.username,
        )
        # User.id is UUID, Activity.user_id is String(36); cast for the JOIN.
        .outerjoin(User, cast(User.id, String) == Activity.user_id)
        .order_by(Activity.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "limit": limit,
        "items": [
            {
                "id": str(r[0]),
                "name": r[1],
                "sport": r[2],
                "provider": r[3],
                "distance_m": r[4],
                "elevation_gain_m": r[5],
                "activity_date": r[6].isoformat() if r[6] else None,
                "created_at": r[7].isoformat() if r[7] else None,
                "username": r[8],
            }
            for r in rows
        ],
    }


@router.get("/sync-status")
async def admin_sync_status(
    _admin: Annotated[AuthenticatedUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Per-user Strava sync status for admin monitoring."""
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

    users.sort(key=lambda u: u["delay_days"] if u["delay_days"] is not None else 9999, reverse=True)

    return {
        "total_connected": len(users),
        "stale_30d": sum(1 for u in users if (u["delay_days"] or 9999) > 30),
        "token_expired": sum(1 for u in users if u["token_expired"]),
        "disabled": sum(1 for u in users if u["disabled"]),
        "users": users,
    }


@router.get("/users")
async def admin_users(
    _admin: Annotated[AuthenticatedUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Per-user observability rows for the admin USER dashboard.

    One row per registered user with: identity (id/email/username/is_admin/
    created_at), Strava linkage (athlete id → profile link, name, connect +
    sync health incl. the /status `reconnect_required` flag), and a traces
    summary (count + per-sport breakdown + total distance + first/last date +
    provider split + skipped/failed import counts).

    Efficiency (runs on db-f1-micro): the heavy tables (`activities`,
    `import_jobs`) are touched by exactly THREE `GROUP BY user_id` scans
    (indexed column) — never a per-user N+1. The users + strava-accounts
    tables are tiny (friends-beta) and fully materialised into dicts.

    Deliberately OMITTED: per-user grid-fallback vs OSM-matched heat_edge
    ratio. `heat_edges` has no `user_id`; attributing rows to a user means
    joining `heat_edge_contributors` (millions of rows) to `heat_edges` by
    `edge_key` filtered on a per-user hash — not cheap on f1-micro. The
    GLOBAL ratio already lives on `/admin/dashboard` (osm_way_id_pct).
    """
    # Reconnect threshold is the SSOT from the Strava module (env-driven,
    # default 3). Lazy import to avoid a heavy/circular top-level import.
    from app.api.integrations_strava import STRAVA_RECONNECT_THRESHOLD

    # ── Users (tiny table — beta) ────────────────────────────────────────
    user_rows = db.query(
        User.id, User.email, User.username, User.is_admin, User.created_at
    ).order_by(User.created_at.asc()).all()

    # ── Strava accounts keyed by user_id (tiny table) ────────────────────
    strava_accounts = {
        acct.user_id: acct
        for acct in db.query(IntegrationAccount).filter(
            IntegrationAccount.provider == "strava"
        ).all()
    }

    # ── Activities aggregated by (user_id, sport): count, distance,
    #    first/last date. One indexed GROUP BY scan. ──────────────────────
    sport_agg = (
        db.query(
            Activity.user_id,
            Activity.sport,
            func.count(Activity.id),
            func.coalesce(func.sum(Activity.distance_m), 0),
            func.min(Activity.activity_date),
            func.max(Activity.activity_date),
        )
        .group_by(Activity.user_id, Activity.sport)
        .all()
    )

    # ── Activities aggregated by (user_id, provider): count. ─────────────
    provider_agg = (
        db.query(Activity.user_id, Activity.provider, func.count(Activity.id))
        .group_by(Activity.user_id, Activity.provider)
        .all()
    )

    # ── Import-job outcomes per user: skipped/failed counts (debug: how
    #    many traces the ingest DROPPED, e.g. ambiguous-sport skips). ─────
    from app.db.models import ImportJob
    import_agg = (
        db.query(
            ImportJob.user_id,
            func.coalesce(func.sum(ImportJob.skipped_count), 0),
            func.coalesce(func.sum(ImportJob.failed_count), 0),
        )
        .group_by(ImportJob.user_id)
        .all()
    )

    # ── Fold the GROUP BY rows into per-user accumulators ────────────────
    by_user_sport: dict[str, dict] = {}
    for uid, sport, count, dist, first_d, last_d in sport_agg:
        acc = by_user_sport.setdefault(
            uid, {"total": 0, "total_distance_m": 0.0, "by_sport": {},
                  "first": None, "last": None}
        )
        acc["total"] += count
        acc["total_distance_m"] += float(dist)
        acc["by_sport"][sport or "unknown"] = {
            "count": count, "distance_m": float(dist),
        }
        if first_d and (acc["first"] is None or first_d < acc["first"]):
            acc["first"] = first_d
        if last_d and (acc["last"] is None or last_d > acc["last"]):
            acc["last"] = last_d

    by_user_provider: dict[str, dict[str, int]] = {}
    for uid, provider, count in provider_agg:
        by_user_provider.setdefault(uid, {})[provider or "unknown"] = count

    by_user_imports = {
        uid: {"skipped": int(skipped), "failed": int(failed)}
        for uid, skipped, failed in import_agg
    }

    now_ts = time.time()
    users = []
    for u_id, email, username, is_admin, created_at in user_rows:
        acct = strava_accounts.get(u_id)
        strava = None
        if acct:
            failures = acct.sync_failures or 0
            strava = {
                "athlete_id": acct.external_user_id,
                "athlete_name": acct.athlete_name,
                "connected_at": acct.created_at.isoformat() if acct.created_at else None,
                "expires_at": acct.expires_at,
                "token_expired": bool(acct.expires_at and acct.expires_at < now_ts),
                "last_synced_at": acct.last_synced_at.isoformat() if acct.last_synced_at else None,
                "sync_failures": failures,
                "reconnect_required": failures >= STRAVA_RECONNECT_THRESHOLD,
            }
        sport = by_user_sport.get(u_id, {})
        imports = by_user_imports.get(u_id, {"skipped": 0, "failed": 0})
        users.append({
            "id": str(u_id),
            "email": email,
            "username": username,
            "is_admin": bool(is_admin),
            "created_at": created_at.isoformat() if created_at else None,
            "strava": strava,
            "activities": {
                "total": sport.get("total", 0),
                "total_distance_m": sport.get("total_distance_m", 0.0),
                "by_sport": sport.get("by_sport", {}),
                "by_provider": by_user_provider.get(u_id, {}),
                "first_activity_date": sport["first"].isoformat() if sport.get("first") else None,
                "last_activity_date": sport["last"].isoformat() if sport.get("last") else None,
                "skipped_import_count": imports["skipped"],
                "failed_import_count": imports["failed"],
            },
        })

    return {"total_users": len(users), "users": users}


# Lifecycle statuses of a `pending_archives` row (migration 0060):
# awaiting_upload → (browser PUT) → uploaded → (job) processing → done | failed.
_ARCHIVE_STATUSES = ("awaiting_upload", "uploaded", "processing", "done", "failed")


@router.get("/archives")
async def admin_archives(
    _admin: Annotated[AuthenticatedUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Whole-archive upload queue (``pending_archives``) observability.

    Beta safety net: a friend's big-archive upload that FAILS in the paced
    drain is invisible to *them* by design (the upload page reports "nothing
    to wait for" once the PUT lands) — this endpoint is how the admin SEES
    stuck / failed archives. Returns (a) counts by lifecycle status and
    (b) the ~20 most recent rows with drain progress + the failure reason.

    Cheap on db-f1-micro: one row per uploaded WHOLE archive (friends-beta ⇒
    tens of rows, never per member), so both reads are tiny scans.
    """
    from app.db.models import PendingArchive

    counts: dict[str, int] = dict.fromkeys(_ARCHIVE_STATUSES, 0)
    for status, count in (
        db.query(PendingArchive.status, func.count(PendingArchive.id))
        .group_by(PendingArchive.status)
        .all()
    ):
        counts[status or "unknown"] = count

    rows = (
        db.query(
            PendingArchive.id,
            PendingArchive.user_id,
            User.email,
            PendingArchive.status,
            PendingArchive.attempts,
            PendingArchive.members_total,
            PendingArchive.imported,
            PendingArchive.skipped,
            PendingArchive.failed,
            PendingArchive.last_error,
            PendingArchive.created_at,
            PendingArchive.updated_at,
        )
        # User.id is UUID, PendingArchive.user_id is String(36); cast for the JOIN.
        .outerjoin(User, cast(User.id, String) == PendingArchive.user_id)
        .order_by(PendingArchive.created_at.desc())
        .limit(20)
        .all()
    )
    return {
        "counts": counts,
        "items": [
            {
                "id": str(r[0]),
                "user_id": str(r[1]),
                "email": r[2],
                "status": r[3],
                "attempts": r[4],
                "members_total": r[5],
                "imported": r[6],
                "skipped": r[7],
                "failed": r[8],
                # Truncate to ~300 chars, KEEPING THE HEAD — the original
                # traceback/reason is at the front and is the load-bearing
                # diagnostic signal (same convention as the ImportJob
                # last_error cap in admin_cancel_job below).
                "last_error": r[9][:300] if r[9] else None,
                "created_at": r[10].isoformat() if r[10] else None,
                "updated_at": r[11].isoformat() if r[11] else None,
            }
            for r in rows
        ],
    }


# A `processing` row untouched for this long is considered STRANDED (the
# drain job was killed mid-archive). Mirrors the reclaim window in
# app.jobs.ingest_pending_archives._claim_archives.
_ARCHIVE_STALE_PROCESSING = timedelta(hours=3)


@router.post("/archives/{archive_id}/requeue")
async def admin_requeue_archive(
    archive_id: str,
    _admin: Annotated[AuthenticatedUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """One-click recovery for a stuck archive: flip a ``failed`` (or stale
    ``processing``) row back to ``uploaded`` and kick the drain job.

    Safe to repeat: re-draining a partially-processed archive is idempotent
    (per-member ``file_hash`` dedup + #453 promotion in ``ingest_activity``).
    409 on ``done`` / ``awaiting_upload`` / FRESH ``processing`` — those are
    either finished or actively being worked on.
    """
    from app.db.models import PendingArchive
    from app.services.run_jobs import trigger_ingest_archives_job

    try:
        uuid.UUID(archive_id)
    except ValueError:
        raise HTTPException(404, "Unknown archive.") from None
    row = db.get(PendingArchive, archive_id)
    if row is None:
        raise HTTPException(404, "Unknown archive.")

    updated_at = row.updated_at
    if updated_at is not None and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    stale_processing = row.status == "processing" and (
        updated_at is None or datetime.now(UTC) - updated_at > _ARCHIVE_STALE_PROCESSING
    )
    if not (row.status == "failed" or stale_processing):
        raise HTTPException(
            409,
            f"Archive is '{row.status}' — only failed or stale-processing "
            "archives can be requeued.",
        )

    row.status = "uploaded"
    row.last_error = None
    db.add(row)
    db.commit()

    # Best-effort event-driven kick (same contract as /imports/strava-archive/
    # complete): a trigger failure must never fail the requeue — the daily
    # backstop scheduler drains the row anyway.
    enqueue = "scheduled"
    try:
        if trigger_ingest_archives_job():
            enqueue = "triggered"
    except Exception as exc:
        log.warning("ingest-archives job trigger failed after requeue: %s", exc)

    return {"archive_id": archive_id, "status": "uploaded", "enqueue": enqueue}


def _fetch_executions(job_name: str, limit: int = 5) -> list[dict]:
    """List the last N executions of a Cloud Run Job via the v2 REST API.

    Uses the in-container metadata server for auth (works only on Cloud
    Run; returns [] locally where the metadata server is unreachable).
    The common-trails-api SA has `roles/run.developer` which includes
    `run.executions.list` — no extra IAM grant needed.
    """
    project = os.environ.get("GCP_PROJECT")
    region = os.environ.get("GCP_REGION")
    if not project or not region:
        return []
    try:
        import httpx
        token_resp = httpx.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
            timeout=3.0,
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        url = (
            f"https://run.googleapis.com/v2/projects/{project}"
            f"/locations/{region}/jobs/{job_name}/executions?pageSize={limit}"
        )
        resp = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=5.0)
        resp.raise_for_status()
        execs = resp.json().get("executions", [])
    except Exception as exc:
        log.warning("Failed to list executions for %s: %s", job_name, exc)
        return []

    out = []
    for e in execs:
        # Status mapping: succeededCount/failedCount + completionTime drives
        # "ok/failed/running". Reads identically to the
        # `gcloud run jobs executions list --status.conditions[0]` view we
        # documented in the ingest-pipeline agent doc.
        completion = e.get("completionTime")
        succeeded = (e.get("succeededCount") or 0) > 0
        failed = (e.get("failedCount") or 0) > 0
        if not completion:
            status = "running"
        elif succeeded:
            status = "ok"
        elif failed:
            status = "failed"
        else:
            status = "unknown"
        out.append({
            "name": (e.get("name") or "").rsplit("/", 1)[-1],
            "start_time": e.get("startTime"),
            "completion_time": completion,
            "status": status,
            "succeeded": e.get("succeededCount") or 0,
            "failed": e.get("failedCount") or 0,
        })
    return out


_TRACKED_JOBS = [
    "common-trails-import-osm-prod",
    "common-trails-import-strava-prod",
    "common-trails-resync-strava-prod",
    "common-trails-group-edges-osm-prod",
    "common-trails-rebuild-heatmap-prod",
    "common-trails-migrate-edge-clustering-prod",
    "common-trails-recompute-elevation-gain-prod",
]


@router.get("/jobs/recent")
async def admin_recent_jobs(
    _admin: Annotated[AuthenticatedUser, Depends(require_admin)],
    per_job: Annotated[int, Query(ge=1, le=20)] = 3,
) -> dict:
    """Recent Cloud Run Job executions across all tracked jobs.

    Surfaces "did the last rebuild succeed / how long did it take" on the
    admin page so we don't have to leave the app for GCP Console.
    """
    items = []
    for job in _TRACKED_JOBS:
        for e in _fetch_executions(job, limit=per_job):
            items.append({**e, "job": job})
    # Most-recent first
    items.sort(key=lambda i: i.get("start_time") or "", reverse=True)
    return {"per_job": per_job, "items": items}


@router.post("/jobs/{job_id}/cancel")
async def admin_cancel_job(
    job_id: str,
    _admin: Annotated[AuthenticatedUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Force a stuck ImportJob to FAILED so its per-user lock releases.

    Use case: a Job container crashed before it could write status='FAILED'
    (e.g. the 2026-05-26 JWT_SECRET incident — container exited at module
    import). The row stays RUNNING forever, the per-user queue lock waits
    up to 6 h for that ghost. The job-startup zombie cleanup handles new
    job launches; this endpoint lets ops fix the *current* stuck state
    without an SQL Studio session.

    Idempotent: a job already in FAILED/COMPLETED is returned unchanged.
    """
    from app.db.models import ImportJob

    job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
    if not job:
        raise HTTPException(404, f"ImportJob {job_id} not found")

    if job.status in ("FAILED", "COMPLETED"):
        return {
            "id": str(job.id),
            "status": job.status,
            "changed": False,
            "message": f"Job already in terminal state ({job.status})",
        }

    prev_status = job.status
    job.status = "FAILED"
    # Cap last_error around 2 KB so repeated cancel/admin-action cycles
    # don't blow it up indefinitely (Postgres TEXT has no hard limit
    # but the UI + JSON serializer choke past a few KB).
    #
    # KEEP THE PREFIX, not the suffix — the original Python traceback
    # is at the head of last_error and is the load-bearing diagnostic
    # signal. Symmetric with the SQL `LEFT(..., 2048)` cap in the
    # zombie-cleanup UPDATE (which also keeps the head). A tail-slice
    # `[-2048:]` would silently destroy root-cause evidence after
    # repeated admin actions.
    suffix = " [cancelled by admin]"
    head = job.last_error or ""
    head_budget = 2048 - len(suffix)
    job.last_error = head[:head_budget] + suffix
    db.commit()
    log.warning(
        "Admin %s cancelled ImportJob %s (was %s, user=%s)",
        _admin.user_id, job_id, prev_status, job.user_id,
    )
    return {
        "id": str(job.id),
        "status": "FAILED",
        "changed": True,
        "previous_status": prev_status,
    }


@router.post("/resync")
async def admin_trigger_resync(
    _admin: Annotated[AuthenticatedUser, Depends(require_admin)],
    background_tasks: BackgroundTasks,
) -> dict:
    """Manually trigger the Strava resync job."""
    from app.api.integrations_strava import GCP_PROJECT, GCP_REGION

    resync_job_name = os.environ.get("RESYNC_JOB_NAME", "common-trails-resync-strava-prod")

    if GCP_PROJECT and GCP_REGION:
        try:
            import httpx
            token_resp = httpx.get(
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"},
                timeout=5.0,
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]

            url = (
                f"https://run.googleapis.com/v2/projects/{GCP_PROJECT}"
                f"/locations/{GCP_REGION}/jobs/{resync_job_name}:run"
            )
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                json={},
                timeout=10.0,
            )
            resp.raise_for_status()
            log.info("Triggered Cloud Run resync job %s", resync_job_name)
            return {"status": "triggered", "target": "cloud_run_job"}
        except Exception as exc:
            log.warning("Cloud Run trigger failed, falling back to background task: %s", exc)

    # Fallback: run in-process
    from app.jobs.resync_strava import resync_all

    background_tasks.add_task(lambda: asyncio.run(resync_all()))
    return {"status": "triggered", "target": "background_task"}
