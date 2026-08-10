"""GitHub-like routes API.

Endpoints:
  POST   /routes                      create route
  GET    /routes                      list routes
  GET    /routes/{id}                 get route
  GET    /routes/batch?ids=a,b,c      batch fetch (max 4)
  PUT    /routes/{id}                 update route
  PATCH  /routes/{id}                 partial update route
  DELETE /routes/{id}                 soft delete route
  POST   /routes/{id}/restore         restore soft-deleted route
  POST   /routes/{id}/fork            fork route
  GET    /routes/{id}/versions        list versions
  GET    /routes/{id}/forks           list forks
  GET    /me/routes                   list user's routes
  GET    /me/drafts                   list user's drafts
  POST   /routes/{id}/suggestions     create suggestion (local PR)
  GET    /routes/{id}/suggestions     list suggestions
  POST   /routes/{id}/tags            create tag (DEPRECATED)
  GET    /routes/{id}/tags            list tags
  GET    /routes/{id}/gpx             export GPX
"""
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.auth import AuthenticatedUser, get_current_user, get_current_user_optional
from app.db.models import Route, RouteAnnotation, RouteFork, RouteTag, RouteVersion, Suggestion
from app.db.session import get_db
from app.rate_limit import limiter

router = APIRouter(tags=["routes"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class RouteCreate(BaseModel):
    name: str
    description: str | None = None
    sport: str = "road"
    visibility: str = "public"
    status: str = "published"  # "draft" | "published"
    geometry_geojson: str | None = None
    waypoints_json: str | None = None  # JSON array [[lon,lat],…] — editor waypoints
    distance_m: float | None = None
    elevation_gain_m: float | None = None
    forked_from_id: str | None = None
    surface_pct: str | None = None  # JSON {"asphalt": 72.5, …}


class RouteOut(BaseModel):
    id: str
    owner_id: str
    name: str
    description: str | None
    sport: str
    visibility: str
    status: str = "published"
    forked_from_id: str | None
    current_version_id: str | None
    distance_m: float | None = None
    elevation_gain_m: float | None = None
    geometry_geojson: str | None = None
    waypoints_json: str | None = None  # JSON array [[lon,lat],…] — editor waypoints
    surface_pct: str | None = None  # JSON {"asphalt": 72.5, …}
    fork_count: int = 0
    center: list[float] | None = None
    created_at: str | None = None


class MyRouteOut(RouteOut):
    last_accessed_at: str | None = None
    deleted_at: str | None = None


class RouteUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    visibility: str | None = None
    sport: str | None = None


class VersionCreate(BaseModel):
    message: str | None = None
    geometry_geojson: str | None = None
    distance_m: float | None = None
    elevation_gain_m: float | None = None


class SuggestionCreate(BaseModel):
    title: str
    description: str | None = None
    geometry_geojson: str | None = None


class ForkCreate(BaseModel):
    name: str | None = None
    description: str | None = None


class TagCreate(BaseModel):
    tag: str
    version_id: str
    message: str | None = None


class AnnotationCreate(BaseModel):
    lon: float
    lat: float
    dist_m: float | None = None
    icon: str = "info"
    text: str | None = None


class AnnotationOut(BaseModel):
    id: str
    route_id: str
    author_id: str
    lon: float
    lat: float
    dist_m: float | None
    icon: str
    text: str | None
    created_at: str | None = None


class VersionOut(BaseModel):
    id: str
    route_id: str
    author_id: str
    message: str | None
    geometry_geojson: str | None
    distance_m: float | None
    elevation_gain_m: float | None


class SuggestionOut(BaseModel):
    id: str
    route_id: str
    author_id: str
    title: str
    description: str | None
    geometry_geojson: str | None
    status: str


class TagOut(BaseModel):
    id: str
    route_id: str
    version_id: str
    tag: str
    message: str | None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_center(geometry_geojson: str | None) -> list[float] | None:
    """Compute midpoint coordinate from a GeoJSON geometry."""
    if not geometry_geojson:
        return None
    try:
        geom = json.loads(geometry_geojson)
        coords = geom.get("coordinates", [])
        if not coords:
            return None
        mid = coords[len(coords) // 2]
        return [mid[0], mid[1]]
    except Exception:
        return None


def _route_to_out(r: Route, db: Session | None = None) -> RouteOut:
    fork_count = 0
    if db:
        fork_count = db.query(func.count(Route.id)).filter(
            Route.forked_from_id == r.id,
            Route.deleted_at.is_(None),
        ).scalar() or 0
    return RouteOut(
        id=r.id,
        owner_id=r.owner_id,
        name=r.name,
        description=r.description,
        sport=r.sport,
        visibility=r.visibility,
        status=r.status or "published",
        forked_from_id=r.forked_from_id,
        current_version_id=r.current_version_id,
        distance_m=r.distance_m,
        elevation_gain_m=r.elevation_gain_m,
        geometry_geojson=r.geometry_geojson,
        waypoints_json=r.waypoints_json,
        surface_pct=r.surface_pct,
        fork_count=fork_count,
        center=_compute_center(r.geometry_geojson),
        created_at=r.created_at.isoformat() if r.created_at else None,
    )


def _route_to_my_out(r: Route, db: Session) -> MyRouteOut:
    base = _route_to_out(r, db)
    return MyRouteOut(
        **base.model_dump(),
        last_accessed_at=r.last_accessed_at.isoformat() if r.last_accessed_at else None,
        deleted_at=r.deleted_at.isoformat() if r.deleted_at else None,
    )


def _version_to_out(v: RouteVersion) -> VersionOut:
    return VersionOut(
        id=v.id,
        route_id=v.route_id,
        author_id=v.author_id,
        message=v.message,
        geometry_geojson=v.geometry_geojson,
        distance_m=v.distance_m,
        elevation_gain_m=v.elevation_gain_m,
    )


def _suggestion_to_out(s: Suggestion) -> SuggestionOut:
    return SuggestionOut(
        id=s.id,
        route_id=s.route_id,
        author_id=s.author_id,
        title=s.title,
        description=s.description,
        geometry_geojson=s.geometry_geojson,
        status=s.status,
    )


def _tag_to_out(t: RouteTag) -> TagOut:
    return TagOut(
        id=t.id,
        route_id=t.route_id,
        version_id=t.version_id,
        tag=t.tag,
        message=t.message,
    )


def _validate_coordinates(coords: list, geom_type: str) -> None:
    """Validate coordinate bounds and size to prevent abuse."""
    MAX_COORDS = 50_000

    def _check_point(pt: list) -> None:
        if len(pt) < 2:
            raise HTTPException(400, "Coordinate must have at least [lon, lat]")
        lon, lat = pt[0], pt[1]
        if not (-180 <= lon <= 180) or not (-90 <= lat <= 90):
            raise HTTPException(400, f"Coordinate out of bounds: [{lon}, {lat}]")

    if geom_type == "Point":
        _check_point(coords)
    elif geom_type == "LineString":
        if len(coords) > MAX_COORDS:
            raise HTTPException(400, f"Too many coordinates ({len(coords)} > {MAX_COORDS})")
        for pt in coords:
            _check_point(pt)
    elif geom_type == "MultiLineString":
        total = sum(len(line) for line in coords)
        if total > MAX_COORDS:
            raise HTTPException(400, f"Too many coordinates ({total} > {MAX_COORDS})")
        for line in coords:
            for pt in line:
                _check_point(pt)


def _get_route_or_404(db: Session, route_id: str, include_deleted: bool = False) -> Route:
    q = db.query(Route).filter(Route.id == route_id)
    if not include_deleted:
        q = q.filter(Route.deleted_at.is_(None))
    r = q.first()
    if not r:
        raise HTTPException(status_code=404, detail="Route not found")
    return r


def _require_owner(route: Route, user_id: str) -> None:
    if route.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Not the owner")


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("/routes", response_model=RouteOut, status_code=201)
async def create_route(
    body: RouteCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RouteOut:
    route_id = str(uuid.uuid4())
    version_id: str | None = None

    if body.geometry_geojson:
        try:
            geom = json.loads(body.geometry_geojson)
        except json.JSONDecodeError:
            raise HTTPException(400, "Invalid GeoJSON string") from None
        if geom.get("type") not in ("LineString", "MultiLineString", "Point"):
            raise HTTPException(400, f"Unsupported geometry type: {geom.get('type')}")
        # M12: coordinate bounds + size validation
        coords = geom.get("coordinates", [])
        _validate_coordinates(coords, geom.get("type"))
        version_id = str(uuid.uuid4())
        db.add(RouteVersion(
            id=version_id,
            route_id=route_id,
            author_id=current_user.user_id,
            message="Initial version",
            geometry_geojson=body.geometry_geojson,
            distance_m=body.distance_m,
            elevation_gain_m=body.elevation_gain_m,
        ))

    route_status = body.status if body.status in ("draft", "published") else "published"
    route = Route(
        id=route_id,
        owner_id=current_user.user_id,
        name=body.name,
        description=body.description,
        sport=body.sport,
        visibility=body.visibility,
        status=route_status,
        forked_from_id=body.forked_from_id,
        current_version_id=version_id,
        distance_m=body.distance_m,
        elevation_gain_m=body.elevation_gain_m,
        geometry_geojson=body.geometry_geojson,
        waypoints_json=body.waypoints_json,
        surface_pct=body.surface_pct,
    )
    db.add(route)
    db.commit()
    db.refresh(route)
    return _route_to_out(route, db)


@router.get("/routes", response_model=list[RouteOut])
@limiter.limit("60/minute")
async def list_routes(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AuthenticatedUser | None, Depends(get_current_user_optional)] = None,
    sport: str | None = None,
    visibility: str = "public",
    search: str | None = None,
) -> list[RouteOut]:
    # H6: private visibility requires auth and filters to own routes only
    if visibility == "private":
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required to list private routes")
        q = db.query(Route).filter(
            Route.owner_id == current_user.user_id,
            Route.visibility == "private",
            Route.status != "draft",
            Route.deleted_at.is_(None),
        )
    else:
        q = db.query(Route).filter(
            Route.visibility == visibility,
            Route.status != "draft",
            Route.deleted_at.is_(None),
        )
    if sport:
        q = q.filter(Route.sport == sport)
    if search:
        q = q.filter(Route.name.ilike(f"%{search}%"))
    results: list[RouteOut] = []
    for r in q.all():
        out = _route_to_out(r, db)
        if not out.geometry_geojson and r.current_version_id:
            version = db.query(RouteVersion).filter(
                RouteVersion.id == r.current_version_id
            ).first()
            if version:
                out.geometry_geojson = version.geometry_geojson
                if not out.center and version.geometry_geojson:
                    try:
                        import json as _json
                        coords = _json.loads(version.geometry_geojson).get("coordinates", [])
                        if coords:
                            mid = coords[len(coords) // 2]
                            out.center = [mid[0], mid[1]]
                    except Exception:
                        pass
        results.append(out)
    return results


@router.get("/routes/batch", response_model=list[RouteOut])
async def batch_routes(
    ids: str,
    db: Annotated[Session, Depends(get_db)],
) -> list[RouteOut]:
    """Fetch multiple routes in one call. ids = comma-separated UUIDs, max 4."""
    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if len(id_list) > 4:
        raise HTTPException(400, "Maximum 4 routes per batch request")
    # Validate UUIDs
    valid_ids: list[str] = []
    for rid in id_list:
        try:
            uuid.UUID(rid)
            valid_ids.append(rid)
        except ValueError:
            continue
    if not valid_ids:
        return []
    routes = db.query(Route).filter(
        Route.id.in_(valid_ids),
        or_(Route.visibility == "public", Route.visibility == "unlisted"),
        Route.deleted_at.is_(None),
    ).all()
    results = []
    for r in routes:
        out = _route_to_out(r, db)
        if not out.geometry_geojson and r.current_version_id:
            version = db.query(RouteVersion).filter(
                RouteVersion.id == r.current_version_id
            ).first()
            if version:
                out.geometry_geojson = version.geometry_geojson
        results.append(out)
    return results


@router.get("/routes/{route_id}", response_model=RouteOut)
async def get_route(
    route_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AuthenticatedUser | None, Depends(get_current_user_optional)] = None,
) -> RouteOut:
    route = _get_route_or_404(db, route_id)
    # C1: visibility check — private routes only visible to owner
    if route.visibility == "private" and (not current_user or route.owner_id != current_user.user_id):
        raise HTTPException(status_code=404, detail="Route not found")
    # Track last_accessed_at for owner
    if current_user and current_user.user_id == route.owner_id:
        route.last_accessed_at = datetime.now(UTC)
        db.commit()
    out = _route_to_out(route, db)
    # Attach geometry from the current version if the route has none
    if not out.geometry_geojson and route.current_version_id:
        version = db.query(RouteVersion).filter(
            RouteVersion.id == route.current_version_id
        ).first()
        if version:
            out.geometry_geojson = version.geometry_geojson
    return out


@router.put("/routes/{route_id}", response_model=RouteOut)
async def update_route(
    route_id: str,
    body: RouteCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RouteOut:
    route = _get_route_or_404(db, route_id)
    _require_owner(route, current_user.user_id)
    route.name = body.name
    route.description = body.description
    route.sport = body.sport
    route.visibility = body.visibility
    if body.status in ("draft", "published"):
        route.status = body.status
    if body.geometry_geojson is not None:
        route.geometry_geojson = body.geometry_geojson
    if body.waypoints_json is not None:
        route.waypoints_json = body.waypoints_json
    if body.distance_m is not None:
        route.distance_m = body.distance_m
    if body.elevation_gain_m is not None:
        route.elevation_gain_m = body.elevation_gain_m
    if body.surface_pct is not None:
        route.surface_pct = body.surface_pct
    db.commit()
    db.refresh(route)
    return _route_to_out(route, db)


@router.patch("/routes/{route_id}", response_model=RouteOut)
async def patch_route(
    route_id: str,
    body: RouteUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RouteOut:
    """Partial update of a route (name, description, visibility, sport)."""
    route = _get_route_or_404(db, route_id)
    _require_owner(route, current_user.user_id)
    if body.name is not None:
        route.name = body.name
    if body.description is not None:
        route.description = body.description
    if body.visibility is not None:
        route.visibility = body.visibility
    if body.sport is not None:
        route.sport = body.sport
    db.commit()
    db.refresh(route)
    return _route_to_out(route, db)


@router.delete("/routes/{route_id}", status_code=204)
async def delete_route(
    route_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    route = _get_route_or_404(db, route_id)
    _require_owner(route, current_user.user_id)
    route.deleted_at = datetime.now(UTC)
    db.commit()


@router.post("/routes/{route_id}/restore", status_code=200)
async def restore_route(
    route_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RouteOut:
    """Restore a soft-deleted route (within 30 days)."""
    route = _get_route_or_404(db, route_id, include_deleted=True)
    _require_owner(route, current_user.user_id)
    if not route.deleted_at:
        raise HTTPException(400, "Route is not deleted")
    if datetime.now(UTC) - route.deleted_at > timedelta(days=30):
        raise HTTPException(410, "Route was deleted more than 30 days ago")
    route.deleted_at = None
    db.commit()
    db.refresh(route)
    return _route_to_out(route, db)


# ── Fork ──────────────────────────────────────────────────────────────────────

@router.post("/routes/{route_id}/fork", response_model=RouteOut, status_code=201)
async def fork_route(
    route_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    body: ForkCreate | None = None,
) -> RouteOut:
    parent = _get_route_or_404(db, route_id)
    if parent.visibility == "private" and parent.owner_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Impossible de créer une variante d'un itinéraire privé")

    fork_id = str(uuid.uuid4())
    fork_ver_id: str | None = None

    # Copy parent geometry into a new version owned by the fork
    if parent.current_version_id:
        src_ver = db.query(RouteVersion).filter(
            RouteVersion.id == parent.current_version_id
        ).first()
        if src_ver:
            fork_ver_id = str(uuid.uuid4())
            db.add(RouteVersion(
                id=fork_ver_id,
                route_id=fork_id,
                author_id=current_user.user_id,
                message="Forked from parent",
                geometry_geojson=src_ver.geometry_geojson,
                distance_m=src_ver.distance_m,
                elevation_gain_m=src_ver.elevation_gain_m,
            ))

    fork = Route(
        id=fork_id,
        owner_id=current_user.user_id,
        name=(body.name if body and body.name else None) or f"{parent.name} (variante)",
        description=(body.description if body and body.description else None) or parent.description,
        sport=parent.sport,
        visibility="private",
        status="published",
        forked_from_id=route_id,
        current_version_id=fork_ver_id,
        distance_m=parent.distance_m,
        elevation_gain_m=parent.elevation_gain_m,
        geometry_geojson=parent.geometry_geojson,
    )
    db.add(fork)

    db.add(RouteFork(
        id=str(uuid.uuid4()),
        parent_route_id=route_id,
        fork_route_id=fork_id,
        forked_by_id=current_user.user_id,
    ))

    db.commit()
    db.refresh(fork)
    return _route_to_out(fork, db)


# ── Versions ──────────────────────────────────────────────────────────────────

@router.get("/routes/{route_id}/versions")
async def list_versions(
    route_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AuthenticatedUser | None, Depends(get_current_user_optional)] = None,
) -> list[VersionOut]:
    route = _get_route_or_404(db, route_id)
    if route.visibility == "private" and (not current_user or route.owner_id != current_user.user_id):
        raise HTTPException(status_code=404, detail="Route not found")
    versions = db.query(RouteVersion).filter(RouteVersion.route_id == route_id).all()
    return [_version_to_out(v) for v in versions]


@router.post("/routes/{route_id}/versions", status_code=201)
async def create_version(
    route_id: str,
    body: VersionCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> VersionOut:
    route = _get_route_or_404(db, route_id)
    _require_owner(route, current_user.user_id)

    version = RouteVersion(
        id=str(uuid.uuid4()),
        route_id=route_id,
        author_id=current_user.user_id,
        message=body.message,
        geometry_geojson=body.geometry_geojson,
        distance_m=body.distance_m,
        elevation_gain_m=body.elevation_gain_m,
    )
    db.add(version)
    route.current_version_id = version.id
    db.commit()
    return _version_to_out(version)


# ── Suggestions (local PRs) ───────────────────────────────────────────────────

@router.post("/routes/{route_id}/suggestions", status_code=201)
async def create_suggestion(
    route_id: str,
    body: SuggestionCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuggestionOut:
    route = _get_route_or_404(db, route_id)
    if route.visibility == "private" and route.owner_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="Route not found")
    sug = Suggestion(
        id=str(uuid.uuid4()),
        route_id=route_id,
        author_id=current_user.user_id,
        title=body.title,
        description=body.description,
        geometry_geojson=body.geometry_geojson,
        status="open",
    )
    db.add(sug)
    db.commit()
    return _suggestion_to_out(sug)


@router.get("/routes/{route_id}/suggestions")
async def list_suggestions(
    route_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AuthenticatedUser | None, Depends(get_current_user_optional)] = None,
) -> list[SuggestionOut]:
    route = _get_route_or_404(db, route_id)
    if route.visibility == "private" and (not current_user or route.owner_id != current_user.user_id):
        raise HTTPException(status_code=404, detail="Route not found")
    results = db.query(Suggestion).filter(Suggestion.route_id == route_id).all()
    return [_suggestion_to_out(s) for s in results]


# ── Tags ──────────────────────────────────────────────────────────────────────

@router.post("/routes/{route_id}/tags", status_code=201)
async def create_tag(
    route_id: str,
    body: TagCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TagOut:
    route = _get_route_or_404(db, route_id)
    _require_owner(route, current_user.user_id)

    # Check tag uniqueness
    existing = db.query(RouteTag).filter(
        RouteTag.route_id == route_id, RouteTag.tag == body.tag
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Tag already exists")

    version = db.query(RouteVersion).filter(RouteVersion.id == body.version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    tag = RouteTag(
        id=str(uuid.uuid4()),
        route_id=route_id,
        version_id=body.version_id,
        tag=body.tag,
        message=body.message,
    )
    db.add(tag)
    db.commit()
    return _tag_to_out(tag)


@router.get("/routes/{route_id}/forks", response_model=list[RouteOut])
async def list_forks(
    route_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> list[RouteOut]:
    """Return all public forks (variants) of a route."""
    _get_route_or_404(db, route_id)
    forks = db.query(Route).filter(
        Route.forked_from_id == route_id,
        Route.visibility == "public",
        Route.deleted_at.is_(None),
    ).all()
    return [_route_to_out(r, db) for r in forks]


# ── My Routes ─────────────────────────────────────────────────────────────

@router.get("/me/routes", response_model=list[MyRouteOut])
async def list_my_routes(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    sport: str | None = None,
    search: str | None = None,
    min_distance_m: float | None = None,
    max_distance_m: float | None = None,
    sort: str = "last_accessed",
    include_deleted: bool = False,
    limit: int = 200,
) -> list[MyRouteOut]:
    """List current user's routes (drafts included)."""
    q = db.query(Route).filter(Route.owner_id == current_user.user_id)
    if not include_deleted:
        q = q.filter(Route.deleted_at.is_(None))
    if sport:
        q = q.filter(Route.sport == sport)
    if search:
        q = q.filter(Route.name.ilike(f"%{search}%"))
    if min_distance_m is not None:
        q = q.filter(Route.distance_m >= min_distance_m)
    if max_distance_m is not None:
        q = q.filter(Route.distance_m <= max_distance_m)
    if sort == "distance_m":
        q = q.order_by(Route.distance_m.desc().nullslast())
    elif sort == "created_at":
        q = q.order_by(Route.created_at.desc())
    else:  # last_accessed
        q = q.order_by(Route.last_accessed_at.desc().nullslast(), Route.created_at.desc())
    q = q.limit(limit)
    return [_route_to_my_out(r, db) for r in q.all()]


# ── Drafts ─────────────────────────────────────────────────────────────────


class DraftUpsert(BaseModel):
    name: str = "Brouillon"
    sport: str = "road"
    geometry_geojson: str | None = None
    waypoints_json: str | None = None
    distance_m: float | None = None
    elevation_gain_m: float | None = None
    forked_from_id: str | None = None
    surface_pct: str | None = None


@router.get("/me/drafts", response_model=list[RouteOut])
async def list_my_drafts(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[RouteOut]:
    """Return current user's draft routes."""
    drafts = db.query(Route).filter(
        Route.owner_id == current_user.user_id,
        Route.status == "draft",
        Route.deleted_at.is_(None),
    ).all()
    return [_route_to_out(r, db) for r in drafts]


@router.put("/me/drafts/{route_id}", response_model=RouteOut)
async def upsert_draft(
    route_id: str,
    body: DraftUpsert,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RouteOut:
    """Upsert a draft route. Creates if new, updates if exists.

    Always sets status="draft" and visibility="private".
    """
    existing = db.query(Route).filter(Route.id == route_id).first()
    if existing:
        # Update existing — must be owner
        _require_owner(existing, current_user.user_id)
        existing.name = body.name
        existing.sport = body.sport
        existing.status = "draft"
        existing.visibility = "private"
        existing.geometry_geojson = body.geometry_geojson
        existing.waypoints_json = body.waypoints_json
        existing.distance_m = body.distance_m
        existing.elevation_gain_m = body.elevation_gain_m
        existing.surface_pct = body.surface_pct
        db.commit()
        db.refresh(existing)
        return _route_to_out(existing, db)
    else:
        # Create new draft
        route = Route(
            id=route_id,
            owner_id=current_user.user_id,
            name=body.name,
            description=None,
            sport=body.sport,
            visibility="private",
            status="draft",
            forked_from_id=body.forked_from_id,
            current_version_id=None,
            distance_m=body.distance_m,
            elevation_gain_m=body.elevation_gain_m,
            geometry_geojson=body.geometry_geojson,
            waypoints_json=body.waypoints_json,
            surface_pct=body.surface_pct,
        )
        db.add(route)
        db.commit()
        db.refresh(route)
        return _route_to_out(route, db)


@router.get("/routes/{route_id}/gpx")
async def export_route_gpx(
    route_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AuthenticatedUser | None, Depends(get_current_user_optional)] = None,
) -> Any:
    """Export a route's current geometry as GPX."""
    route = _get_route_or_404(db, route_id)
    # C2: visibility check — private routes only exportable by owner
    if route.visibility == "private" and (not current_user or route.owner_id != current_user.user_id):
        raise HTTPException(status_code=404, detail="Route not found")
    geometry_geojson = route.geometry_geojson
    if not geometry_geojson:
        raise HTTPException(status_code=404, detail="No geometry for this route")

    try:
        geojson = json.loads(geometry_geojson)
        coords = geojson.get("coordinates", [])
    except Exception:
        coords = []

    trkpts = "\n".join(
        f'    <trkpt lat="{pt[1]}" lon="{pt[0]}"></trkpt>'
        for pt in coords
    )

    # Include annotations as GPX waypoints (<wpt>)
    ICON_EMOJI = {
        "water": "💧", "food": "🍽️", "viewpoint": "👁️", "danger": "⚠️",
        "info": "ℹ️", "photo": "📸", "shelter": "⛺", "bike-shop": "🔧",
    }
    annotations = (
        db.query(RouteAnnotation)
        .filter(RouteAnnotation.route_id == route_id)
        .order_by(RouteAnnotation.dist_m.asc().nullslast())
        .all()
    )
    wpt_lines = []
    for ann in annotations:
        name = xml_escape(f"{ICON_EMOJI.get(ann.icon, '')} {ann.text or ann.icon}")
        wpt_lines.append(
            f'  <wpt lat="{ann.lat}" lon="{ann.lon}"><name>{name}</name><type>{ann.icon}</type></wpt>'
        )
    wpts_str = "\n".join(wpt_lines)

    route_name = xml_escape(route.name or "itineraire")
    gpx_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="CHEMINS COMMUNS">
{wpts_str}
  <trk>
    <name>{route_name}</name>
    <trkseg>
{trkpts}
    </trkseg>
  </trk>
</gpx>"""

    safe_name = route_name.replace(" ", "_").lower()[:40]
    return Response(
        content=gpx_content,
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.gpx"'},
    )


@router.get("/routes/{route_id}/tags")
async def list_tags(
    route_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AuthenticatedUser | None, Depends(get_current_user_optional)] = None,
) -> list[TagOut]:
    route = _get_route_or_404(db, route_id)
    if route.visibility == "private" and (not current_user or route.owner_id != current_user.user_id):
        raise HTTPException(status_code=404, detail="Route not found")
    tags = db.query(RouteTag).filter(RouteTag.route_id == route_id).all()
    return [_tag_to_out(t) for t in tags]


# ── Route Annotations ──────────────────────────────────────────────────────────

ANNOTATION_ICONS = {"water", "food", "viewpoint", "danger", "info", "photo", "shelter", "bike-shop"}


def _annotation_to_out(ann: RouteAnnotation) -> AnnotationOut:
    return AnnotationOut(
        id=str(ann.id),
        route_id=str(ann.route_id),
        author_id=str(ann.author_id),
        lon=ann.lon,
        lat=ann.lat,
        dist_m=ann.dist_m,
        icon=ann.icon,
        text=ann.text,
        created_at=ann.created_at.isoformat() if ann.created_at else None,
    )


@router.get("/routes/{route_id}/annotations")
async def list_annotations(
    route_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> list[AnnotationOut]:
    route = _get_route_or_404(db, route_id)
    if route.visibility == "private":
        raise HTTPException(status_code=404, detail="Route not found")
    annotations = (
        db.query(RouteAnnotation)
        .filter(RouteAnnotation.route_id == route_id)
        .order_by(RouteAnnotation.dist_m.asc().nullslast())
        .all()
    )
    return [_annotation_to_out(a) for a in annotations]


@router.post("/routes/{route_id}/annotations", status_code=201)
async def create_annotation(
    route_id: str,
    body: AnnotationCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AnnotationOut:
    route = _get_route_or_404(db, route_id)
    if str(route.owner_id) != str(user.user_id):
        raise HTTPException(status_code=403, detail="Only the route owner can add annotations")
    if body.icon not in ANNOTATION_ICONS:
        raise HTTPException(status_code=422, detail=f"Invalid icon: {body.icon}")
    ann = RouteAnnotation(
        route_id=route_id,
        author_id=str(user.user_id),
        lon=body.lon,
        lat=body.lat,
        dist_m=body.dist_m,
        icon=body.icon,
        text=body.text,
    )
    db.add(ann)
    db.commit()
    db.refresh(ann)
    return _annotation_to_out(ann)


@router.delete("/routes/{route_id}/annotations/{annotation_id}", status_code=204)
async def delete_annotation(
    route_id: str,
    annotation_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> None:
    ann = db.query(RouteAnnotation).filter(
        RouteAnnotation.id == annotation_id,
        RouteAnnotation.route_id == route_id,
    ).first()
    if not ann:
        raise HTTPException(status_code=404, detail="Annotation not found")
    if str(ann.author_id) != str(user.user_id):
        raise HTTPException(status_code=403, detail="Only the author can delete this annotation")
    db.delete(ann)
    db.commit()
