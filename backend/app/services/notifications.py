"""Notification helpers — write a row when a long-running phase finishes.

The frontend polls `GET /me/notifications?unread_only=true` once on app
mount and shows a toast per unread row, so the user knows a long-running
phase finished without having to return to the import page.

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
