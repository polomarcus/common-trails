"""Static-export serving: HEAD, real 404, trailing slash, API untouched.

Frontend audit 2026-11-07 (P0 #2 + #3), all reproduced in prod:
- every page logged ``HEAD /map → 405``: Next ``<Link>`` prefetch probes with
  HEAD and the catch-all was GET-only, so no navigation was ever prefetched;
- ``/this-does-not-exist`` and ``/favicon.ico`` answered 200 with the HOME
  page (soft-404), and ``/map/`` showed the home page instead of the map;
  ``404.html`` was exported but never served.

Drives the REAL ``register_frontend`` on an app that carries the REAL API
routes of ``app.main`` (no DB needed), over a fake export tree laid out the
way ``next build`` with ``output: 'export'`` + ``trailingSlash: false`` writes
it (``map.html``, ``auth/verify.html``, ``404.html``…).
"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.frontend_static import register_frontend, resolve_frontend_file, serve_frontend_path

HOME = "<html><title>home</title><body>HOME</body></html>"
MAP = "<html><body>MAP</body></html>"
VERIFY = "<html><body>VERIFY</body></html>"
NOT_FOUND = "<html><body>PAGE-404</body></html>"


@pytest.fixture()
def export_dir(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    (out / "auth").mkdir(parents=True)
    (out / "_next" / "static" / "chunks").mkdir(parents=True)
    (out / "index.html").write_text(HOME)
    (out / "map.html").write_text(MAP)
    (out / "stats.html").write_text("<html><body>STATS</body></html>")
    (out / "auth" / "verify.html").write_text(VERIFY)
    (out / "404.html").write_text(NOT_FOUND)
    (out / "robots.txt").write_text("User-agent: *\nAllow: /\n")
    (out / "_next" / "static" / "chunks" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    return out


@pytest.fixture()
def client(export_dir: Path) -> TestClient:
    from app.main import app as real_app

    app = FastAPI()
    # The real API routes, minus the real app's own frontend wiring.
    for route in real_app.router.routes:
        if getattr(route, "name", None) in {"serve_frontend", "next-static"}:
            continue
        app.router.routes.append(route)
    register_frontend(app, export_dir)
    return TestClient(app, raise_server_exceptions=True)


# ── HEAD (Next prefetch) ──────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/", "/map", "/auth/verify"])
def test_head_on_pages_is_200_without_body(client, path):
    r = client.head(path)
    assert r.status_code == 200, f"HEAD {path} → {r.status_code} (prefetch needs 2xx)"
    assert "text/html" in r.headers["content-type"]
    assert r.content == b""


def test_head_on_next_asset_is_200(client):
    r = client.head("/_next/static/chunks/app.js")
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]


def test_head_with_html_accept_on_api_colliding_page(client):
    """/stats is both a page and an API path: the HTML priority middleware
    must also answer HEAD, not only GET."""
    r = client.head("/stats", headers={"accept": "text/html"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# ── Pages + real 404 ──────────────────────────────────────────────────────────

def test_get_pages_serve_their_own_html(client):
    assert "HOME" in client.get("/").text
    assert "MAP" in client.get("/map").text
    r = client.get("/auth/verify?token=abc")  # magic-link landing (query-param route)
    assert r.status_code == 200 and "VERIFY" in r.text
    assert r.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/this-does-not-exist", "/favicon.ico", "/en", "/me", "/map/extra"])
def test_unknown_path_is_404_with_404_page(client, path):
    r = client.get(path)
    assert r.status_code == 404, f"{path} → {r.status_code} (soft-404)"
    assert "PAGE-404" in r.text
    assert "HOME" not in r.text
    assert "text/html" in r.headers["content-type"]


def test_unknown_path_head_is_404(client):
    assert client.head("/this-does-not-exist").status_code == 404


def test_exact_static_file_is_served(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.text.startswith("User-agent")
    assert "text/plain" in r.headers["content-type"]


def test_404_without_404_page_is_json(tmp_path):
    (tmp_path / "index.html").write_text(HOME)
    r = serve_frontend_path(tmp_path, "nope")
    assert r.status_code == 404


# ── Trailing slash ────────────────────────────────────────────────────────────

def test_trailing_slash_redirects_to_bare_path(client):
    r = client.get("/map/", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/map"
    followed = client.get("/map/")
    assert followed.status_code == 200 and "MAP" in followed.text


def test_trailing_slash_keeps_query_string(client):
    r = client.get("/auth/verify/?token=abc", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/auth/verify?token=abc"


def test_trailing_slash_redirect_is_never_protocol_relative(tmp_path):
    # A request for "//evil.example/" reaches the catch-all as "/evil.example/".
    r = serve_frontend_path(tmp_path, "/evil.example/")
    assert r.status_code == 308
    assert r.headers["location"] == "/evil.example"


def test_root_is_not_redirected(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200 and "HOME" in r.text


def test_path_traversal_is_refused(export_dir):
    assert resolve_frontend_file(export_dir, "../secret.txt") is None
    assert resolve_frontend_file(export_dir, "../secret") is None


# ── API routes win over the catch-all ─────────────────────────────────────────

def test_api_routes_unaffected(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["status"] == "ok"

    r = client.get("/export/heatmap.pmtiles", follow_redirects=False)
    assert r.status_code == 302


@pytest.mark.parametrize("path", ["/healthz", "/readyz", "/export/heatmap.pmtiles"])
def test_head_on_get_only_api_route_keeps_405(client, path):
    """The catch-all accepts HEAD, so HEAD on a GET-only API path reaches it.
    It must keep the pre-catch-all answer (405 + Allow: GET), never the
    frontend 404 page (uptime checkers / CD probes would read it as "gone")."""
    r = client.head(path)
    assert r.status_code == 405, f"HEAD {path} → {r.status_code}"
    assert r.headers.get("allow") == "GET"
    assert "text/html" not in r.headers.get("content-type", "")


def test_api_404_stays_json_for_non_get(client):
    # POST is not served by the catch-all (GET/HEAD only) → never an HTML page.
    r = client.post("/this-does-not-exist")
    assert r.status_code in (404, 405)
    assert "PAGE-404" not in r.text
