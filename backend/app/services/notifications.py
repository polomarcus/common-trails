"""Notification helpers — write a row when a long-running phase finishes.

The frontend polls `GET /me/notifications?unread_only=true` once on app
mount and shows a toast per unread row, so the user knows their Strava
GPS upgrade / photo import / monthly resync finished without having to
return to the import page.

Each emit opens its own SessionLocal: callers are background tasks or
Cloud Run Jobs and we don't want to entangle their lifecycle with the
notification write.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any

from app.db.models import Notification
from app.db.session import SessionLocal

log = logging.getLogger(__name__)


def emit_notification(
    user_id: str,
    kind: str,
    title: str,
    body: str = "",
    meta: dict[str, Any] | None = None,
) -> str | None:
    """Persist a notification row. Returns the new id, or None on failure.

    Failures are swallowed and logged: a missed notification must never
    take down the phase finisher that called it.
    """
    db = SessionLocal()
    try:
        notif = Notification(
            user_id=user_id,
            kind=kind,
            title=title,
            body=body,
            meta=meta,
        )
        db.add(notif)
        db.commit()
        return notif.id
    except Exception as exc:
        log.warning("Failed to emit notification (user=%s kind=%s): %s", user_id, kind, exc)
        with contextlib.suppress(Exception):
            db.rollback()
        return None
    finally:
        db.close()


def emit_activity_synced(
    user_id: str,
    activity_name: str | None,
    sport: str,
    provider_activity_id: str | None,
) -> str | None:
    """Emit an ``activity_synced`` toast for a GENUINELY-NEW activity.

    Called only from the incremental Strava sync paths (webhook worker +
    trailing-window resync) right after a confirmed-new ingest — NOT from
    the shared bulk-ingest function, so the initial import backfill never
    spams hundreds of toasts on first connect.

    The FR title/body are fallbacks for non-toast consumers; the frontend
    toast re-localises via i18n keys using ``meta.name`` (see
    NotificationToasts + notification-display). Fully fail-soft:
    ``emit_notification`` swallows its own failures, and building the
    payload here cannot raise on the given inputs.
    """
    name = (activity_name or "").strip()
    # PERSONAL sync only: Strava-API activities stay in the user's private view
    # and do NOT enter the community heatmap (compliance pivot #453). The copy
    # must not imply the common/public map. The frontend re-localises via i18n
    # keys (notif.activitySynced.*); these FR strings are the non-toast fallback.
    body = (
        f"Ta sortie « {name} » est synchronisée dans ta vue perso ✓"
        if name
        else "Ta sortie est synchronisée dans ta vue perso ✓"
    )
    return emit_notification(
        user_id=user_id,
        kind="activity_synced",
        title="Sortie synchronisée",
        body=body,
        meta={
            "name": name or None,
            "sport": sport,
            "provider_activity_id": provider_activity_id,
        },
    )
