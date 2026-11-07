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
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user
from app.db.models import Activity, ActivityPhoto
from app.db.session import get_db
from app.services.activity_deletion import delete_user_activity
from app.services.activity_sport import change_activities_sport
from app.services.display_geometry import (  # noqa: F401 — re-exported (tests)
    DISPLAY_GAP_BREAK_M,
    _split_at_gaps,
)
from app.services.ingest import (
    count_user_map_activities,
    get_activity_by_id,
    iter_user_map_activities,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["me"])


class ActivitiesResponse(BaseModel):
    type: str = "FeatureCollection"
    features: list
    total: int


def _activity_to_feature(act: dict, compact: bool = False) -> dict:
    """Shape a single activity dict into a GeoJSON Feature.

    The geometry is split at GPS gaps (``_split_at_gaps``) so the map never
    draws a straight line across a dropout — it may therefore be a
    ``MultiLineString``.

    ``compact=False`` (single-activity detail): every stored point, full
    precision. ``compact=True`` (the /me/activities list feeding "Ma heatmap"):
    the precomputed ``display_geojson`` cache (gap-split → Douglas-Peucker 2 m
    → 5-decimal rounding, see ``app.services.display_geometry``) when present,
    else the full-precision gap-split geometry (exactly the pre-cache payload,
    no extra CPU) — a GET never writes; ``app.cli.backfill_display_geojson``
    fills the cache.
    """
    cached = act.get("display_geojson") if compact else None
    # Not backfilled yet (or detail view): the pre-cache behaviour, gap split
    # only. Running Douglas-Peucker here would make every un-cached row cost
    # MORE CPU per request than before the cache existed.
    geometry = json.loads(cached) if cached else _split_at_gaps(json.loads(act["geometry_geojson"]))
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

    **SQL-side filter/order/limit** (memory fix, follow-up to the 32 MiB one):
    the old code called ``get_user_activities`` — EVERY column of the user's
    WHOLE corpus (geometry_geojson included) materialized in web RAM, then
    filtered and sliced ``[-limit:]`` in Python. A real beta user has 14,460
    activities (≈0.4–1.4 GB) on a 512Mi instance → OOM. Now the sport +
    geometry filters, the "most recent ``limit``" selection and the column
    projection all happen in Postgres, and rows stream through a server-side
    cursor (``yield_per``) — memory is O(window), not O(corpus). Ordering
    contract unchanged: features ascend by effective date, newest LAST
    (what ``[-limit:]`` on the insertion-ordered list produced).

    **Compact geometry** (payload fix): each feature carries the DISPLAY
    geometry (gap-split → Douglas-Peucker 2 m → 5-decimal coords), read from
    the precomputed ``display_geojson`` cache (rows not backfilled yet get the
    pre-cache full-precision gap-split geometry) — ~19× smaller gzipped on a
    dense 1 Hz ride. Full precision stays on
    ``/me/activities/{id}``.
    """
    user_id = current_user.user_id

    # ``total`` is emitted at the head of the body, so it must be known before
    # the rows stream: a cheap indexed COUNT, capped at ``limit``. (A write
    # landing between the COUNT and the row scan could skew it by a row —
    # same near-never mismatch class as the defensive per-feature skip below,
    # accepted for O(1) memory.)
    total = min(count_user_map_activities(user_id, sport=sport), limit)

    def _iter_body() -> Iterator[bytes]:
        yield f'{{"type":"FeatureCollection","total":{total},"features":['.encode()
        first = True
        for act in iter_user_map_activities(user_id, sport=sport, limit=limit):
            try:
                feature = _activity_to_feature(act, compact=True)
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


# ── Sport correction (single + bulk) ─────────────────────────────────────────
# A wrong sport at import pollutes the wrong community layer; the owner can fix
# it. See app.services.activity_sport for what a change touches (heat partition
# re-write in matched mode, debounced PMTiles rebuild).

# Bulk cap — one request re-classifies at most this many activities. The grain
# is an explicit id list: activities carry no import-batch id (the archive
# tables don't link back to them reliably), so the client sends the ids it shows.
BULK_SPORT_MAX_IDS = 500


def _validate_editable_sport(v: str) -> str:
    from app.config import EDITABLE_SPORTS

    if v not in EDITABLE_SPORTS:
        raise ValueError(f"sport must be one of {sorted(EDITABLE_SPORTS)}")
    return v


# Validated against the config SSOT → an unknown sport is a 422.
EditableSport = Annotated[str, AfterValidator(_validate_editable_sport)]


class SportUpdate(BaseModel):
    sport: EditableSport


class BulkSportUpdate(BaseModel):
    activity_ids: list[str] = Field(min_length=1, max_length=BULK_SPORT_MAX_IDS)
    sport: EditableSport


class BulkSportResult(BaseModel):
    sport: str
    matched: int  # owned activities found among the ids
    updated: int  # of which the sport actually changed
    updated_ids: list[str]


def _valid_uuids(ids: list[str]) -> list[str]:
    out = []
    for i in ids:
        try:
            uuid.UUID(i)
        except ValueError:
            continue
        out.append(i)
    return out


@router.patch("/me/activities/sport", response_model=BulkSportResult)
def bulk_update_me_activities_sport(
    body: BulkSportUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BulkSportResult:
    """Change the sport of several of the user's activities at once (≤500 ids).

    Ids that don't exist or belong to another user are ignored (counted out of
    ``matched``) — no existence leak, and one stale id doesn't fail the batch.
    Declared BEFORE ``/me/activities/{activity_id}`` so ``sport`` is never
    captured as an id.
    """
    ids = _valid_uuids(list(dict.fromkeys(body.activity_ids)))
    found, changed = change_activities_sport(db, current_user.user_id, ids, body.sport)
    return BulkSportResult(
        sport=body.sport, matched=len(found), updated=len(changed), updated_ids=changed
    )


@router.patch("/me/activities/{activity_id}")
def update_me_activity_sport(
    activity_id: str,
    body: SportUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Correct the sport of one of the user's activities.

    404 when the activity doesn't exist OR belongs to another user — same
    opacity as GET/DELETE. Idempotent: same sport → 200, nothing re-written.
    """
    if not _valid_uuids([activity_id]):
        raise HTTPException(status_code=404, detail="Activity not found")
    found, changed = change_activities_sport(
        db, current_user.user_id, [activity_id], body.sport
    )
    if not found:
        raise HTTPException(status_code=404, detail="Activity not found")
    return {"id": activity_id, "sport": body.sport, "changed": bool(changed)}


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
