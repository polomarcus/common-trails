"""Personal activities API — private, returns user's own routes as GeoJSON.

GET /me/activities — GeoJSON FeatureCollection of the user's imported traces.
Used by the frontend map to display and edit the user's personal routes.
"""
import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import AfterValidator, BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.services.activity_deletion import delete_user_activity
from app.services.activity_sport import (
    TooManyDefaultSportActivities,
    change_activities_sport,
    count_default_sport,
    list_default_sport,
    resolve_default_sport,
)
from app.services.display_geometry import (  # noqa: F401 — re-exported (tests)
    DISPLAY_GAP_BREAK_M,
    _split_at_gaps,
)
from app.services.ingest import (
    get_activity_by_id,
    iter_user_map_activities,
    user_map_activities_validator,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["me"])


class ActivitiesResponse(BaseModel):
    type: str = "FeatureCollection"
    features: list
    total: int


def _feature_properties(act: dict) -> dict:
    """The Feature ``properties`` — shared by the parsed and spliced paths so
    both emit exactly the same keys, defaults and order."""
    return {
        "id": act["id"],
        "name": act.get("name") or "",
        "sport": act.get("sport", "road"),
        # Road only by default (generic "Ride"): the UI asks the rider to
        # classify it. See activities.sport_is_default (0071).
        "sport_is_default": bool(act.get("sport_is_default", False)),
        "distance_m": act.get("distance_m"),
        "elevation_gain_m": act.get("elevation_gain_m"),
        "activity_date": act.get("activity_date"),
        "created_at": act.get("created_at"),
        # True when the row has no real date → kept off the public heatmap.
        "undated": bool(act.get("undated", False)),
        "moving_time": act.get("moving_time"),
        "provider": act.get("provider", "file"),
        "provider_activity_id": act.get("provider_activity_id"),
    }


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

    The list endpoint does not parse cached rows: it splices the cached text
    verbatim (``_compact_feature_json``). This stays the parsed reference path
    (detail view + NULL-cache fallback).
    """
    cached = act.get("display_geojson") if compact else None
    # Not backfilled yet (or detail view): the pre-cache behaviour, gap split
    # only. Running Douglas-Peucker here would make every un-cached row cost
    # MORE CPU per request than before the cache existed.
    geometry = json.loads(cached) if cached else _split_at_gaps(json.loads(act["geometry_geojson"]))
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": _feature_properties(act),
    }


_JSON_SEPARATORS = (",", ":")


def _compact_feature_json(act: dict) -> tuple[str, bool]:
    """One /me/activities list Feature as compact JSON text →
    ``(text, used_fallback)``.

    Cached rows (``display_geojson`` set): the cached geometry TEXT is spliced
    verbatim between the Feature envelope and ``json.dumps`` of the properties
    — never parsed. The cache is itself ``json.dumps(..., separators=(",", ":"))``
    output (``compact_display_geojson``, the only writer: ingest, Strava stream
    upgrade, backfill), so this is the same text the former
    ``json.dumps(json.loads(cached))`` round-trip emitted, without parsing and
    re-serialising every coordinate (~36 MB for a heavy account).

    Safety: the cache is valid JSON by construction (written only from
    ``json.dumps`` output, NULL on any failure). The O(1) guard below — a JSON
    object's first/last characters — catches what could plausibly go wrong
    with stored text (empty / truncated / non-object value): such a row takes
    the parsed path, which raises on invalid JSON so the caller skips the row
    (the previous behaviour) instead of emitting an invalid body.

    NULL cache: the parsed path, unchanged (gap-split of the full geometry).
    """
    cached = act.get("display_geojson")
    if cached and cached[0] == "{" and cached[-1] == "}":
        props = json.dumps(_feature_properties(act), separators=_JSON_SEPARATORS)
        return '{"type":"Feature","geometry":' + cached + ',"properties":' + props + "}", False
    feature = _activity_to_feature(act, compact=True)
    return json.dumps(feature, separators=_JSON_SEPARATORS), True


# The streamed body is emitted in chunks of at least this many bytes. One tiny
# chunk per feature made every ASGI send + gzip call pay per-message overhead;
# chunks >= 128 KiB also let Starlette's GZipMiddleware compress in a worker
# thread instead of on the event loop.
STREAM_CHUNK_BYTES = 256 * 1024

# Bump whenever the /me/activities OUTPUT FORMAT changes for unchanged rows
# (new property, different geometry derivation, …): it is part of the ETag,
# so a deploy that changes the format invalidates every client's cached copy.
ME_ACTIVITIES_FORMAT_VERSION = "2"
# Belt and braces for a forgotten bump: Cloud Run sets K_REVISION per deploy,
# so every deploy also invalidates cached copies (one refetch per client).
_DEPLOY_REVISION = os.environ.get("K_REVISION", "")

ME_ACTIVITIES_CACHE_CONTROL = "private, no-cache"


def _me_activities_etag(
    user_id: str, sport: str | None, limit: int, count: int, digest: str
) -> str:
    """Weak ETag over the format version, the user, the query params and the
    row validator (``user_map_activities_validator``). Weak because the same
    entity is served gzip-encoded or identity (GZipMiddleware)."""
    raw = "|".join(
        [ME_ACTIVITIES_FORMAT_VERSION, _DEPLOY_REVISION, user_id, sport or "*", str(limit), str(count), digest]
    )
    return 'W/"' + hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest() + '"'


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 If-None-Match: weak comparison over a comma-separated list."""
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    opaque = etag.removeprefix("W/")
    return any(c.strip().removeprefix("W/") == opaque for c in if_none_match.split(","))


# ``?download=true``: no "most recent N" window — a hard ceiling only.
EXPORT_MAX_ACTIVITIES = 200_000
EXPORT_FILENAME = "ma-heatmap-chemins-communs.geojson"


@router.get("/me/activities")
async def me_activities(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    sport: str | None = Query(None, description="road|gravel|mtb|offroad — omit for all"),
    limit: int = Query(200, ge=1, le=5000),
    download: bool = Query(False, description="Export: EVERY activity, as a .geojson attachment"),
    if_none_match: str | None = Header(None),
) -> Response:
    """Return personal activities as a GeoJSON FeatureCollection (private).

    Each Feature contains the route LineString geometry + sport/distance metadata.
    Only activities with geometry are included.

    **Streamed** (``StreamingResponse``) rather than a single in-memory body:
    ``/map`` requests ``limit=5000`` and a heavy user already has thousands of
    full-resolution LineStrings, so one materialized ``ActivitiesResponse`` JSON
    body approaches/exceeds Cloud Run's 32 MiB non-streaming response cap → a
    silent 500 → the map shows no personal routes. A streamed response is NOT
    subject to that cap. The body is emitted incrementally — the header, then
    the comma-separated features, then the closing ``]}``, buffered into
    ``STREAM_CHUNK_BYTES`` chunks — so the full feature list is never built in
    memory. The JSON shape is the object the frontend parses with
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
    geometry (gap-split → Douglas-Peucker 2 m → 5-decimal coords), the
    precomputed ``display_geojson`` cache spliced verbatim (rows not backfilled
    yet get the pre-cache full-precision gap-split geometry) — ~19× smaller
    gzipped on a dense 1 Hz ride. Full precision stays on
    ``/me/activities/{id}``.

    **HTTP revalidation**: weak ``ETag`` + ``Cache-Control: private,
    no-cache``. The browser keeps the body and revalidates on every /map
    mount; an unchanged corpus answers ``304`` with no body after ONE indexed
    query (``user_map_activities_validator`` — no geometry read), which also
    yields ``total``.
    """
    started = time.perf_counter()
    user_id = current_user.user_id
    # ``is True``: the handler is also called directly (tests), where an omitted
    # ``download`` is the truthy ``Query(...)`` default object, not ``False``.
    download = download is True
    if download:
        # "Télécharger ma heatmap" (Calques): the rider's WHOLE corpus as a
        # file, not the map's "most recent 5000" window. Same streamed body
        # (O(window) memory), served as an attachment and never revalidated.
        limit = EXPORT_MAX_ACTIVITIES
        if_none_match = None

    # ``total`` is emitted at the head of the body, so it must be known before
    # the rows stream: a cheap indexed COUNT (capped at ``limit``), computed in
    # the same query as the validator. Computed BEFORE the row scan: a write
    # landing in between yields a body at least as new as the ETag, so the
    # next revalidation misses and refetches (never a stale 304). It can skew
    # ``total`` by a row — same near-never mismatch class as the defensive
    # per-feature skip below, accepted for O(1) memory.
    count, digest = await run_in_threadpool(user_map_activities_validator, user_id, sport)
    total = min(count, limit)
    etag = _me_activities_etag(user_id, sport, limit, count, digest)
    cache_headers = {"ETag": etag, "Cache-Control": ME_ACTIVITIES_CACHE_CONTROL}
    if download:
        cache_headers = {
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{EXPORT_FILENAME}"',
        }

    if _etag_matches(if_none_match, etag):
        logger.info(
            "me_activities: 304 rows=%d elapsed_ms=%.0f",
            total, (time.perf_counter() - started) * 1000,
        )
        return Response(status_code=304, headers=cache_headers)

    def _iter_body() -> Iterator[bytes]:
        rows = fallbacks = skipped = sent = 0
        complete = False
        buf = [f'{{"type":"FeatureCollection","total":{total},"features":[']
        size = len(buf[0])
        try:
            for act in iter_user_map_activities(user_id, sport=sport, limit=limit):
                try:
                    text, fell_back = _compact_feature_json(act)
                except (json.JSONDecodeError, KeyError, TypeError):
                    skipped += 1
                    continue
                if rows:
                    text = "," + text
                rows += 1
                fallbacks += fell_back
                buf.append(text)
                size += len(text)
                if size >= STREAM_CHUNK_BYTES:
                    chunk = "".join(buf).encode()
                    sent += len(chunk)
                    yield chunk
                    buf, size = [], 0
            buf.append("]}")
            chunk = "".join(buf).encode()
            sent += len(chunk)
            yield chunk
            complete = True
        finally:
            logger.info(
                "me_activities: 200 rows=%d fallbacks=%d skipped=%d bytes=%d "
                "elapsed_ms=%.0f complete=%s",
                rows, fallbacks, skipped, sent,
                (time.perf_counter() - started) * 1000, complete,
            )

    return StreamingResponse(
        _iter_body(),
        media_type="application/geo+json" if download else "application/json",
        headers=cache_headers,
    )


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


class DefaultSportActivity(BaseModel):
    id: str
    name: str
    sport: str
    distance_m: float | None = None
    activity_date: str | None = None


class DefaultSportStatus(BaseModel):
    count: int
    activities: list[DefaultSportActivity]


@router.get("/me/activities/default-sport", response_model=DefaultSportStatus)
def me_activities_default_sport(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(50, ge=0, le=500),
) -> DefaultSportStatus:
    """The user's activities whose sport is only a generic default.

    A Strava export's "Ride" says nothing about road / gravel / MTB: such a
    ride is imported as ``road`` and flagged. ``count`` is the total;
    ``activities`` the ``limit`` most recent (name, date, distance — no
    geometry). Declared BEFORE ``/me/activities/{activity_id}`` so
    ``default-sport`` is never captured as an id.
    """
    count = count_default_sport(db, current_user.user_id)
    items = list_default_sport(db, current_user.user_id, limit) if count and limit else []
    return DefaultSportStatus(count=count, activities=[DefaultSportActivity(**a) for a in items])


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
    """Either an explicit id list (≤500), or ``from_default: true`` = "every
    activity of mine whose sport is still the generic default" (no id list, no
    cap — the server resolves the set). Exactly one of the two."""

    activity_ids: list[str] | None = Field(default=None, min_length=1, max_length=BULK_SPORT_MAX_IDS)
    from_default: bool = False
    sport: EditableSport

    @model_validator(mode="after")
    def _one_target(self) -> "BulkSportUpdate":
        if bool(self.activity_ids) == self.from_default:
            raise ValueError("provide either activity_ids or from_default=true")
        return self


class BulkSportResult(BaseModel):
    sport: str
    matched: int  # owned activities found among the ids (or default-sport ones)
    updated: int  # of which the sport actually changed
    updated_ids: list[str]
    # Default-sport activities left for this user after the call.
    default_sport_count: int = 0


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

    Every activity targeted gets ``sport_is_default = false`` — also when its
    sport is unchanged (the rider CONFIRMED it). ``{"from_default": true,
    "sport": "gravel"}`` re-classifies all the default-sport activities at
    once; ``{"from_default": true, "sport": "road"}`` confirms them as road.
    """
    user_id = current_user.user_id
    if body.from_default:
        try:
            matched, changed = resolve_default_sport(db, user_id, body.sport)
        except TooManyDefaultSportActivities as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{exc.count} activities to re-classify at once is too many here: "
                    f"send them as activity_ids, at most {BULK_SPORT_MAX_IDS} per request"
                ),
            ) from exc
    else:
        ids = _valid_uuids(list(dict.fromkeys(body.activity_ids or [])))
        found, changed = change_activities_sport(db, user_id, ids, body.sport)
        matched = len(found)
    return BulkSportResult(
        sport=body.sport,
        matched=matched,
        updated=len(changed),
        updated_ids=changed,
        default_sport_count=count_default_sport(db, user_id),
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
