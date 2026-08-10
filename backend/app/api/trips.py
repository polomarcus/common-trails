"""Trip Collections API — bikepacking multi-day journeys.

Endpoints:
  POST   /trips                         create trip
  GET    /trips                         list trips (public + own)
  GET    /trips/{id}                    detail with stages + POIs
  PUT    /trips/{id}                    update trip metadata
  DELETE /trips/{id}                    delete trip (cascade)
  POST   /trips/{id}/fork              deep copy trip
  POST   /trips/{id}/stages            add stage
  PUT    /trips/{id}/stages/{sid}       update stage
  DELETE /trips/{id}/stages/{sid}       remove stage
  PUT    /trips/{id}/stages/reorder     reorder stages
  POST   /trips/{id}/pois              add POI
  PUT    /trips/{id}/pois/{pid}         update POI
  DELETE /trips/{id}/pois/{pid}         remove POI
  GET    /trips/{id}/gpx               merged GPX (all stages)
  GET    /trips/{id}/gpx/zip           ZIP with one GPX per stage
"""
import io
import json
import uuid
import zipfile
from typing import Annotated, Literal
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user, get_current_user_optional
from app.db.models import Route, RouteVersion, Trip, TripPOI, TripStage
from app.db.session import get_db
from app.services.gpx import geojson_to_gpx

router = APIRouter(tags=["trips"])

SportType = Literal["road", "gravel", "mtb", "offroad", "running"]
VisibilityType = Literal["public", "unlisted", "private"]
StatusType = Literal["draft", "planned", "completed"]
LodgingType = Literal["camping", "hotel", "refuge", "free", "none"]
POIType = Literal["water", "food", "camp", "shelter", "shop", "train", "viewpoint", "custom"]

VALID_SPORTS: set[str] = {"road", "gravel", "mtb", "offroad", "running"}  # type: ignore[assignment]
VALID_VISIBILITIES: set[str] = {"public", "unlisted", "private"}  # type: ignore[assignment]
VALID_STATUSES: set[str] = {"draft", "planned", "completed"}  # type: ignore[assignment]
VALID_LODGING: set[str] = {"camping", "hotel", "refuge", "free", "none"}  # type: ignore[assignment]
VALID_POI_TYPES: set[str] = {"water", "food", "camp", "shelter", "shop", "train", "viewpoint", "custom"}  # type: ignore[assignment]


# ── Schemas ───────────────────────────────────────────────────────────────────

class TripCreate(BaseModel):
    name: str
    description: str | None = None
    sport: SportType = "road"
    visibility: VisibilityType = "private"
    status: StatusType = "draft"
    region: str | None = None
    tags_json: str | None = None
    cover_image_url: str | None = None


class TripUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    sport: SportType | None = None
    visibility: VisibilityType | None = None
    status: StatusType | None = None
    region: str | None = None
    tags_json: str | None = None
    cover_image_url: str | None = None


class StageCreate(BaseModel):
    route_id: str | None = None
    day_index: int = 0
    title: str | None = None
    description: str | None = None
    estimated_time_min: int | None = None
    lodging_type: LodgingType | None = None
    is_rest_day: bool = False


class StageUpdate(BaseModel):
    route_id: str | None = None
    day_index: int | None = None
    title: str | None = None
    description: str | None = None
    estimated_time_min: int | None = None
    lodging_type: LodgingType | None = None
    is_rest_day: bool | None = None


class StageReorder(BaseModel):
    stage_ids: list[str]


class POICreate(BaseModel):
    type: POIType = "custom"
    lon: float
    lat: float
    name: str | None = None
    notes: str | None = None
    stage_id: str | None = None
    source: str = "user"


class POIUpdate(BaseModel):
    type: POIType | None = None
    lon: float | None = None
    lat: float | None = None
    name: str | None = None
    notes: str | None = None
    stage_id: str | None = None


# ── Output Models ────────────────────────────────────────────────────────────

class POIOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    trip_id: str
    stage_id: str | None
    type: str
    lon: float
    lat: float
    name: str | None
    notes: str | None
    source: str | None
    created_at: str | None

    @classmethod
    def from_orm_instance(cls, p: TripPOI) -> "POIOut":
        return cls(
            id=p.id,
            trip_id=p.trip_id,
            stage_id=p.stage_id,
            type=p.type,
            lon=p.lon,
            lat=p.lat,
            name=p.name,
            notes=p.notes,
            source=p.source,
            created_at=p.created_at.isoformat() if p.created_at else None,
        )


class StageOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    trip_id: str
    route_id: str | None
    day_index: int | None
    title: str | None
    description: str | None
    estimated_time_min: int | None
    lodging_type: str | None
    is_rest_day: bool | None
    created_at: str | None
    updated_at: str | None
    # Enriched route fields
    route_name: str | None = None
    route_sport: str | None = None
    route_distance_m: float | None = None
    route_elevation_gain_m: float | None = None
    route_geometry_geojson: str | None = None

    @classmethod
    def from_orm_instance(cls, s: TripStage, route_data: dict | None = None) -> "StageOut":
        rd = route_data or {}
        return cls(
            id=s.id,
            trip_id=s.trip_id,
            route_id=s.route_id,
            day_index=s.day_index,
            title=s.title,
            description=s.description,
            estimated_time_min=s.estimated_time_min,
            lodging_type=s.lodging_type,
            is_rest_day=s.is_rest_day,
            created_at=s.created_at.isoformat() if s.created_at else None,
            updated_at=s.updated_at.isoformat() if s.updated_at else None,
            route_name=rd.get("route_name"),
            route_sport=rd.get("route_sport"),
            route_distance_m=rd.get("route_distance_m"),
            route_elevation_gain_m=rd.get("route_elevation_gain_m"),
            route_geometry_geojson=rd.get("route_geometry_geojson"),
        )


class TripOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    owner_id: str
    name: str
    slug: str | None
    description: str | None
    cover_image_url: str | None
    sport: str
    visibility: str
    status: str
    region: str | None
    tags_json: str | None
    total_distance_m: float | None
    total_dplus_m: float | None
    forked_from_id: str | None
    created_at: str | None
    updated_at: str | None
    stages_count: int = 0
    days_count: int = 0
    stages: list[StageOut] | None = None
    pois: list[POIOut] | None = None

    @classmethod
    def from_orm_instance(
        cls,
        t: Trip,
        stages_count: int = 0,
        days_count: int = 0,
        stages: list[StageOut] | None = None,
        pois: list[POIOut] | None = None,
    ) -> "TripOut":
        return cls(
            id=t.id,
            owner_id=t.owner_id,
            name=t.name,
            slug=t.slug,
            description=t.description,
            cover_image_url=t.cover_image_url,
            sport=t.sport,
            visibility=t.visibility,
            status=t.status,
            region=t.region,
            tags_json=t.tags_json,
            total_distance_m=t.total_distance_m,
            total_dplus_m=t.total_dplus_m,
            forked_from_id=t.forked_from_id,
            created_at=t.created_at.isoformat() if t.created_at else None,
            updated_at=t.updated_at.isoformat() if t.updated_at else None,
            stages_count=stages_count,
            days_count=days_count,
            stages=stages,
            pois=pois,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_trip_or_404(db: Session, trip_id: str) -> Trip:
    t = db.query(Trip).filter(Trip.id == trip_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Trip not found")
    return t


def _require_trip_owner(trip: Trip, user_id: str) -> None:
    if trip.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Not the trip owner")


def _get_route_data(db: Session, route_id: str | None) -> dict | None:
    """Get route data for stage enrichment."""
    if not route_id:
        return None
    route = db.query(Route).filter(Route.id == route_id).first()
    if not route:
        return None
    geojson = route.geometry_geojson
    if not geojson and route.current_version_id:
        ver = db.query(RouteVersion).filter(
            RouteVersion.id == route.current_version_id
        ).first()
        if ver:
            geojson = ver.geometry_geojson
    return {
        "route_name": route.name,
        "route_sport": route.sport,
        "route_distance_m": route.distance_m,
        "route_elevation_gain_m": route.elevation_gain_m,
        "route_geometry_geojson": geojson,
    }


def _recompute_trip_stats(db: Session, trip: Trip) -> None:
    """Recompute total_distance_m and total_dplus_m from linked routes (single JOIN query)."""
    db.flush()  # ensure pending adds/deletes are visible to the query
    rows = (
        db.query(Route.distance_m, Route.elevation_gain_m)
        .join(TripStage, TripStage.route_id == Route.id)
        .filter(TripStage.trip_id == trip.id, TripStage.is_rest_day == False)  # noqa: E712
        .all()
    )
    trip.total_distance_m = sum(r.distance_m or 0 for r in rows)
    trip.total_dplus_m = sum(r.elevation_gain_m or 0 for r in rows)


def _build_stage_out(db: Session, stage: TripStage) -> StageOut:
    """Build a StageOut with enriched route data."""
    rd = _get_route_data(db, stage.route_id)
    return StageOut.from_orm_instance(stage, route_data=rd)


def _trip_out(db: Session, trip: Trip, include_children: bool = False) -> TripOut:
    """Build trip output, optionally with stages and POIs (batch route loading)."""
    stages = db.query(TripStage).filter(
        TripStage.trip_id == trip.id
    ).order_by(TripStage.day_index).all()
    stages_count = len(stages)
    days_count = (
        max((s.day_index or 0 for s in stages), default=-1) + 1
        if stages else 0
    )
    stage_outs: list[StageOut] | None = None
    poi_outs: list[POIOut] | None = None
    if include_children:
        # Batch-load all routes referenced by stages in one query (avoids N+1)
        route_ids = [s.route_id for s in stages if s.route_id]
        route_map: dict[str, dict] = {}
        if route_ids:
            routes = db.query(Route).filter(Route.id.in_(route_ids)).all()
            for r in routes:
                geojson = r.geometry_geojson
                if not geojson and r.current_version_id:
                    ver = db.query(RouteVersion).filter(
                        RouteVersion.id == r.current_version_id
                    ).first()
                    if ver:
                        geojson = ver.geometry_geojson
                route_map[r.id] = {
                    "route_name": r.name,
                    "route_sport": r.sport,
                    "route_distance_m": r.distance_m,
                    "route_elevation_gain_m": r.elevation_gain_m,
                    "route_geometry_geojson": geojson,
                }
        stage_outs = [
            StageOut.from_orm_instance(s, route_data=route_map.get(s.route_id) if s.route_id else None)
            for s in stages
        ]
        pois = db.query(TripPOI).filter(TripPOI.trip_id == trip.id).all()
        poi_outs = [POIOut.from_orm_instance(p) for p in pois]
    return TripOut.from_orm_instance(
        trip,
        stages_count=stages_count,
        days_count=days_count,
        stages=stage_outs,
        pois=poi_outs,
    )


def _stage_geojson(db: Session, stage: TripStage) -> str | None:
    """Get the GeoJSON geometry string for a stage's linked route."""
    if not stage.route_id:
        return None
    route = db.query(Route).filter(Route.id == stage.route_id).first()
    if not route:
        return None
    geojson = route.geometry_geojson
    if not geojson and route.current_version_id:
        ver = db.query(RouteVersion).filter(
            RouteVersion.id == route.current_version_id
        ).first()
        if ver:
            geojson = ver.geometry_geojson
    return geojson


# ── Trip CRUD ─────────────────────────────────────────────────────────────────

@router.post("/trips", status_code=201)
async def create_trip(
    body: TripCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TripOut:
    if body.sport not in VALID_SPORTS:
        raise HTTPException(status_code=422, detail=f"Invalid sport: {body.sport}")
    if body.visibility not in VALID_VISIBILITIES:
        raise HTTPException(status_code=422, detail=f"Invalid visibility: {body.visibility}")
    if body.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status: {body.status}")

    trip = Trip(
        id=str(uuid.uuid4()),
        owner_id=current_user.user_id,
        name=body.name,
        slug=None,
        description=body.description,
        cover_image_url=body.cover_image_url,
        sport=body.sport,
        visibility=body.visibility,
        status=body.status,
        region=body.region,
        tags_json=body.tags_json,
        total_distance_m=0,
        total_dplus_m=0,
        forked_from_id=None,
    )
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return _trip_out(db, trip)


@router.get("/trips")
async def list_trips(
    current_user: Annotated[AuthenticatedUser | None, Depends(get_current_user_optional)],
    db: Annotated[Session, Depends(get_db)],
    visibility: str | None = None,
    sport: str | None = None,
    status: str | None = None,
) -> list[TripOut]:
    from sqlalchemy import or_

    user_id = current_user.user_id if current_user else None
    q = db.query(Trip)

    # Visibility: non-owners can only see public/unlisted non-draft trips
    if user_id:
        q = q.filter(
            or_(
                Trip.owner_id == user_id,
                (Trip.visibility != "private") & (Trip.status != "draft"),
            )
        )
    else:
        q = q.filter(Trip.visibility != "private", Trip.status != "draft")

    # Optional filters
    if visibility:
        if user_id:
            # Owner can see their own regardless of visibility filter
            q = q.filter(or_(Trip.visibility == visibility, Trip.owner_id == user_id))
        else:
            q = q.filter(Trip.visibility == visibility)
    if sport:
        q = q.filter(Trip.sport == sport)
    if status:
        q = q.filter(Trip.status == status)

    return [_trip_out(db, t) for t in q.all()]


@router.get("/trips/{trip_id}")
async def get_trip(
    trip_id: str,
    current_user: Annotated[AuthenticatedUser | None, Depends(get_current_user_optional)],
    db: Annotated[Session, Depends(get_db)],
) -> TripOut:
    trip = _get_trip_or_404(db, trip_id)
    user_id = current_user.user_id if current_user else None
    is_owner = user_id and trip.owner_id == user_id
    if trip.visibility == "private" and not is_owner:
        raise HTTPException(status_code=404, detail="Trip not found")
    return _trip_out(db, trip, include_children=True)


@router.put("/trips/{trip_id}")
async def update_trip(
    trip_id: str,
    body: TripUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TripOut:
    trip = _get_trip_or_404(db, trip_id)
    _require_trip_owner(trip, current_user.user_id)

    if body.name is not None:
        trip.name = body.name
    if body.description is not None:
        trip.description = body.description
    if body.sport is not None:
        if body.sport not in VALID_SPORTS:
            raise HTTPException(status_code=422, detail=f"Invalid sport: {body.sport}")
        trip.sport = body.sport
    if body.visibility is not None:
        if body.visibility not in VALID_VISIBILITIES:
            raise HTTPException(status_code=422, detail=f"Invalid visibility: {body.visibility}")
        trip.visibility = body.visibility
    if body.status is not None:
        if body.status not in VALID_STATUSES:
            raise HTTPException(status_code=422, detail=f"Invalid status: {body.status}")
        trip.status = body.status
    if body.region is not None:
        trip.region = body.region
    if body.tags_json is not None:
        trip.tags_json = body.tags_json
    if body.cover_image_url is not None:
        trip.cover_image_url = body.cover_image_url
    db.commit()
    db.refresh(trip)
    return _trip_out(db, trip, include_children=True)


@router.delete("/trips/{trip_id}", status_code=204)
async def delete_trip(
    trip_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    trip = _get_trip_or_404(db, trip_id)
    _require_trip_owner(trip, current_user.user_id)
    # Cascade delete stages and POIs
    db.query(TripPOI).filter(TripPOI.trip_id == trip_id).delete()
    db.query(TripStage).filter(TripStage.trip_id == trip_id).delete()
    db.delete(trip)
    db.commit()


# ── Fork ──────────────────────────────────────────────────────────────────────

@router.post("/trips/{trip_id}/fork", status_code=201)
async def fork_trip(
    trip_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TripOut:
    trip = _get_trip_or_404(db, trip_id)
    user_id = current_user.user_id
    if trip.visibility == "private" and trip.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Impossible de créer une variante d'un voyage privé")

    new_trip = Trip(
        id=str(uuid.uuid4()),
        owner_id=user_id,
        name=trip.name,
        slug=None,
        description=trip.description,
        cover_image_url=trip.cover_image_url,
        sport=trip.sport,
        visibility="private",
        status="draft",
        region=trip.region,
        tags_json=trip.tags_json,
        total_distance_m=trip.total_distance_m,
        total_dplus_m=trip.total_dplus_m,
        forked_from_id=trip_id,
    )
    db.add(new_trip)

    # Deep copy stages
    stages = db.query(TripStage).filter(
        TripStage.trip_id == trip_id
    ).order_by(TripStage.day_index).all()
    stage_id_map: dict[str, str] = {}
    for s in stages:
        new_sid = str(uuid.uuid4())
        stage_id_map[s.id] = new_sid
        db.add(TripStage(
            id=new_sid,
            trip_id=new_trip.id,
            route_id=s.route_id,
            day_index=s.day_index,
            title=s.title,
            description=s.description,
            estimated_time_min=s.estimated_time_min,
            lodging_type=s.lodging_type,
            is_rest_day=s.is_rest_day,
        ))

    # Deep copy POIs
    pois = db.query(TripPOI).filter(TripPOI.trip_id == trip_id).all()
    for p in pois:
        db.add(TripPOI(
            id=str(uuid.uuid4()),
            trip_id=new_trip.id,
            stage_id=stage_id_map.get(p.stage_id) if p.stage_id else None,
            type=p.type,
            lon=p.lon,
            lat=p.lat,
            name=p.name,
            notes=p.notes,
            source=p.source,
        ))

    _recompute_trip_stats(db, new_trip)
    db.commit()
    db.refresh(new_trip)
    return _trip_out(db, new_trip, include_children=True)


# ── Stages ────────────────────────────────────────────────────────────────────

@router.post("/trips/{trip_id}/stages", status_code=201)
async def add_stage(
    trip_id: str,
    body: StageCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StageOut:
    trip = _get_trip_or_404(db, trip_id)
    _require_trip_owner(trip, current_user.user_id)

    if body.lodging_type and body.lodging_type not in VALID_LODGING:
        raise HTTPException(status_code=422, detail=f"Invalid lodging_type: {body.lodging_type}")
    if body.route_id:
        route = db.query(Route).filter(Route.id == body.route_id).first()
        if not route:
            raise HTTPException(status_code=404, detail="Route not found")

    stage = TripStage(
        id=str(uuid.uuid4()),
        trip_id=trip_id,
        route_id=body.route_id,
        day_index=body.day_index,
        title=body.title,
        description=body.description,
        estimated_time_min=body.estimated_time_min,
        lodging_type=body.lodging_type,
        is_rest_day=body.is_rest_day,
    )
    db.add(stage)
    _recompute_trip_stats(db, trip)
    db.commit()
    db.refresh(stage)
    return _build_stage_out(db, stage)


@router.put("/trips/{trip_id}/stages/reorder")
async def reorder_stages(
    trip_id: str,
    body: StageReorder,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[StageOut]:
    trip = _get_trip_or_404(db, trip_id)
    _require_trip_owner(trip, current_user.user_id)

    for i, sid in enumerate(body.stage_ids):
        stage = db.query(TripStage).filter(
            TripStage.id == sid, TripStage.trip_id == trip_id
        ).first()
        if not stage:
            raise HTTPException(status_code=404, detail=f"Stage {sid} not found in this trip")
        stage.day_index = i
    db.commit()

    stages = db.query(TripStage).filter(
        TripStage.trip_id == trip_id
    ).order_by(TripStage.day_index).all()
    return [_build_stage_out(db, s) for s in stages]


@router.put("/trips/{trip_id}/stages/{stage_id}")
async def update_stage(
    trip_id: str,
    stage_id: str,
    body: StageUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StageOut:
    trip = _get_trip_or_404(db, trip_id)
    _require_trip_owner(trip, current_user.user_id)
    stage = db.query(TripStage).filter(
        TripStage.id == stage_id, TripStage.trip_id == trip_id
    ).first()
    if not stage:
        raise HTTPException(status_code=404, detail="Stage not found")

    if body.lodging_type is not None and body.lodging_type not in VALID_LODGING:
        raise HTTPException(status_code=422, detail=f"Invalid lodging_type: {body.lodging_type}")

    for field in ("route_id", "day_index", "title", "description", "estimated_time_min", "lodging_type", "is_rest_day"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(stage, field, val)
    _recompute_trip_stats(db, trip)
    db.commit()
    db.refresh(stage)
    return _build_stage_out(db, stage)


@router.delete("/trips/{trip_id}/stages/{stage_id}", status_code=204)
async def remove_stage(
    trip_id: str,
    stage_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    trip = _get_trip_or_404(db, trip_id)
    _require_trip_owner(trip, current_user.user_id)
    stage = db.query(TripStage).filter(
        TripStage.id == stage_id, TripStage.trip_id == trip_id
    ).first()
    if not stage:
        raise HTTPException(status_code=404, detail="Stage not found")
    db.delete(stage)
    _recompute_trip_stats(db, trip)
    db.commit()


# ── POIs ──────────────────────────────────────────────────────────────────────

@router.post("/trips/{trip_id}/pois", status_code=201)
async def add_poi(
    trip_id: str,
    body: POICreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> POIOut:
    trip = _get_trip_or_404(db, trip_id)
    _require_trip_owner(trip, current_user.user_id)

    if body.type not in VALID_POI_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid POI type: {body.type}")

    poi = TripPOI(
        id=str(uuid.uuid4()),
        trip_id=trip_id,
        stage_id=body.stage_id,
        type=body.type,
        lon=body.lon,
        lat=body.lat,
        name=body.name,
        notes=body.notes,
        source=body.source,
    )
    db.add(poi)
    db.commit()
    db.refresh(poi)
    return POIOut.from_orm_instance(poi)


@router.put("/trips/{trip_id}/pois/{poi_id}")
async def update_poi(
    trip_id: str,
    poi_id: str,
    body: POIUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> POIOut:
    trip = _get_trip_or_404(db, trip_id)
    _require_trip_owner(trip, current_user.user_id)
    poi = db.query(TripPOI).filter(
        TripPOI.id == poi_id, TripPOI.trip_id == trip_id
    ).first()
    if not poi:
        raise HTTPException(status_code=404, detail="POI not found")

    if body.type is not None and body.type not in VALID_POI_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid POI type: {body.type}")

    for field in ("type", "lon", "lat", "name", "notes", "stage_id"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(poi, field, val)
    db.commit()
    db.refresh(poi)
    return POIOut.from_orm_instance(poi)


@router.delete("/trips/{trip_id}/pois/{poi_id}", status_code=204)
async def remove_poi(
    trip_id: str,
    poi_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    trip = _get_trip_or_404(db, trip_id)
    _require_trip_owner(trip, current_user.user_id)
    poi = db.query(TripPOI).filter(
        TripPOI.id == poi_id, TripPOI.trip_id == trip_id
    ).first()
    if not poi:
        raise HTTPException(status_code=404, detail="POI not found")
    db.delete(poi)
    db.commit()


# ── GPX Export ────────────────────────────────────────────────────────────────

@router.get("/trips/{trip_id}/stages/{stage_id}/gpx")
async def stage_gpx(
    trip_id: str,
    stage_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AuthenticatedUser | None, Depends(get_current_user_optional)] = None,
) -> Response:
    """Download a single stage as GPX."""
    trip = _get_trip_or_404(db, trip_id)
    user_id = current_user.user_id if current_user else None
    if trip.visibility == "private" and (not user_id or trip.owner_id != user_id):
        raise HTTPException(status_code=404, detail="Trip not found")
    stage = db.query(TripStage).filter(
        TripStage.id == stage_id, TripStage.trip_id == trip_id
    ).first()
    if not stage:
        raise HTTPException(status_code=404, detail="Stage not found")

    geojson_str = _stage_geojson(db, stage)
    if not geojson_str:
        raise HTTPException(status_code=404, detail="Stage has no geometry")

    stage_title = stage.title or f"etape_{(stage.day_index or 0) + 1}"
    gpx_xml = geojson_to_gpx(geojson_str, name=stage_title)
    safe_title = stage_title.replace(" ", "_").lower()[:40]
    return Response(
        content=gpx_xml,
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.gpx"'},
    )


@router.get("/trips/{trip_id}/gpx")
async def trip_gpx(
    trip_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AuthenticatedUser | None, Depends(get_current_user_optional)] = None,
) -> Response:
    """Merged GPX — one <trk> per stage."""
    trip = _get_trip_or_404(db, trip_id)
    user_id = current_user.user_id if current_user else None
    if trip.visibility == "private" and (not user_id or trip.owner_id != user_id):
        raise HTTPException(status_code=404, detail="Trip not found")
    stages = db.query(TripStage).filter(
        TripStage.trip_id == trip_id
    ).order_by(TripStage.day_index).all()

    # Build multi-track GPX
    tracks = []
    for s in stages:
        if s.is_rest_day:
            continue
        geojson_str = _stage_geojson(db, s)
        if not geojson_str:
            continue
        try:
            geojson = json.loads(geojson_str)
            coords = geojson.get("coordinates", [])
        except Exception:
            continue
        if not coords:
            continue
        track_name = xml_escape(s.title or f"Jour {(s.day_index or 0) + 1}")
        trkpts = "\n".join(
            f'      <trkpt lat="{pt[1]}" lon="{pt[0]}">'
            + (f"<ele>{pt[2]}</ele>" if len(pt) > 2 else "")
            + "</trkpt>"
            for pt in coords
        )
        tracks.append(f"""  <trk>
    <name>{track_name}</name>
    <trkseg>
{trkpts}
    </trkseg>
  </trk>""")

    gpx_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="CHEMINS COMMUNS">
{chr(10).join(tracks)}
</gpx>"""

    safe_name = trip.name.replace(" ", "_").lower()[:40]
    return Response(
        content=gpx_content,
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.gpx"'},
    )


@router.get("/trips/{trip_id}/gpx/zip")
async def trip_gpx_zip(
    trip_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AuthenticatedUser | None, Depends(get_current_user_optional)] = None,
) -> Response:
    """ZIP archive with one GPX per stage."""
    trip = _get_trip_or_404(db, trip_id)
    user_id = current_user.user_id if current_user else None
    if trip.visibility == "private" and (not user_id or trip.owner_id != user_id):
        raise HTTPException(status_code=404, detail="Trip not found")
    stages = db.query(TripStage).filter(
        TripStage.trip_id == trip_id
    ).order_by(TripStage.day_index).all()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in stages:
            if s.is_rest_day:
                continue
            geojson_str = _stage_geojson(db, s)
            if not geojson_str:
                continue
            stage_title = s.title or "etape"
            safe_title = stage_title.replace(" ", "_").lower()[:30]
            filename = f"jour_{(s.day_index or 0) + 1}_{safe_title}.gpx"
            gpx_xml = geojson_to_gpx(geojson_str, name=stage_title)
            zf.writestr(filename, gpx_xml)

    buf.seek(0)
    safe_name = trip.name.replace(" ", "_").lower()[:40]
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_stages.zip"'},
    )
