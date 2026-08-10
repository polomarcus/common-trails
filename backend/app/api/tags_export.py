"""Tag GPX export — stable download by route + tag name.

GET /routes/{route_id}/tags/{tag}/gpx
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user_optional
from app.db.models import Route, RouteTag, RouteVersion
from app.db.session import get_db
from app.services.gpx import geojson_to_gpx

router = APIRouter(tags=["tags"])


@router.get("/routes/{route_id}/tags/{tag}/gpx", response_class=Response)
async def export_tag_gpx_v2(
    route_id: str,
    tag: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AuthenticatedUser | None, Depends(get_current_user_optional)] = None,
) -> Response:
    """Export a tagged route version as GPX file.

    Accessible without auth for public/unlisted routes.
    Private routes require owner authentication.
    """
    route = db.query(Route).filter(Route.id == route_id).first()
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    # C2: visibility check — private routes only exportable by owner
    if route.visibility == "private" and (not current_user or route.owner_id != current_user.user_id):
        raise HTTPException(status_code=404, detail="Route not found")

    tag_record = db.query(RouteTag).filter(
        RouteTag.route_id == route_id, RouteTag.tag == tag
    ).first()
    if not tag_record:
        raise HTTPException(status_code=404, detail=f"Jalon '{tag}' introuvable sur l'itinéraire")

    version = db.query(RouteVersion).filter(
        RouteVersion.id == tag_record.version_id
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    gpx_name = f"{route.name or 'route'} ({tag})"
    gpx_content = geojson_to_gpx(version.geometry_geojson, name=gpx_name)

    return Response(
        content=gpx_content.encode("utf-8"),
        media_type="application/gpx+xml",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{route_id}-{tag}.gpx"'
            )
        },
    )
