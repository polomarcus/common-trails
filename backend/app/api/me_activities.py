"""Personal activities API — private, returns user's own routes as GeoJSON.

GET /me/activities — GeoJSON FeatureCollection of the user's imported traces.
Used by the frontend map to display and edit the user's personal routes.
"""
import json
import logging
import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user
from app.db.models import Activity, ActivityPhoto
from app.db.session import get_db
from app.services.activity_deletion import delete_user_activity
from app.services.ingest import get_activity_by_id, get_user_activities

logger = logging.getLogger(__name__)

router = APIRouter(tags=["me"])


class ActivitiesResponse(BaseModel):
    type: str = "FeatureCollection"
    features: list
    total: int


def _activity_to_feature(act: dict) -> dict:
    """Shape a single activity dict into a GeoJSON Feature."""
    geometry = json.loads(act["geometry_geojson"])
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "id": act["id"],
            "name": act.get("name") or "",
            "sport": act.get("sport", "road"),
            "distance_m": act.get("distance_m"),
            "elevation_gain_m": act.get("elevation_gain_m"),
            "activity_date": act.get("activity_date"),
            "created_at": act.get("created_at"),
            "moving_time": act.get("moving_time"),
            "provider": act.get("provider", "file"),
            "provider_activity_id": act.get("provider_activity_id"),
            "total_photo_count": act.get("total_photo_count") or 0,
        },
    }


@router.get("/me/activities")
async def me_activities(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    sport: str | None = Query(None, description="road|gravel|mtb|offroad — omit for all"),
    limit: int = Query(200, ge=1, le=5000),
) -> StreamingResponse:
    """Return personal activities as a GeoJSON FeatureCollection (private).

    Each Feature contains the route LineString geometry + sport/distance metadata.
    Only activities with geometry are included.

    **Streamed** (``StreamingResponse``) rather than a single in-memory body:
    ``/map`` requests ``limit=5000`` and a heavy user already has thousands of
    full-resolution LineStrings, so one materialized ``ActivitiesResponse`` JSON
    body approaches/exceeds Cloud Run's 32 MiB non-streaming response cap → a
    silent 500 → the map shows no personal routes. A streamed response is NOT
    subject to that cap. The body is emitted incrementally — the header, then
    one ``json.dumps(feature)`` per row comma-separated, then the closing
    ``]}`` — so the full feature list is never built in memory. The JSON shape
    is byte-for-byte the same object the frontend already parses with
    ``resp.json()``: ``{"type":"FeatureCollection","total":N,"features":[...]}``.
    """
    user_id = current_user.user_id
    user_acts = [
        a for a in get_user_activities(user_id)
        if (sport is None or a["sport"] == sport)
        and a.get("geometry_geojson")
    ]

    # Keep the most recent `limit` activities
    user_acts = user_acts[-limit:]

    # ``total`` is emitted at the head of the body, so it is the count of
    # activities we are about to stream. Every row here already passed the
    # ``geometry_geojson`` filter, so a per-feature render failure
    # (json.JSONDecodeError on a corrupt stored geometry) is a near-never
    # defensive skip — the streamed feature count matches ``total`` in the
    # normal path (the shape the old materialized response produced).
    total = len(user_acts)

    def _iter_body() -> Iterator[bytes]:
        yield f'{{"type":"FeatureCollection","total":{total},"features":['.encode()
        first = True
        for act in user_acts:
            try:
                feature = _activity_to_feature(act)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            chunk = json.dumps(feature, separators=(",", ":"))
            if first:
                first = False
                yield chunk.encode()
            else:
                yield ("," + chunk).encode()
        yield b"]}"

    return StreamingResponse(_iter_body(), media_type="application/json")


class ContributionStatus(BaseModel):
    contributed: bool
    count: int
    last_contribution_at: str | None = None


@router.get("/me/contribution-status", response_model=ContributionStatus)
async def me_contribution_status(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ContributionStatus:
    """Whether THIS user has already contributed to the community heatmap.

    Powers the /strava landing banner ("you've already contributed N traces" vs
    "you haven't contributed yet"). Counts only COMMUNITY-ELIGIBLE activities via
    the provenance SSOT (``manual_upload`` + geometry + ``contribute_heatmap``) —
    the exact set the public map/exports use — so a user whose only rides came
    via the Strava API (personal-only) correctly reads as "not yet contributed".
    Cheap: a single indexed COUNT + MAX over the user's own rows.
    """
    from sqlalchemy import text as sa_text

    from app.services.provenance import community_eligible_conditions

    conds, params = community_eligible_conditions()
    elig = " AND ".join(conds)
    row = db.execute(
        sa_text(
            f"SELECT COUNT(*), MAX(created_at) FROM activities "
            f"WHERE user_id = :uid AND {elig}"
        ),
        {**params, "uid": current_user.user_id},
    ).fetchone()
    count = int(row[0] or 0)
    last = row[1].isoformat() if row and row[1] else None
    return ContributionStatus(contributed=count > 0, count=count, last_contribution_at=last)


@router.get("/me/activities/{activity_id}")
async def me_activity_by_id(
    activity_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> dict:
    """Return a single private activity as a GeoJSON Feature.

    Replaces the legacy frontend pattern of fetching the entire
    ``/me/activities?limit=5000`` list to find one row by id —
    O(1) DB lookup instead of O(N) over potentially 5000+ rows.
    """
    act = get_activity_by_id(current_user.user_id, activity_id)
    if not act or not act.get("geometry_geojson"):
        raise HTTPException(status_code=404, detail="Activity not found")
    try:
        return _activity_to_feature(act)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=500, detail="Activity geometry is malformed") from exc


@router.delete("/me/activities/{activity_id}", status_code=204)
async def delete_me_activity(
    activity_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Delete one of the user's activities + its community heat contributions (GDPR).

    Per-activity heat attribution exists (``heat_edge_contributors`` PK
    includes ``activity_id`` since migration 0052), so this removes the
    activity's contributor rows, recounts the touched ``heat_edges``
    (deleting edges left with zero contributors), and incrementally
    refreshes ``heat_edges_agg`` — see
    ``app.services.activity_deletion.delete_user_activity``.

    Not done per-deletion (deliberate): no PMTiles/artefact rebuild — the
    static rendered heatmap reflects the deletion at the next scheduled
    rebuild. The legacy ``heat_cells`` layer has no per-activity
    attribution and is only cleaned by a full rebuild / account deletion.

    Returns 404 when the activity doesn't exist OR belongs to another
    user — same opacity as the ``/me/activities/{id}`` GET.
    """
    try:
        uuid.UUID(activity_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Activity not found") from None

    if not delete_user_activity(db, current_user.user_id, activity_id):
        raise HTTPException(status_code=404, detail="Activity not found")
    return Response(status_code=204)


class ActivityPhotoOut(BaseModel):
    photo_id: str
    strava_photo_id: str | None
    thumbnail_url: str
    full_url: str
    lat: float | None
    lon: float | None
    caption: str | None


@router.get("/me/activities/{activity_id}/photos", response_model=list[ActivityPhotoOut])
async def me_activity_photos(
    activity_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ActivityPhotoOut]:
    """Return photos for a single activity (private, owner-only).

    Lazy-load companion to ``/me/activities/{id}``: the activity list endpoint
    only exposes ``total_photo_count`` so the frontend can decide whether to
    fetch this. Photos are stored as raw Strava CDN URLs in ``ActivityPhoto``
    (signed/time-limited URLs); freshly imported photos work fine, expired
    URLs need a re-import (beta limitation).

    Returns 404 when the activity doesn't exist OR belongs to another user —
    same opacity as the ``/me/activities/{id}`` by-id endpoint.
    """
    # Owner check via the activity row itself — single query, same scoping
    # rule as the rest of the /me/* family (Activity.user_id == current_user.user_id).
    act = db.query(Activity).filter(
        Activity.id == activity_id,
        Activity.user_id == current_user.user_id,
    ).first()
    if not act:
        # Don't leak existence of other users' activities — 404, not 403.
        raise HTTPException(status_code=404, detail="Activity not found")

    photos = db.query(ActivityPhoto).filter(
        ActivityPhoto.activity_id == activity_id,
        ActivityPhoto.user_id == current_user.user_id,
    ).order_by(ActivityPhoto.created_at.asc()).all()

    return [
        ActivityPhotoOut(
            photo_id=p.id,
            strava_photo_id=p.strava_photo_id,
            thumbnail_url=p.url_thumb,
            full_url=p.url_medium,
            lat=p.lat,
            lon=p.lon,
            caption=p.caption,
        )
        for p in photos
    ]
