"""Non-regression: the static-frontend HTML entry must NOT be heuristically
cached by browsers.

The Next static export references content-HASHED JS chunks from its HTML entry.
With NO Cache-Control on the HTML, browsers heuristic-cache it, so a returning
visitor keeps an OLD HTML → old chunk refs → a stale bundle even after a fresh
deploy (this stranded users on a broken build on 2026-08-08 → "no heatmap on
/map"). The fix serves every HTML response with ``Cache-Control: no-cache`` so
it always revalidates (cheap 304), while the hashed ``/_next`` assets stay
immutable.

Drives the REAL catch-all route via TestClient (fails on the pre-fix code that
sent no Cache-Control header; passes on the fix).
"""
from app.main import _FRONTEND_DIR


def test_frontend_html_entry_is_no_cache(client):
    idx = _FRONTEND_DIR / "index.html"
    created = False
    if not idx.exists():
        _FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
        idx.write_text("<html><body>ok</body></html>")
        created = True
    try:
        r = client.get("/", headers={"accept": "text/html"})
        assert r.status_code == 200, r.text
        assert "text/html" in r.headers.get("content-type", "")
        # THE fix: the HTML entry must revalidate, never be silently reused.
        assert r.headers.get("cache-control") == "no-cache", (
            f"HTML entry must be no-cache, got {r.headers.get('cache-control')!r} "
            "— heuristic caching strands returning visitors on a stale bundle"
        )
    finally:
        if created:
            idx.unlink()


def test_html_response_helper_sets_no_cache(tmp_path):
    """The SSOT helper every HTML route uses carries the header."""
    from app.main import _html_response

    f = tmp_path / "page.html"
    f.write_text("<html></html>")
    resp = _html_response(f)
    assert resp.media_type == "text/html"
    assert resp.headers["cache-control"] == "no-cache"
