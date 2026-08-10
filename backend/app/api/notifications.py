"""Per-user notification API — owner-scoped only.

Contract:
    GET  /me/notifications?unread_only=true&limit=20  -> list of notifications
    POST /me/notifications/{id}/read                  -> mark single read
    POST /me/notifications/read_all                   -> mark all read

The frontend polls the list endpoint once on app mount (no continuous
polling, no websocket — this is friends-beta).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user
from app.db.models import Notification
from app.db.session import get_db

router = APIRouter(tags=["notifications"])


class NotificationItem(BaseModel):
    id: str
    kind: str
    title: str
    body: str
    meta: dict[str, Any] | None
    created_at: datetime
    read_at: datetime | None


class NotificationList(BaseModel):
    items: list[NotificationItem]
    unread_count: int


class MarkReadResponse(BaseModel):
    status: str
    marked: int


@router.get("/me/notifications", response_model=NotificationList)
async def list_notifications(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    unread_only: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
) -> NotificationList:
    """List notifications for the current user, newest first."""
    user_id = current_user.user_id

    q = db.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        q = q.filter(Notification.read_at.is_(None))
    rows = q.order_by(Notification.created_at.desc()).limit(limit).all()

    unread_count = (
        db.query(Notification)
        .filter(Notification.user_id == user_id, Notification.read_at.is_(None))
        .count()
    )

    items = [
        NotificationItem(
            id=str(r.id),
            kind=r.kind,
            title=r.title,
            body=r.body or "",
            meta=r.meta,
            created_at=r.created_at,
            read_at=r.read_at,
        )
        for r in rows
    ]
    return NotificationList(items=items, unread_count=unread_count)


@router.post("/me/notifications/{notif_id}/read", response_model=MarkReadResponse)
async def mark_read(
    notif_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MarkReadResponse:
    """Mark a single notification as read. 404 if not owned by the caller."""
    user_id = current_user.user_id
    notif = (
        db.query(Notification)
        .filter(Notification.id == notif_id, Notification.user_id == user_id)
        .first()
    )
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")

    if notif.read_at is None:
        notif.read_at = datetime.now(UTC)
        db.commit()
        return MarkReadResponse(status="ok", marked=1)
    return MarkReadResponse(status="ok", marked=0)


@router.post("/me/notifications/read_all", response_model=MarkReadResponse)
async def mark_all_read(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MarkReadResponse:
    """Mark every unread notification for the current user as read."""
    from sqlalchemy import update

    user_id = current_user.user_id
    now = datetime.now(UTC)
    result = db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=now)
    )
    db.commit()
    return MarkReadResponse(status="ok", marked=result.rowcount or 0)
