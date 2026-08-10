"""Route collections API — group routes for organization and comparison.

Private endpoints (auth required):
  POST   /me/collections                              create collection
  GET    /me/collections                              list collections with route counts
  GET    /me/collections/{id}                         get collection with route summaries
  PUT    /me/collections/{id}                         update name/description/visibility
  DELETE /me/collections/{id}                         delete collection (not routes)
  POST   /me/collections/{id}/routes                  add route to collection
  PUT    /me/collections/{id}/routes/{route_id}       update route position
  DELETE /me/collections/{id}/routes/{route_id}       remove route from collection
  GET    /me/collections/{id}/compare                 geometries for up to 4 routes
  POST   /me/collections/{id}/annotations             create annotation
  PUT    /me/collections/{id}/annotations/{ann_id}    update annotation
  DELETE /me/collections/{id}/annotations/{ann_id}    delete annotation

Public endpoint (auth optional):
  GET    /collections/{id}                            public collection view
"""
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user, get_current_user_optional
from app.db.models import Route, RouteAnnotation, RouteCollection, RouteCollectionItem
from app.db.session import get_db

router = APIRouter(tags=["collections"])
collections_public_router = APIRouter(prefix="/collections", tags=["collections"])

ANNOTATION_ICONS = Literal[
    "water", "food", "camping", "refuge", "danger",
    "portage", "photo", "bike-shop", "info",
]
MAX_ROUTES_PER_COLLECTION = 20
MAX_ANNOTATIONS_PER_COLLECTION = 200


# ── Schemas ───────────────────────────────────────────────────────────────────

class CollectionCreate(BaseModel):
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    visibility: Literal["public", "unlisted", "private"] = "private"


class CollectionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    visibility: Literal["public", "unlisted", "private"] | None = None


class CollectionOut(BaseModel):
    id: str
    name: str
    description: str | None
    visibility: str = "private"
    route_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class CollectionDetailOut(CollectionOut):
    routes: list[dict]


class AddRouteBody(BaseModel):
    route_id: str
    position: int | None = None


class UpdateRoutePositionBody(BaseModel):
    position: int


class AnnotationCreate(BaseModel):
    icon: ANNOTATION_ICONS
    text: str | None = Field(default=None, max_length=1000)
    lat: float
    lon: float
    route_id: str | None = None


class AnnotationUpdate(BaseModel):
    icon: ANNOTATION_ICONS | None = None
    text: str | None = Field(default=None, max_length=1000)
    lat: float | None = None
    lon: float | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_collection_or_404(db: Session, collection_id: str, owner_id: str) -> RouteCollection:
    c = db.query(RouteCollection).filter(
        RouteCollection.id == collection_id,
        RouteCollection.owner_id == owner_id,
    ).first()
    if not c:
        raise HTTPException(404, "Collection not found")
    return c


def _collection_out(c: RouteCollection, count: int) -> CollectionOut:
    return CollectionOut(
        id=c.id,
        name=c.name,
        description=c.description,
        visibility=c.visibility or "private",
        route_count=count,
        created_at=c.created_at.isoformat() if c.created_at else None,
        updated_at=c.updated_at.isoformat() if c.updated_at else None,
    )


def _touch_updated_at(db: Session, collection: RouteCollection) -> None:
    """Explicitly set updated_at — ORM onupdate only fires on column changes."""
    collection.updated_at = datetime.now(UTC)


def _annotation_dict(a: RouteAnnotation) -> dict:
    return {
        "id": a.id,
        "icon": a.icon,
        "text": a.text,
        "lat": a.lat,
        "lon": a.lon,
        "route_id": a.route_id,
        "collection_id": a.collection_id,
        "author_id": a.author_id,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


# ── Private Endpoints (auth required) ────────────────────────────────────────

@router.post("/me/collections", response_model=CollectionOut, status_code=201)
async def create_collection(
    body: CollectionCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CollectionOut:
    c = RouteCollection(
        id=str(uuid.uuid4()),
        owner_id=current_user.user_id,
        name=body.name,
        description=body.description,
        visibility=body.visibility,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _collection_out(c, 0)


@router.get("/me/collections", response_model=list[CollectionOut])
async def list_collections(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[CollectionOut]:
    collections = db.query(RouteCollection).filter(
        RouteCollection.owner_id == current_user.user_id,
    ).order_by(RouteCollection.updated_at.desc()).all()
    result = []
    for c in collections:
        count = db.query(func.count(RouteCollectionItem.id)).filter(
            RouteCollectionItem.collection_id == c.id,
        ).scalar() or 0
        result.append(_collection_out(c, count))
    return result


@router.get("/me/collections/{collection_id}", response_model=CollectionDetailOut)
async def get_collection(
    collection_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CollectionDetailOut:
    c = _get_collection_or_404(db, collection_id, current_user.user_id)
    items = db.query(RouteCollectionItem).filter(
        RouteCollectionItem.collection_id == c.id,
    ).order_by(RouteCollectionItem.position).all()
    routes = []
    for item in items:
        route = db.query(Route).filter(Route.id == item.route_id, Route.deleted_at.is_(None)).first()
        if route:
            routes.append({
                "id": route.id,
                "name": route.name,
                "sport": route.sport,
                "distance_m": route.distance_m,
                "elevation_gain_m": route.elevation_gain_m,
                "visibility": route.visibility,
                "position": item.position,
                "added_at": item.added_at.isoformat() if item.added_at else None,
            })
    out = _collection_out(c, len(routes))
    return CollectionDetailOut(**out.model_dump(), routes=routes)


@router.put("/me/collections/{collection_id}", response_model=CollectionOut)
async def update_collection(
    collection_id: str,
    body: CollectionUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CollectionOut:
    c = _get_collection_or_404(db, collection_id, current_user.user_id)
    if body.name is not None:
        c.name = body.name
    if body.description is not None:
        c.description = body.description
    if body.visibility is not None:
        c.visibility = body.visibility
    _touch_updated_at(db, c)
    db.commit()
    db.refresh(c)
    count = db.query(func.count(RouteCollectionItem.id)).filter(
        RouteCollectionItem.collection_id == c.id,
    ).scalar() or 0
    return _collection_out(c, count)


@router.delete("/me/collections/{collection_id}", status_code=204)
async def delete_collection(
    collection_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    c = _get_collection_or_404(db, collection_id, current_user.user_id)
    db.delete(c)
    db.commit()


@router.post("/me/collections/{collection_id}/routes", status_code=201)
async def add_route_to_collection(
    collection_id: str,
    body: AddRouteBody,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    c = _get_collection_or_404(db, collection_id, current_user.user_id)
    # Enforce limit
    count = db.query(func.count(RouteCollectionItem.id)).filter(
        RouteCollectionItem.collection_id == c.id,
    ).scalar() or 0
    if count >= MAX_ROUTES_PER_COLLECTION:
        raise HTTPException(409, f"Collection cannot exceed {MAX_ROUTES_PER_COLLECTION} routes")
    # Verify route exists and user has access (own route or public/unlisted)
    route = db.query(Route).filter(Route.id == body.route_id, Route.deleted_at.is_(None)).first()
    if not route:
        raise HTTPException(404, "Route not found")
    if route.visibility == "private" and route.owner_id != current_user.user_id:
        raise HTTPException(404, "Route not found")
    # Check duplicate
    existing = db.query(RouteCollectionItem).filter(
        RouteCollectionItem.collection_id == c.id,
        RouteCollectionItem.route_id == body.route_id,
    ).first()
    if existing:
        raise HTTPException(409, "Route already in collection")
    # Auto-increment position
    if body.position is not None:
        pos = body.position
    else:
        max_pos = db.query(func.max(RouteCollectionItem.position)).filter(
            RouteCollectionItem.collection_id == c.id,
        ).scalar()
        pos = (max_pos + 1) if max_pos is not None else 0
    db.add(RouteCollectionItem(
        id=str(uuid.uuid4()),
        collection_id=c.id,
        route_id=body.route_id,
        position=pos,
    ))
    _touch_updated_at(db, c)
    db.commit()
    return {"status": "added", "position": pos}


@router.put("/me/collections/{collection_id}/routes/{route_id}")
async def update_route_position(
    collection_id: str,
    route_id: str,
    body: UpdateRoutePositionBody,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    c = _get_collection_or_404(db, collection_id, current_user.user_id)
    item = db.query(RouteCollectionItem).filter(
        RouteCollectionItem.collection_id == collection_id,
        RouteCollectionItem.route_id == route_id,
    ).first()
    if not item:
        raise HTTPException(404, "Route not in collection")
    item.position = body.position
    _touch_updated_at(db, c)
    db.commit()
    return {"status": "updated", "position": body.position}


@router.delete("/me/collections/{collection_id}/routes/{route_id}", status_code=204)
async def remove_route_from_collection(
    collection_id: str,
    route_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    c = _get_collection_or_404(db, collection_id, current_user.user_id)
    item = db.query(RouteCollectionItem).filter(
        RouteCollectionItem.collection_id == collection_id,
        RouteCollectionItem.route_id == route_id,
    ).first()
    if not item:
        raise HTTPException(404, "Route not in collection")
    db.delete(item)
    _touch_updated_at(db, c)
    db.commit()


@router.get("/me/collections/{collection_id}/compare")
async def compare_collection_routes(
    collection_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    """Return geometries for up to 4 routes in a collection (for map comparison)."""
    c = _get_collection_or_404(db, collection_id, current_user.user_id)
    items = db.query(RouteCollectionItem).filter(
        RouteCollectionItem.collection_id == c.id,
    ).order_by(RouteCollectionItem.position).limit(4).all()
    result = []
    for item in items:
        route = db.query(Route).filter(Route.id == item.route_id, Route.deleted_at.is_(None)).first()
        if not route:
            continue
        # Skip private routes not owned by requester
        if route.visibility == "private" and route.owner_id != current_user.user_id:
            continue
        if route.geometry_geojson:
            result.append({
                "id": route.id,
                "name": route.name,
                "sport": route.sport,
                "distance_m": route.distance_m,
                "elevation_gain_m": route.elevation_gain_m,
                "geometry_geojson": route.geometry_geojson,
            })
    return result


# ── Annotation CRUD (auth required) ──────────────────────────────────────────

@router.post("/me/collections/{collection_id}/annotations", status_code=201)
async def create_annotation(
    collection_id: str,
    body: AnnotationCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    c = _get_collection_or_404(db, collection_id, current_user.user_id)
    # Enforce limit
    ann_count = db.query(func.count(RouteAnnotation.id)).filter(
        RouteAnnotation.collection_id == c.id,
    ).scalar() or 0
    if ann_count >= MAX_ANNOTATIONS_PER_COLLECTION:
        raise HTTPException(409, f"Collection cannot exceed {MAX_ANNOTATIONS_PER_COLLECTION} annotations")
    ann = RouteAnnotation(
        id=str(uuid.uuid4()),
        collection_id=c.id,
        route_id=body.route_id,
        author_id=current_user.user_id,
        icon=body.icon,
        text=body.text,
        lat=body.lat,
        lon=body.lon,
    )
    db.add(ann)
    _touch_updated_at(db, c)
    db.commit()
    db.refresh(ann)
    return _annotation_dict(ann)


@router.put("/me/collections/{collection_id}/annotations/{annotation_id}")
async def update_annotation(
    collection_id: str,
    annotation_id: str,
    body: AnnotationUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    c = _get_collection_or_404(db, collection_id, current_user.user_id)
    ann = db.query(RouteAnnotation).filter(
        RouteAnnotation.id == annotation_id,
        RouteAnnotation.collection_id == c.id,
    ).first()
    if not ann:
        raise HTTPException(404, "Annotation not found")
    if ann.author_id != current_user.user_id:
        raise HTTPException(404, "Annotation not found")
    if body.icon is not None:
        ann.icon = body.icon
    if body.text is not None:
        ann.text = body.text
    if body.lat is not None:
        ann.lat = body.lat
    if body.lon is not None:
        ann.lon = body.lon
    _touch_updated_at(db, c)
    db.commit()
    db.refresh(ann)
    return _annotation_dict(ann)


@router.delete("/me/collections/{collection_id}/annotations/{annotation_id}", status_code=204)
async def delete_annotation(
    collection_id: str,
    annotation_id: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    c = _get_collection_or_404(db, collection_id, current_user.user_id)
    ann = db.query(RouteAnnotation).filter(
        RouteAnnotation.id == annotation_id,
        RouteAnnotation.collection_id == c.id,
    ).first()
    if not ann:
        raise HTTPException(404, "Annotation not found")
    if ann.author_id != current_user.user_id:
        raise HTTPException(404, "Annotation not found")
    db.delete(ann)
    _touch_updated_at(db, c)
    db.commit()


# ── Public Endpoint (auth optional) ──────────────────────────────────────────

@collections_public_router.get("/{collection_id}")
async def get_public_collection(
    collection_id: str,
    current_user: Annotated[AuthenticatedUser | None, Depends(get_current_user_optional)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Public collection view — public/unlisted accessible to all, private only to owner."""
    c = db.query(RouteCollection).filter(RouteCollection.id == collection_id).first()
    if not c:
        raise HTTPException(404, "Collection not found")

    user_id = current_user.user_id if current_user else None
    is_owner = user_id == c.owner_id

    # Visibility check: private → 404 for non-owners
    if c.visibility == "private" and not is_owner:
        raise HTTPException(404, "Collection not found")

    # Batch-fetch routes via items ordered by position
    items = db.query(RouteCollectionItem).filter(
        RouteCollectionItem.collection_id == c.id,
    ).order_by(RouteCollectionItem.position).all()

    route_ids = [item.route_id for item in items]
    routes_by_id = {}
    if route_ids:
        routes_list = db.query(Route).filter(Route.id.in_(route_ids), Route.deleted_at.is_(None)).all()
        routes_by_id = {r.id: r for r in routes_list}

    # Build route list with simplified geometries
    routes_out = []
    visible_route_ids = []
    total_distance = 0.0
    total_elevation = 0.0
    for item in items:
        route = routes_by_id.get(item.route_id)
        if not route:
            continue
        # Private routes: include with null geometry for non-owners
        route_visible = route.visibility != "private" or (user_id is not None and route.owner_id == user_id)
        geojson_simplified = None
        if route_visible and route.geometry_geojson:
            visible_route_ids.append(route.id)
            # Simplify geometry via PostGIS
            result = db.execute(
                text("SELECT ST_AsGeoJSON(ST_Simplify(ST_GeomFromGeoJSON(:geojson), 0.0001))"),
                {"geojson": route.geometry_geojson},
            ).scalar()
            geojson_simplified = result
            total_distance += route.distance_m or 0
            total_elevation += route.elevation_gain_m or 0
        routes_out.append({
            "id": route.id,
            "name": route.name if route_visible else "Route privée",
            "sport": route.sport if route_visible else None,
            "distance_m": route.distance_m if route_visible else None,
            "elevation_gain_m": route.elevation_gain_m if route_visible else None,
            "geometry_geojson": geojson_simplified,
            "position": item.position,
            "visible": route_visible,
        })

    # Fetch only annotations that belong to this collection (collection_id = c.id)
    annotations = db.query(RouteAnnotation).filter(
        RouteAnnotation.collection_id == c.id,
    ).all()
    annotations_out = [_annotation_dict(a) for a in annotations]

    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "visibility": c.visibility,
        "routes": routes_out,
        "annotations": annotations_out,
        "stats": {
            "route_count": len([r for r in routes_out if r["visible"]]),
            "total_distance_m": total_distance,
            "total_elevation_gain_m": total_elevation,
        },
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
