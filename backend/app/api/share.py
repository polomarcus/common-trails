"""Open Graph share endpoint.

Serves HTML with OG meta tags for route sharing previews (WhatsApp, Telegram,
Twitter, etc.). Crawlers read the meta tags; browsers get redirected to the
frontend map page.

Endpoints:
  GET /share/{route_id}              HTML page with OG meta tags + redirect
  GET /share/{route_id}/og-image     Dynamic PNG card (rendered server-side)

The og-image used to be SVG. WhatsApp / Twitter / iMessage / Slack /
LinkedIn silently drop SVG previews, so the share button effectively
produced a card-less link. Switched to PNG via Pillow on 2026-05-16.
"""
import io
import logging
import math
from pathlib import Path
from typing import Annotated
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user
from app.config import FRONTEND_URL
from app.db.models import Activity, Route
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["share"])

SPORT_EMOJI = {"road": "\U0001f6b4", "gravel": "\U0001f6b5", "mtb": "\U0001f6b5", "offroad": "\U0001f6b6", "running": "\U0001f3c3"}
SPORT_COLORS = {"road": "#2563eb", "gravel": "#d97706", "mtb": "#7c3aed", "offroad": "#0891b2", "running": "#dc2626"}
SPORT_LABELS_FR = {"road": "Route", "gravel": "Gravel", "mtb": "VTT", "offroad": "Off-road", "running": "Course"}


def _fmt_dist(m: float | None) -> str:
    if not m:
        return ""
    return f"{m / 1000:.1f} km"


def _fmt_elev(m: float | None) -> str:
    if not m:
        return ""
    return f"D+ {math.floor(m)} m"


@router.get("/share/{route_id}")
async def share_route_html(
    route_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    """Return HTML with Open Graph meta tags for social sharing previews."""
    route = db.query(Route).filter(Route.id == route_id).first()
    if not route:
        raise HTTPException(404, "Route not found")
    if route.visibility == "private":
        raise HTTPException(404, "Route not found")

    name = escape(route.name)
    sport = route.sport or "road"
    emoji = SPORT_EMOJI.get(sport, "\U0001f5fa")
    dist = _fmt_dist(route.distance_m)
    elev = _fmt_elev(route.elevation_gain_m)

    # Build description
    parts = [sport.capitalize()]
    if dist:
        parts.append(dist)
    if elev:
        parts.append(elev)
    description = escape(" · ".join(parts))

    og_title = f"{emoji} {name}"
    base_url = str(request.base_url).rstrip("/")
    og_image = f"{base_url}/share/{route_id}/og-image"
    frontend_url = f"{FRONTEND_URL.rstrip('/')}/map?route={route_id}"

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <title>{og_title} — Common Trails</title>

  <!-- Open Graph -->
  <meta property="og:type" content="website" />
  <meta property="og:title" content="{og_title}" />
  <meta property="og:description" content="{description}" />
  <meta property="og:image" content="{og_image}" />
  <meta property="og:image:type" content="image/png" />
  <meta property="og:image:width" content="1200" />
  <meta property="og:image:height" content="630" />
  <meta property="og:url" content="{frontend_url}" />
  <meta property="og:site_name" content="CHEMINS COMMUNS — Common Trails" />

  <!-- Twitter Card -->
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="{og_title}" />
  <meta name="twitter:description" content="{description}" />
  <meta name="twitter:image" content="{og_image}" />

  <!-- Redirect to frontend -->
  <meta http-equiv="refresh" content="0; url={frontend_url}" />
</head>
<body style="font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f5f5f0">
  <div style="text-align:center">
    <p style="font-size:16px;color:#555">Redirection vers la carte…</p>
    <a href="{frontend_url}" style="color:#1a4731;font-weight:700">Ouvrir l'itinéraire</a>
  </div>
  <script>window.location.replace("{frontend_url}");</script>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/share/activity/{activity_id}")
async def share_activity_html(
    activity_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> HTMLResponse:
    """Return HTML with Open Graph meta tags for activity sharing previews.

    Activities are PRIVATE data (no per-activity visibility column — private by
    definition). The requester must be the owner; non-owners get 404 (not 403)
    to avoid leaking the existence of another user's activity by id enumeration.
    """
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity or activity.user_id != current_user.user_id:
        raise HTTPException(404, "Activity not found")

    name = escape(activity.name or "Activité")
    sport = activity.sport or "road"
    emoji = SPORT_EMOJI.get(sport, "\U0001f5fa")
    dist = _fmt_dist(activity.distance_m)
    elev = _fmt_elev(activity.elevation_gain_m)

    parts = [sport.capitalize()]
    if dist:
        parts.append(dist)
    if elev:
        parts.append(elev)
    description = escape(" · ".join(parts))

    og_title = f"{emoji} {name}"
    base_url = str(request.base_url).rstrip("/")
    og_image = f"{base_url}/share/activity/{activity_id}/og-image"
    frontend_url = f"{FRONTEND_URL.rstrip('/')}/activities?id={activity_id}"

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <title>{og_title} — Common Trails</title>

  <!-- Open Graph -->
  <meta property="og:type" content="website" />
  <meta property="og:title" content="{og_title}" />
  <meta property="og:description" content="{description}" />
  <meta property="og:image" content="{og_image}" />
  <meta property="og:image:type" content="image/png" />
  <meta property="og:image:width" content="1200" />
  <meta property="og:image:height" content="630" />
  <meta property="og:url" content="{frontend_url}" />
  <meta property="og:site_name" content="CHEMINS COMMUNS — Common Trails" />

  <!-- Twitter Card -->
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="{og_title}" />
  <meta name="twitter:description" content="{description}" />
  <meta name="twitter:image" content="{og_image}" />

  <!-- Redirect to frontend -->
  <meta http-equiv="refresh" content="0; url={frontend_url}" />
</head>
<body style="font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f5f5f0">
  <div style="text-align:center">
    <p style="font-size:16px;color:#555">Redirection vers la trace…</p>
    <a href="{frontend_url}" style="color:#1a4731;font-weight:700">Ouvrir la trace</a>
  </div>
  <script>window.location.replace("{frontend_url}");</script>
</body>
</html>"""
    return HTMLResponse(content=html)


# ── Pillow renderer ──────────────────────────────────────────────────────────
#
# Font discovery: ``fonts-dejavu-core`` is apt-installed in the Dockerfile.
# On Debian-slim the typical paths are listed below; we fall back to PIL's
# default bitmap font if none match (visible-but-ugly, never breaks the
# response). For local dev on macOS the system fonts ship outside
# ``/usr/share/fonts`` so we try a few macOS paths too.
_FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]
_FONT_REGULAR_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]


def _find_font(candidates: list[str]) -> str | None:
    for p in candidates:
        if Path(p).exists():
            return p
    return None


_FONT_BOLD = _find_font(_FONT_BOLD_CANDIDATES)
_FONT_REGULAR = _find_font(_FONT_REGULAR_CANDIDATES)


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _og_card_png(title: str, sport: str, dist: str, elev: str) -> bytes:
    """Render a 1200×630 PNG OG card. Returns raw PNG bytes.

    Layout (mirrors the previous SVG):
      - Vertical gradient background (deep teal → forest green)
      - "CHEMINS COMMUNS" caption at top
      - Emoji + route title large in the middle
      - Sport · distance · D+ subtitle
      - Sport-coloured bar at the bottom
    """
    color_hex = SPORT_COLORS.get(sport, "#2d6a4f")
    label = SPORT_LABELS_FR.get(sport, sport.capitalize())
    # SPORT_EMOJI is intentionally NOT used in the rendered text — Pillow
    # via the bundled fonts-dejavu-core has no color-emoji glyphs, so an
    # emoji rendered to PNG comes out as ``□``. The sport is conveyed by
    # the coloured bar + dot + label instead.

    safe_title = title or "Itinéraire"
    if len(safe_title) > 40:
        safe_title = safe_title[:38] + "…"

    subtitle_parts = [label]
    if dist:
        subtitle_parts.append(dist)
    if elev:
        subtitle_parts.append(elev)
    subtitle = " · ".join(subtitle_parts)

    img = Image.new("RGB", (1200, 630), color=(11, 31, 26))
    draw = ImageDraw.Draw(img)
    # Vertical gradient (deep teal at top → forest green at bottom)
    top = (11, 31, 26)
    bot = (26, 71, 49)
    for y in range(630):
        ratio = y / 630
        r = int(top[0] + (bot[0] - top[0]) * ratio)
        g = int(top[1] + (bot[1] - top[1]) * ratio)
        b = int(top[2] + (bot[2] - top[2]) * ratio)
        draw.line([(0, y), (1200, y)], fill=(r, g, b))

    # Sport-coloured bar at the bottom
    sport_rgb = _hex_to_rgb(color_hex)
    draw.rectangle([(0, 590), (1200, 630)], fill=sport_rgb)

    # Fonts — fall back to PIL default if none found
    try:
        caption_font = ImageFont.truetype(_FONT_BOLD, 36) if _FONT_BOLD else ImageFont.load_default()
        title_font = ImageFont.truetype(_FONT_BOLD, 56) if _FONT_BOLD else ImageFont.load_default()
        subtitle_font = ImageFont.truetype(_FONT_REGULAR, 30) if _FONT_REGULAR else ImageFont.load_default()
    except Exception as exc:
        logger.warning("Pillow font load failed: %s — falling back to bitmap default", exc)
        caption_font = ImageFont.load_default()
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()

    # Caption — light-green letter-spaced (Pillow has no letter-spacing,
    # close enough by inserting hair-spaces)
    caption = "C H E M I N S C O M M U N S"
    bbox = draw.textbbox((0, 0), caption, font=caption_font)
    caption_w = bbox[2] - bbox[0]
    draw.text(((1200 - caption_w) // 2, 175), caption, font=caption_font, fill=(167, 196, 160))

    # Title — route name (no emoji, see comment above)
    bbox = draw.textbbox((0, 0), safe_title, font=title_font)
    title_w = bbox[2] - bbox[0]
    draw.text(((1200 - title_w) // 2, 280), safe_title, font=title_font, fill=(255, 255, 255))

    # Subtitle — sport · dist · elev
    bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    subtitle_w = bbox[2] - bbox[0]
    draw.text(((1200 - subtitle_w) // 2, 380), subtitle, font=subtitle_font, fill=(209, 213, 219))

    # Sport coloured dot — visual marker in the middle bottom area
    dot_y = 500
    dot_r_outer = 24
    dot_r_inner = 12
    # Outer halo (partial alpha via overlay)
    draw.ellipse(
        [(600 - dot_r_outer, dot_y - dot_r_outer), (600 + dot_r_outer, dot_y + dot_r_outer)],
        fill=sport_rgb,
    )
    draw.ellipse(
        [(600 - dot_r_inner, dot_y - dot_r_inner), (600 + dot_r_inner, dot_y + dot_r_inner)],
        fill=(255, 255, 255),
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _og_card_svg(title: str, sport: str, dist: str, elev: str) -> str:
    """Generate a 1200x630 SVG card for Open Graph previews."""
    color = SPORT_COLORS.get(sport, "#2d6a4f")
    label = SPORT_LABELS_FR.get(sport, sport.capitalize())
    emoji = SPORT_EMOJI.get(sport, "\U0001f5fa")

    subtitle_parts = [label]
    if dist:
        subtitle_parts.append(dist)
    if elev:
        subtitle_parts.append(elev)
    subtitle = escape(" · ".join(subtitle_parts))
    safe_title = escape(title)
    if len(safe_title) > 40:
        safe_title = safe_title[:38] + "\u2026"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0b1f1a"/>
      <stop offset="100%" stop-color="#1a4731"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="630" fill="url(#bg)"/>
  <rect x="0" y="590" width="1200" height="40" fill="{color}" opacity="0.8"/>
  <text x="600" y="200" text-anchor="middle" font-family="system-ui,-apple-system,sans-serif" font-size="36" fill="#a7c4a0" letter-spacing="3">CHEMINS COMMUNS</text>
  <text x="600" y="320" text-anchor="middle" font-family="system-ui,-apple-system,sans-serif" font-size="56" font-weight="800" fill="#ffffff">{emoji} {safe_title}</text>
  <text x="600" y="400" text-anchor="middle" font-family="system-ui,-apple-system,sans-serif" font-size="30" fill="#d1d5db">{subtitle}</text>
  <circle cx="600" cy="500" r="24" fill="{color}" opacity="0.3"/>
  <circle cx="600" cy="500" r="12" fill="{color}"/>
</svg>"""


@router.get("/share/{route_id}/og-image")
async def share_route_og_image(
    route_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Dynamic PNG Open Graph card for a route (1200\u00d7630).

    WhatsApp / Twitter / iMessage / Slack drop SVG og-images silently;
    PNG is the format the open-graph ecosystem agrees on. See module
    docstring for the history.
    """
    route = db.query(Route).filter(Route.id == route_id).first()
    if not route:
        raise HTTPException(404, "Route not found")
    if route.visibility == "private":
        raise HTTPException(404, "Route not found")

    png_bytes = _og_card_png(
        title=route.name,
        sport=route.sport or "road",
        dist=_fmt_dist(route.distance_m),
        elev=_fmt_elev(route.elevation_gain_m),
    )
    return Response(content=png_bytes, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/share/activity/{activity_id}/og-image")
async def share_activity_og_image(
    activity_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> Response:
    """Dynamic PNG Open Graph card for an activity (1200\u00d7630).

    Activities are PRIVATE \u2014 only the owner may render the card. Non-owners get
    404 (not 403) to avoid leaking the existence of another user's activity.
    """
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity or activity.user_id != current_user.user_id:
        raise HTTPException(404, "Activity not found")

    png_bytes = _og_card_png(
        title=activity.name or "Activit\u00e9",
        sport=activity.sport or "road",
        dist=_fmt_dist(activity.distance_m),
        elev=_fmt_elev(activity.elevation_gain_m),
    )
    return Response(content=png_bytes, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})
