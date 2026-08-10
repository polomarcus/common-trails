"""Raw-trace pivot (2026-07-29): under ``HEATMAP_DISPLAY_SOURCE=raw`` the ingest
path must NOT run OSM map-matching or write ``heat_edges`` — the community map
renders precise GPS traces straight from ``activities``, and the matcher
substrate (``osm_road_edges`` / ``osm_ways``) is DROPPED in prod.

These drive the REAL handlers (``ingest.ingest_activity``,
``internal_ingest.process_heat_compute``, ``activity_deletion.delete_user_activity``)
— no inline mirrors.

FAIL on the pre-change code:
  * raw manual_upload ingest writes heat_edges (edges_indexed > 0) instead of 0;
  * GDPR delete crashes when the heat tables are absent.
PASS after: raw ingest saves the activity with edges_indexed == 0 and touches no
heat_edges; GDPR delete succeeds with the heat tables gone.
"""
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import ingest_activity
from app.services.provenance import COMMUNITY_SOURCE, STRAVA_API_SOURCE

# Distinctive empty bbox (Gulf of Guinea) so heat_edges never collide with real
# dev data; short (<60 m) grid-fallback segments so a matched write WOULD land.
_USER = "raw-skip-user"
_LON0, _LAT0 = 7.4005, 4.4005
_BBOX = (7.4, 4.4, 7.41, 4.41)


def _geo(n: int = 6, step: float = 0.0002, jitter: float = 0.0) -> str:
    coords = [
        [round(_LON0 + i * step + jitter, 6), round(_LAT0 + i * step + jitter, 6)]
        for i in range(n)
    ]
    return json.dumps({"type": "LineString", "coordinates": coords})


def _count_edges() -> int:
    db = SessionLocal()
    try:
        return db.execute(sa_text(
            "SELECT COUNT(*) FROM heat_edges "
            "WHERE ST_Intersects(geometry, ST_MakeEnvelope(:a,:b,:c,:d,4326))"
        ), {"a": _BBOX[0], "b": _BBOX[1], "c": _BBOX[2], "d": _BBOX[3]}).scalar() or 0
    finally:
        db.close()


def _activity_exists(activity_id: str) -> bool:
    db = SessionLocal()
    try:
        return db.execute(
            sa_text("SELECT 1 FROM activities WHERE id = :i"), {"i": activity_id}
        ).fetchone() is not None
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _USER})
        db.execute(sa_text(
            "DELETE FROM heat_edge_contributors WHERE edge_key IN ("
            "  SELECT edge_key FROM heat_edges "
            "  WHERE ST_Intersects(geometry, ST_MakeEnvelope(:a,:b,:c,:d,4326)))"
        ), {"a": _BBOX[0], "b": _BBOX[1], "c": _BBOX[2], "d": _BBOX[3]})
        db.execute(sa_text(
            "DELETE FROM heat_edges "
            "WHERE ST_Intersects(geometry, ST_MakeEnvelope(:a,:b,:c,:d,4326))"
        ), {"a": _BBOX[0], "b": _BBOX[1], "c": _BBOX[2], "d": _BBOX[3]})
        db.commit()
    finally:
        db.close()


class TestRawSkipsMatchedIngest:
    def test_raw_manual_upload_saves_activity_without_heat_edges(self, monkeypatch):
        """The main ingest_activity gate (~line 3235). Under raw, a
        community-eligible upload is saved but writes NO heat_edges."""
        monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")
        before = _count_edges()
        r = ingest_activity(_USER, {
            "provider": "file", "provider_activity_id": None,
            "source": COMMUNITY_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(), "distance_m": 5000,
            "file_hash": "raw-manual-main",
            "activity_date": datetime(2025, 6, 1, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True)  # skip_heat_computation defaults False → inline
        assert r["status"] == "created"
        assert r["edges_indexed"] == 0, "raw mode must not run the matched heat path"
        assert _activity_exists(r["activity_id"]), "the activity must still be stored"
        assert _count_edges() == before, "raw mode must not write heat_edges"

    def test_matched_manual_upload_still_writes_heat_edges(self, monkeypatch):
        """Control: matched mode (the default) is UNCHANGED — a community-eligible
        upload still feeds heat_edges. Proves the skip is raw-specific."""
        monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "matched")
        before = _count_edges()
        r = ingest_activity(_USER, {
            "provider": "file", "provider_activity_id": None,
            "source": COMMUNITY_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(), "distance_m": 5000,
            "file_hash": "matched-manual-main",
            "activity_date": datetime(2025, 6, 2, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True)
        assert r["status"] == "created"
        assert r["edges_indexed"] > 0, "matched mode must still feed heat_edges"
        assert _count_edges() > before

    def test_raw_promote_path_skips_heat(self, monkeypatch):
        """The promote gate (~line 3150): a manual_upload landing over a
        strava_api twin promotes the row but, under raw, writes NO heat_edges."""
        monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")
        date = datetime(2025, 6, 3, 7, 0, tzinfo=UTC)
        strava = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "raw-promo-strava",
            "source": STRAVA_API_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(n=3, step=0.0004), "distance_m": 5000,
            "activity_date": date,
        }, contribute_heatmap=True)
        assert strava["status"] == "created"
        before = _count_edges()
        promo = ingest_activity(_USER, {
            "provider": "file", "provider_activity_id": None,
            "source": COMMUNITY_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(n=6, step=0.0002), "distance_m": 5030,  # +0.6% → dedup twin
            "file_hash": "raw-promo-manual",
            "activity_date": date,
        }, contribute_heatmap=True)
        assert promo["status"] == "promoted", "should hit the cross-source promote path"
        assert promo["edges_indexed"] == 0, "raw mode must not run the matched heat path on promote"
        assert _count_edges() == before, "raw promote must not write heat_edges"

    def test_raw_process_heat_compute_is_noop(self, monkeypatch):
        """The prod async chokepoint (single-GPX / /imports/files async both flow
        through process_heat_compute). Under raw it writes no heat_edges."""
        from app.api.internal_ingest import process_heat_compute

        # Store the activity in matched-off state via skip_heat_computation so no
        # inline heat runs; then invoke the async compute under raw.
        monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")
        stored = ingest_activity(_USER, {
            "provider": "file", "provider_activity_id": None,
            "source": COMMUNITY_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(), "distance_m": 5000,
            "file_hash": "raw-phc-manual",
            "activity_date": datetime(2025, 6, 4, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True, skip_heat_computation=True)
        assert stored["status"] == "created"
        before = _count_edges()
        r = process_heat_compute(stored["activity_id"], _USER)
        assert r["edges_indexed"] == 0, "raw process_heat_compute must not run matched heat"
        assert _count_edges() == before, "raw process_heat_compute must not write heat_edges"


class _AbsentHeatTablesExecute:
    """Instance-level ``Session.execute`` shim that makes the heat tables look
    DROPPED: ``information_schema`` existence checks for them return False, and
    ANY direct SQL touching them raises (as Postgres would with
    ``relation "…" does not exist``). ORM query/delete/commit go through the
    session's Connection, not this shim, so the activity + its FK-cascaded
    ``activity_cells`` are still deleted.
    """

    def __init__(self, real_execute):
        self._real = real_execute

    def __call__(self, statement, params=None, *args, **kwargs):
        sql = str(statement)
        if "information_schema.tables" in sql:
            name = (params or {}).get("n")
            if name in ("heat_edges", "heat_edge_contributors"):
                return _FalseScalar()
            return self._real(statement, params, *args, **kwargs)
        if "heat_edge" in sql:  # matches heat_edges / heat_edge_contributors
            raise RuntimeError('relation "heat_edge_contributors" does not exist')
        return self._real(statement, params, *args, **kwargs)


class _FalseScalar:
    def scalar(self):
        return False


class TestGdprDeleteWithHeatTablesAbsent:
    def test_delete_activity_works_when_heat_tables_dropped(self):
        """GDPR delete must still remove the activity (+ cascaded activity_cells)
        even when heat_edges / heat_edge_contributors are absent (raw pivot).

        FAILS on the pre-change code: the unconditional
        ``DELETE FROM heat_edge_contributors`` raises → the delete aborts."""
        from app.services.activity_deletion import delete_user_activity

        # Store a community activity with geometry (→ activity_cells rows).
        stored = ingest_activity(_USER, {
            "provider": "file", "provider_activity_id": None,
            "source": COMMUNITY_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(), "distance_m": 5000,
            "file_hash": "raw-gdpr-manual",
            "activity_date": datetime(2025, 6, 5, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True, skip_heat_computation=True)
        activity_id = stored["activity_id"]
        assert _activity_exists(activity_id)

        # activity_cells should exist for the stored geometry.
        chk = SessionLocal()
        try:
            n_cells = chk.execute(
                sa_text("SELECT COUNT(*) FROM activity_cells WHERE activity_id = :a"),
                {"a": activity_id},
            ).scalar() or 0
        finally:
            chk.close()
        assert n_cells > 0

        # Delete through a session whose heat tables look DROPPED.
        db = SessionLocal()
        try:
            db.execute = _AbsentHeatTablesExecute(db.execute)  # type: ignore[method-assign]
            ok = delete_user_activity(db, _USER, activity_id)
        finally:
            db.close()
        assert ok is True, "delete must succeed with heat tables absent"

        # Activity + cascaded activity_cells are gone (verified on a clean session).
        assert not _activity_exists(activity_id)
        v = SessionLocal()
        try:
            remaining = v.execute(
                sa_text("SELECT COUNT(*) FROM activity_cells WHERE activity_id = :a"),
                {"a": activity_id},
            ).scalar() or 0
        finally:
            v.close()
        assert remaining == 0, "activity_cells must cascade-delete"
