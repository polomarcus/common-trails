"""The matched-era heatmap HTTP surface is gone; what is left stays fresh.

Prod logs showed zero requests in 30 days to the removed routes and nothing in
frontend / e2e / scripts calls them. ``/heatmap/summary``, ``/heatmap/dfci``
and ``/me/cells`` are kept.
"""
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import ingest_activity
from app.services.provenance import COMMUNITY_SOURCE

_USER = "summary-ttl-user"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/heatmap/stats?sport=road&zoom=14"),
        ("GET", "/heatmap/export?sport=road"),
        ("GET", "/heatmap/trails"),
        ("POST", "/heatmap/trails"),
        ("GET", "/heatmap/tiles/gravel/14/8305/5905.mvt"),
        ("GET", "/heatmap/display-url"),
        ("GET", "/me/unexplored"),
    ],
)
def test_removed_route_is_404(client, auth_headers, method, path):
    kwargs = {"json": []} if method == "POST" else {}
    resp = client.request(method, path, headers=auth_headers, **kwargs)
    # A removed POST answers 405, not 404: the only thing left matching the
    # path is the GET/HEAD SPA catch-all (app/frontend_static.py).
    expected = 405 if method == "POST" else 404
    assert resp.status_code == expected, f"{method} {path} still routed ({resp.status_code})"


def test_kept_routes_still_answer(client, auth_headers):
    assert client.get("/heatmap/summary").status_code == 200
    assert client.get("/heatmap/dfci").status_code == 200
    assert client.get("/me/cells", headers=auth_headers).status_code == 200


def test_heatmap_module_dropped_the_matched_surface():
    import app.api.heatmap as hm

    for name in ("_generate_tile", "_mvt_cache", "_reset_tile_caches",
                 "_start_tile_warmup", "_get_edge_version", "heatmap_stats",
                 "heatmap_export", "heatmap_trails", "heatmap_tile",
                 "heatmap_display_url", "unexplored_zones", "seed_trails"):
        assert not hasattr(hm, name), name


@pytest.fixture
def _cleanup_activities():
    yield
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _USER})
        db.commit()
    finally:
        db.close()


def test_summary_reflects_new_activity_after_ttl(client, monkeypatch, _cleanup_activities):
    """The summary cache is a short TTL, not a version key: nothing bumps a
    version on upload in the raw world, so a version-keyed cache froze the
    numbers for the life of the process. Clock is monkeypatched — no sleep."""
    import app.api.heatmap as hm

    clock = {"t": 10_000.0}
    monkeypatch.setattr(hm, "_monotonic", lambda: clock["t"])
    monkeypatch.setattr(hm, "_summary_cache", None)

    before = client.get("/heatmap/summary").json()["total_activities"]

    coords = [[round(7.2005 + i * 0.0002, 6), round(4.2005 + i * 0.0002, 6)] for i in range(6)]
    res = ingest_activity(_USER, {
        "provider": "file", "provider_activity_id": None,
        "source": COMMUNITY_SOURCE, "sport": "mtb",
        "geometry_geojson": json.dumps({"type": "LineString", "coordinates": coords}),
        "distance_m": 5000, "file_hash": "summary-ttl-1",
        "activity_date": datetime(2025, 9, 1, 8, 0, tzinfo=UTC),
    }, contribute_heatmap=True)
    assert res["status"] == "created"

    # Inside the TTL window: still the cached numbers.
    clock["t"] += hm._SUMMARY_CACHE_TTL_S - 1
    assert client.get("/heatmap/summary").json()["total_activities"] == before

    # Past the TTL: recomputed, the new community-eligible trace is counted.
    clock["t"] += 2
    after = client.get("/heatmap/summary").json()
    assert after["total_activities"] == before + 1
    assert after["source"] == "raw"
    assert after["license"] == "ODbL-1.0"
