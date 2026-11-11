"""Guards: the legacy "matched" heatmap pipeline is GONE.

The community map is built from ``activities`` (raw masked traces) and nothing
else. These tests pin that no code path reaches the old OSM-matched machinery:

* ``app.services.ingest`` exposes none of its symbols, the dead modules are not
  importable, and there is no display-source switch left in the code;
* the REAL write paths (``ingest_activity`` — create and cross-source promote —
  and ``delete_user_activity``) run no SQL against the matched tables
  (``heat_edges*``, ``heat_edge_contributors``, ``heat_cells*``, the OSM
  substrate, ``edge_version_state``, ``trail_edges``): they only write
  ``activities`` + ``activity_cells``.

The tables themselves still exist until the schema clean-up; the SQL capture
below is what proves the code no longer depends on them.
"""
import contextlib
import importlib.util
import json
import pathlib
import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import event
from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine

from app.db.session import SessionLocal
from app.services.ingest import ingest_activity
from app.services.provenance import COMMUNITY_SOURCE, STRAVA_API_SOURCE

_USER = "raw-skip-user"
_LON0, _LAT0 = 7.4005, 4.4005  # Gulf of Guinea — collides with nothing

_MATCHED_TABLES = re.compile(
    r"\b(heat_edges\w*|heat_edge_contributors|heat_cells|heat_cell_contributors|"
    r"osm_road_edges|osm_ways|osm_import_meta|edge_version_state|trail_edges)\b"
)


def _geo(n: int = 6, step: float = 0.0002) -> str:
    coords = [[round(_LON0 + i * step, 6), round(_LAT0 + i * step, 6)] for i in range(n)]
    return json.dumps({"type": "LineString", "coordinates": coords})


@contextlib.contextmanager
def _captured_sql():
    """Every statement the app sends to the DB while the block runs.

    Listens on the ``Engine`` CLASS, not ``app.db.session.engine``: another test
    may have rebuilt that module's engine, and the app resolves its session
    factory at call time."""
    statements: list[str] = []

    def _record(_conn, _cursor, statement, _params, _context, _executemany):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", _record)


def _assert_no_matched_sql(statements: list[str]) -> None:
    assert statements, "the SQL capture saw nothing — the listener is not wired"
    hits = [s for s in statements if _MATCHED_TABLES.search(s)]
    assert not hits, f"matched-pipeline tables were touched: {hits[:3]}"


def _counts(activity_id: str) -> tuple[int, int]:
    db = SessionLocal()
    try:
        acts = db.execute(
            sa_text("SELECT COUNT(*) FROM activities WHERE id = :a"), {"a": activity_id},
        ).scalar() or 0
        cells = db.execute(
            sa_text("SELECT COUNT(*) FROM activity_cells WHERE activity_id = :a"),
            {"a": activity_id},
        ).scalar() or 0
        return int(acts), int(cells)
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _USER})
        db.commit()
    finally:
        db.close()


# ── Code surface ─────────────────────────────────────────────────────────────

_INGEST_DENYLIST = (
    "_match_to_osm", "_update_heat_edges", "_maintain_heat_agg", "_upsert_edges_batch",
    "_update_heat_cells", "_edge_key", "_snap", "_OsmSegment", "_fetch_and_store_osm_tile",
    "_viterbi_point_matches", "get_heat_edges_public", "get_heat_cells_aggregated",
    "get_heatmap_summary", "get_personal_edges", "get_edge_version", "_bump_edge_version",
    "rebuild_heatmap_parallel", "ingest_activities_bulk", "store_trail_edges",
    "enrich_dfci_trail_slopes", "load_seed_gpx", "heat_user_id_hash",
)

_DEAD_MODULES = (
    "app.services.heat_aggregation", "app.services.heat_quality",
    "app.services.routing_profiles", "app.services.surface_classification",
    "app.services.tile_keys", "app.services.local_dem", "app.services.dem",
    "app.jobs.rebuild_heat_agg",
)


def test_ingest_module_exposes_no_matched_symbol():
    from app.services import ingest

    leftovers = [name for name in _INGEST_DENYLIST if hasattr(ingest, name)]
    assert leftovers == []


@pytest.mark.parametrize("module", _DEAD_MODULES)
def test_matched_modules_are_gone(module):
    assert importlib.util.find_spec(module) is None


def test_no_display_source_switch_left_in_the_app():
    """Raw is simply what the code does: no env switch, no ``raw_display_enabled``."""
    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = [
        str(path.relative_to(app_dir))
        for path in sorted(app_dir.rglob("*.py"))
        if re.search(r"HEATMAP_DISPLAY_SOURCE|raw_display_enabled", path.read_text())
    ]
    assert offenders == []


def test_retired_heat_compute_route_is_gone(client):
    resp = client.post("/internal/ingest/heat", json={"activity_id": "x", "user_id": "y"})
    assert resp.status_code in (404, 405)


# ── Write paths ──────────────────────────────────────────────────────────────


def test_upload_writes_activities_and_cells_only():
    with _captured_sql() as statements:
        r = ingest_activity(_USER, {
            "provider": "file", "provider_activity_id": None,
            "source": COMMUNITY_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(), "distance_m": 5000,
            "file_hash": "raw-manual-main",
            "activity_date": datetime(2025, 6, 1, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True)
    assert r["status"] == "created"
    assert r["edges_indexed"] == 0
    assert r["cells_indexed"] > 0
    acts, cells = _counts(r["activity_id"])
    assert acts == 1
    assert cells == r["cells_indexed"]
    _assert_no_matched_sql(statements)
    written = {m.group(1) for s in statements
               if (m := re.match(r"\s*INSERT INTO (\w+)", s, re.I))}
    assert written == {"activities", "activity_cells"}


def test_promote_path_touches_no_matched_table():
    """A manual_upload landing over a strava_api twin promotes the row — and
    still writes nothing but ``activities`` / ``activity_cells``."""
    date = datetime(2025, 6, 3, 7, 0, tzinfo=UTC)
    strava = ingest_activity(_USER, {
        "provider": "strava", "provider_activity_id": "raw-promo-strava",
        "source": STRAVA_API_SOURCE, "sport": "mtb",
        "geometry_geojson": _geo(n=3, step=0.0004), "distance_m": 5000,
        "activity_date": date,
    }, contribute_heatmap=True)
    assert strava["status"] == "created"
    with _captured_sql() as statements:
        promo = ingest_activity(_USER, {
            "provider": "file", "provider_activity_id": None,
            "source": COMMUNITY_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(n=6, step=0.0002), "distance_m": 5030,  # +0.6% → dedup twin
            "file_hash": "raw-promo-manual",
            "activity_date": date,
        }, contribute_heatmap=True)
    assert promo["status"] == "promoted", "should hit the cross-source promote path"
    assert promo["activity_id"] == strava["activity_id"]
    assert promo["edges_indexed"] == 0
    _assert_no_matched_sql(statements)


def test_gdpr_delete_removes_activity_and_cells_without_matched_sql():
    from app.services.activity_deletion import delete_user_activity

    stored = ingest_activity(_USER, {
        "provider": "file", "provider_activity_id": None,
        "source": COMMUNITY_SOURCE, "sport": "mtb",
        "geometry_geojson": _geo(), "distance_m": 5000,
        "file_hash": "raw-gdpr-manual",
        "activity_date": datetime(2025, 6, 5, 8, 0, tzinfo=UTC),
    }, contribute_heatmap=True)
    activity_id = stored["activity_id"]
    acts, cells = _counts(activity_id)
    assert acts == 1 and cells > 0

    db = SessionLocal()
    try:
        with _captured_sql() as statements:
            ok = delete_user_activity(db, _USER, activity_id)
    finally:
        db.close()
    assert ok is True
    _assert_no_matched_sql(statements)
    # Activity + FK-cascaded activity_cells are gone.
    assert _counts(activity_id) == (0, 0)
