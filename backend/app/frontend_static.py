"""Serve the Next.js static export (``output: 'export'``) from FastAPI.

The export is baked into the backend image (``FRONTEND_DIR``). Next is built
with the default ``trailingSlash: false``, so each route is ONE file:
``/`` → ``index.html``, ``/map`` → ``map.html``, ``/auth/verify`` →
``auth/verify.html``, unknown → ``404.html``. Every page is a static route
(no ``[param]`` segment); client state lives in query params
(``/routes?id=…``, ``/auth/verify?token=…``, ``/strava?status=…``), so NO page
needs a "serve index.html for any path" SPA fallback.

Rules (frontend audit 2026-11-07):
- GET **and HEAD** — Next's ``<Link>`` prefetch probes with HEAD; a GET-only
  catch-all answered 405 and every prefetch failed (red console on each page).
- unknown path → **404** + ``404.html``. The old SPA fallback answered 200 with
  the home page (soft-404: junk URLs indexed, ``/favicon.ico`` served as HTML).
- trailing slash → **308** to the bare path (``/map/`` → ``/map``), query
  string kept. ``/map/`` used to fall through to the home page.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Match, Mount

# The Next.js static export references content-HASHED chunks from the HTML
# entry (index.html / map.html / …). If the browser heuristically caches the
# HTML (which it does when NO Cache-Control is sent), a returning visitor keeps
# an OLD HTML → old chunk refs → a stale JS bundle even after a fresh deploy.
# That stranded users on a broken build after a bad deploy window (2026-08-08:
# the heatmap URL was empty in the cached bundle → "no heatmap on /map"). Fix:
# HTML always revalidates (cheap 304 via etag), hashed assets stay immutable.
HTML_HEADERS = {"Cache-Control": "no-cache"}

# Browser navigation to these pages collides with API paths of the same name
# (GET /collections from a browser must return the page, not the JSON list).
FRONTEND_PAGES = {
    "collections", "routes", "discover", "stats", "strava", "account",
    "activities", "admin", "methode", "privacy",
}

_FRONTEND_METHODS = ("GET", "HEAD")


def html_response(path: Path, status_code: int = 200) -> FileResponse:
    return FileResponse(path, status_code=status_code, media_type="text/html", headers=HTML_HEADERS)


def _inside(root: Path, candidate: Path) -> bool:
    """True when ``candidate`` resolves inside ``root`` (no ``..`` escape)."""
    try:
        candidate.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def resolve_frontend_file(frontend_dir: Path, path: str) -> tuple[Path, bool] | None:
    """Map a URL path (no leading slash) to an exported file.

    Returns ``(file, is_html_page)`` or None when nothing matches.
    """
    path = path.strip("/")
    candidates: list[tuple[Path, bool]] = []
    if path:
        # Exact file (robots.txt, og-card.png, icon.svg, map.html…). Not
        # content-hashed → served no-cache so a deploy never strands it.
        candidates.append((frontend_dir / path, False))
        # Clean URL: /map → map.html, /auth/verify → auth/verify.html
        candidates.append((frontend_dir / f"{path}.html", True))
    # Directory index: / → index.html (and any dir that has one)
    candidates.append((frontend_dir / path / "index.html", True))
    for candidate, is_page in candidates:
        if candidate.is_file() and _inside(frontend_dir, candidate):
            return candidate, is_page
    return None


def serve_frontend_path(frontend_dir: Path, path: str, query: str = "") -> Response:
    """Response for a GET/HEAD on a non-API path. Pure: no app state."""
    if path.endswith("/") and path.strip("/"):
        # Canonical form is the bare path (Next export, trailingSlash: false).
        # Rebuilt from the stripped path so "//evil.example/" can never turn
        # into a protocol-relative open redirect.
        location = "/" + path.strip("/")
        if query:
            location += "?" + query
        return RedirectResponse(location, status_code=308)

    found = resolve_frontend_file(frontend_dir, path)
    if found is not None:
        file, is_page = found
        if is_page or file.suffix == ".html":
            return html_response(file)
        return FileResponse(file, headers=HTML_HEADERS)

    not_found = frontend_dir / "404.html"
    if not_found.is_file():
        return html_response(not_found, status_code=404)
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def register_frontend(app: FastAPI, frontend_dir: Path) -> None:
    """Wire the static export onto ``app``. Call AFTER every API router: the
    catch-all only sees paths no API route matched."""

    @app.middleware("http")
    async def frontend_html_priority(request: Request, call_next):
        """A browser navigating to a page whose name is also an API path gets
        the HTML page, not the API JSON."""
        if request.method in _FRONTEND_METHODS and frontend_dir.is_dir():
            first = request.url.path.strip("/").split("/")[0]
            accept = request.headers.get("accept", "")
            if first in FRONTEND_PAGES and "text/html" in accept:
                html_file = frontend_dir / f"{first}.html"
                if html_file.is_file():
                    return html_response(html_file)
            # Same collision one level down: /me/routes is BOTH the "Mes
            # itinéraires" page and the API list endpoint. Opened in a browser
            # it showed the raw API JSON ({"detail":"Not authenticated"},
            # 2026-11-11). A navigation (Accept: text/html) whose exact clean
            # path has an exported page gets that page; fetch() calls (Accept
            # */* or JSON) still reach the API.
            clean = request.url.path.strip("/")
            if "/" in clean and "text/html" in accept:
                nested = frontend_dir / f"{clean}.html"
                try:
                    is_page = nested.is_file() and _inside(frontend_dir, nested)
                except OSError:  # e.g. a 300-char segment → ENAMETOOLONG
                    is_page = False
                if is_page:
                    return html_response(nested)
        return await call_next(request)

    if not frontend_dir.is_dir():
        return

    from fastapi.staticfiles import StaticFiles

    class _ImmutableStaticFiles(StaticFiles):
        """/_next assets are content-hashed → cache them hard + immutable."""

        def file_response(self, *args, **kwargs):  # type: ignore[override]
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp

    # Serve /_next/static assets with immutable cache (hashed filenames).
    # StaticFiles answers HEAD natively.
    next_dir = frontend_dir / "_next"
    if next_dir.is_dir():
        app.mount("/_next", _ImmutableStaticFiles(directory=next_dir), name="next-static")

    @app.api_route("/{path:path}", methods=list(_FRONTEND_METHODS), include_in_schema=False)
    async def serve_frontend(path: str, request: Request) -> Response:
        """Catch-all: exported file / clean URL / 404.html."""
        if request.method == "HEAD" and _owned_by_api_get_route(request):
            # HEAD on a GET-only API path (/healthz, /readyz…) reached us only
            # because the catch-all accepts HEAD. Keep the pre-catch-all answer
            # (405 + Allow: GET) instead of the frontend 404 page.
            return JSONResponse(
                {"detail": "Method Not Allowed"}, status_code=405, headers={"Allow": "GET"},
            )
        return serve_frontend_path(frontend_dir, path, request.url.query)

    def _owned_by_api_get_route(request: Request) -> bool:
        """True when a route other than this catch-all serves GET on this path.
        Generic BaseRoute.matches: FastAPI >= 0.14x wraps include_router() in
        an `_IncludedRouter` (not an APIRoute) that delegates matching."""
        scope = {**request.scope, "method": "GET"}
        for route in request.app.router.routes:
            if getattr(route, "endpoint", None) is serve_frontend or isinstance(route, Mount):
                continue
            if route.matches(scope)[0] == Match.FULL:
                return True
        return False
