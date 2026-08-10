"""Personal photos API — private, returns user's geolocated Strava photos as GeoJSON.

GET /me/photos — GeoJSON FeatureCollection of the user's activity photos.
Used by the frontend map to display photo markers.
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user
from app.db.models import ActivityPhoto
from app.db.session import get_db

router = APIRouter(tags=["me"])


@router.get("/me/photos")
async def me_photos(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Return user photos as GeoJSON FeatureCollection."""
    user_id = current_user.user_id

    photos = db.query(ActivityPhoto).filter(
        ActivityPhoto.user_id == user_id,
        ActivityPhoto.lat.isnot(None),
        ActivityPhoto.lon.isnot(None),
    ).all()

    features = []
    for p in photos:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [p.lon, p.lat],
            },
            "properties": {
                "id": p.id,
                "url_thumb": p.url_thumb,
                "url_medium": p.url_medium,
                "caption": p.caption,
                "activity_name": p.activity_name,
                "activity_id": p.activity_id,
            },
        })

    return {"type": "FeatureCollection", "features": features}
