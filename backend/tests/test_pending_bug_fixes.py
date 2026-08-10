"""Non-regression tests for the three pending-bug fixes.

Covers:
1. ``/routing/graph/ch/{sport}/regional.pb`` 500 + CORS regression
   (struct.pack uint8 overflow on raw ch_level + unbounded payload).
2. PMTiles export drops grid-fallback "spaghetti" edges (osm_way_id IS
   NULL AND ST_Length > 60m) — these are GPS-jump artifacts that the
   15m densifier explicitly skips and at K=1 render as straight lines
   crossing fields.
3. ``_update_heat_edges`` does not create grid-fallback edges longer
   than the 60m sanity cap — keeps the spaghetti out of the DB at
   write time, not just at read time.
"""
import json

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import _update_heat_edges

# Test edges live in a remote Iceland bbox (lon ≈ -18, lat ≈ 65) where the
# real heatmap has no data. We can't filter by sport: ingest normalizes
# anything starting with `test_` to `gravel` (see _normalize_heat_edge_sport).
_SPORT = "test_pending"
_TEST_BBOX = "ST_MakeEnvelope(-18.2, 65.6, -17.9, 65.8, 4326)"


def _wipe_test_bbox():
    db = SessionLocal()
    try:
        # Delete contributors first (FK), then edges in the test bbox.
        db.execute(sa_text(f"""
            DELETE FROM heat_edge_contributors
            WHERE edge_key IN (
                SELECT edge_key FROM heat_edges
                WHERE ST_Intersects(geometry, {_TEST_BBOX})
            )
        """))
        db.execute(sa_text(f"""
            DELETE FROM heat_edges
            WHERE ST_Intersects(geometry, {_TEST_BBOX})
        """))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clean_test_edges():
    """Wipe edges in the Iceland test bbox."""
    _wipe_test_bbox()
    yield
    _wipe_test_bbox()


# ── Bug #3: regional.pb 500 + CORS ───────────────────────────────────────────
#
# The companion TestEncodeCHShortcutsLevelOverflow class was removed with the
# graph_builder service (matched-era routing-graph binary encoder) at the
# raw-trace cutover — its sole subject, encode_ch_shortcuts_binary, is gone.

@pytest.mark.skip(
    reason=(
        "Routing subsystem retired at the raw-trace cutover (OSM substrate dropped) "
        "— the /routing/graph/ch/.../regional.pb endpoint goes with it. See "
        "reference_drop_osm_matching_plan / CLAUDE.md pivot. Re-enable only if routing is revived."
    )
)
class TestRegionalCHEndpoint:
    """GET /routing/graph/ch/{sport}/regional.pb — 200 + CORS for all CH sports."""

    def test_offroad_regional_returns_200(self, client):
        resp = client.get("/routing/graph/ch/offroad/regional.pb")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/octet-stream"

    def test_invalid_sport_returns_400_not_500(self, client):
        resp = client.get("/routing/graph/ch/swimming/regional.pb")
        assert resp.status_code == 400

    def test_regional_emits_cors_for_origin(self, client):
        # CORS middleware requires an Origin header to emit the
        # access-control-allow-origin response header.
        resp = client.get(
            "/routing/graph/ch/offroad/regional.pb",
            headers={"Origin": "http://localhost:3787"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:3787"

    def test_road_returns_empty_payload(self, client):
        # Road is not a CH sport — endpoint must return an empty CTGB v2,
        # not a 500.
        resp = client.get("/routing/graph/ch/road/regional.pb")
        assert resp.status_code == 200
        # 8-byte header only
        assert len(resp.content) == 8
        assert resp.content[:4] == b"CTGB"


# ── Bug #1: spaghetti heatmap ────────────────────────────────────────────────

class TestSpaghettiFilterPMTilesExport:
    """build_pmtiles export query excludes long grid-fallback edges."""

    def _capture_sql(self) -> str:
        """Run export_geojson against a fake DB; return the SQL it issued."""
        from app.jobs import build_pmtiles

        captured: dict = {}

        class _FakeResult:
            def __iter__(self):
                return iter([])

        class _FakeDB:
            def execute(self, query):
                captured["sql"] = str(query)
                return _FakeResult()

        # drop_grid_fallback=True EXPLICITLY: since 2026-07-20 (desire-lines
        # decision) the DEFAULT keeps grid fallback (env-resolved) — this
        # test pins the DROP path, i.e. the ops fallback
        # HEATMAP_KEEP_GRID_FALLBACK=false / --drop-grid-fallback, where the
        # zero-heat_edges-scan OOM invariant (#442) must keep holding.
        build_pmtiles.export_geojson(
            _FakeDB(), "/tmp/pending_bug_test_export.geojsonl",
            ['user_count', 'sport', 'heat_score'], min_uc=1,
            drop_grid_fallback=True,
        )
        return captured["sql"]

    # NOTE (2026-06: matview-drop refactor): the long-grid-fallback (>60 m) and
    # solo-grid-fallback (user_count < 2) spaghetti filters moved into the shared
    # `build_heat_aggregation_sql` builder, so the old source-string greps here
    # (`"ST_Length(...) > 60"` / `"user_count < 2"`) are obsolete AND were the
    # vacuous "assert on SQL text" anti-pattern. The BEHAVIOUR is now pinned by
    # REAL execute-and-assert coverage: `test_pmtiles_kanon_export.py` seeds a
    # uc=1 edge and a >60 m grid-fallback edge and asserts BOTH are excluded from
    # the produced features. So the two grep tests are removed (not weakened).

    def test_export_reads_pre_aggregated_heat_edges_agg(self):
        """The PMTiles export reads the pre-materialised ``heat_edges_agg``
        table (migration 0057) for the OSM-matched half — one row per
        (osm_way_id, sport) — instead of re-running the ~5 M-row GROUP BY at
        build time (which OOMed on db-f1-micro).

        The by-way aggregation itself (GROUP BY osm_way_id + smooth way
        geometry + MAX-not-SUM) now lives on the WRITE path and is pinned by
        the execute-and-assert PARITY suite ``test_heat_edges_agg.py`` +
        ``test_pmtiles_aggregation.py`` — not by grepping this SQL string.
        """
        sql = self._capture_sql()
        assert "heat_edges_agg" in sql, (
            "export must read the pre-materialised aggregate table, not "
            "re-aggregate heat_edges live (OOM on db-f1-micro)"
        )
        # drop_grid_fallback=True → pure agg read, ZERO heat_edges scan (the
        # OOM fix). NOTE: since 2026-07-20 the DEFAULT keeps grid fallback
        # (desire lines), which legitimately reads the NULL-osm_way_id half
        # of heat_edges live — the zero-scan invariant applies to the DROP
        # path only.
        assert "FROM heat_edges he" not in sql, (
            "the drop-grid PMTiles export must NOT scan heat_edges "
            "(drop_grid_fallback=True → agg-only read)"
        )


import re


def _references_raw_heat_edges(sql: str) -> bool:
    """True if `sql` actually READS the raw 5 M-row ``heat_edges`` table (the
    f1-micro OOM path, #442) — as opposed to the light ``heat_edges_agg``
    aggregate, another table's ``heat_edges`` COLUMN, or a ``pg_class``
    estimate.

    Matches ``heat_edges`` ONLY when it appears as a table right after
    ``FROM`` / ``JOIN``, and NOT when immediately followed by ``_`` (so
    ``heat_edges_agg`` / ``heat_edges_contributors`` / ``heat_edges_offroad``
    are excluded). This deliberately does NOT match:
      - ``INSERT INTO heatmap_metrics (heat_edges, ...)`` — a COLUMN name on a
        DIFFERENT table, not a scan (added by the admin heatmap-monitoring
        snapshot, #447);
      - ``FROM pg_class WHERE relname LIKE 'heat_edges_%'`` — the cheap
        reltuples estimate, not a table scan;
      - ``FROM heat_edges_agg`` — the light aggregate.
    A genuine ``SELECT ... FROM heat_edges`` (optionally aliased, e.g.
    ``FROM heat_edges he``) still trips it — the #442 regression must fail.
    """
    return re.search(r"\b(?:FROM|JOIN)\s+heat_edges\b(?!_)", sql, re.IGNORECASE) is not None


class TestReferencesRawHeatEdgesHelper:
    """Keep ``_references_raw_heat_edges`` honest: it must catch a REAL raw
    scan (the #442 regression) but NOT false-flag a column name or the light
    aggregate / pg_class estimate (the #447 admin-monitoring false positive).
    """

    def test_catches_a_genuine_raw_scan(self):
        assert _references_raw_heat_edges("SELECT COUNT(*) FROM heat_edges")
        assert _references_raw_heat_edges("SELECT * FROM heat_edges he WHERE ...")
        assert _references_raw_heat_edges("SELECT * FROM a JOIN heat_edges ON a.k = heat_edges.k")
        # Case-insensitive.
        assert _references_raw_heat_edges("select 1 from heat_edges")

    def test_excludes_the_light_aggregate(self):
        assert not _references_raw_heat_edges("SELECT COUNT(*) FROM heat_edges_agg WHERE user_count >= 1")
        assert not _references_raw_heat_edges("... FROM heat_edges_contributors ...")

    def test_excludes_heatmap_metrics_column_name(self):
        # The #447 false positive: `heat_edges` is a COLUMN of heatmap_metrics,
        # not a scanned table — must NOT be flagged.
        assert not _references_raw_heat_edges(
            "INSERT INTO heatmap_metrics (heat_edges, agg_ways, activities) "
            "VALUES (:heat_edges, :agg_ways, :activities)"
        )

    def test_excludes_pg_class_reltuples_estimate(self):
        assert not _references_raw_heat_edges(
            "SELECT COALESCE(SUM(GREATEST(reltuples, 0)), 0)::bigint FROM pg_class "
            "WHERE relkind = 'r' AND relname LIKE 'heat_edges_%' "
            "AND relname <> 'heat_edges_agg'"
        )


class TestBuildPmtilesLightPathNoRawScan:
    """The default (light) ``build_pmtiles`` path must never scan raw
    ``heat_edges``.

    Regression for the db-f1-micro incident (fast-follow to #441): #441
    moved the PMTiles AGGREGATION onto ``heat_edges_agg`` but left two
    vestigial ``SELECT COUNT(*) FROM heat_edges`` queries (the pre-build log
    line + the pointer-JSON edge_count). A full COUNT over the 5 M-row table
    severed the f1-micro connection ("server closed the connection
    unexpectedly") BEFORE the light agg build could run — defeating the point
    of #441 (PMTiles build with no DB-tier bump).
    """

    # One valid GeoJSONL feature row so a REAL export_geojson writes >=1 feature
    # → written>0 → main() runs the tippecanoe + upload deliverables (the
    # build_pmtiles 0-feature guard skips both on an empty corpus). Iterated
    # ONLY by the real export SELECT; COUNT queries use .scalar().
    _FEATURE_ROW = (
        '{"type":"Feature","geometry":{"type":"LineString","coordinates":'
        '[[3.80,43.60],[3.81,43.61]]},"properties":{"sport":"gravel",'
        '"user_count":1,"pass_count":1,"heat_score":0.5,"highway_type":""}}',
    )

    class _FakeResult:
        def __init__(self, scalar_value: int = 0, rows=None):
            self._scalar = scalar_value
            self._rows = rows if rows is not None else []

        def scalar(self):
            return self._scalar

        def __iter__(self):
            return iter(self._rows)

    class _RecordingDB:
        """Captures every SQL string executed; returns an empty result — except
        when ``feed_export_row`` is set, the non-COUNT SELECT that
        ``export_geojson`` iterates yields ONE feature row so a REAL export
        writes >=1 feature (→ the deliverable path runs under the 0-feature
        guard). COUNT(*) queries always return a scalar (empty rows)."""

        def __init__(self, feed_export_row: bool = False):
            self.queries: list[str] = []
            self._feed = feed_export_row

        def execute(self, query, *args, **kwargs):
            q = str(query)
            self.queries.append(q)
            outer = TestBuildPmtilesLightPathNoRawScan
            if self._feed and "COUNT(*)" not in q and "SELECT" in q.upper():
                return outer._FakeResult(rows=[outer._FEATURE_ROW])
            return outer._FakeResult()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    def test_pre_build_count_reads_agg_not_raw(self):
        """No COUNT on the build path may scan the raw 5 M-row ``heat_edges``
        table (the query that dropped the f1-micro connection, #442). Cheap
        indexed COUNTs are fine — the pre-build size count over the light
        ``heat_edges_agg`` aggregate AND the homepage-stats ``COUNT(*) FROM
        activities`` (community-stats change: ~2.7 k indexed rows, NOT the
        5 M raw table)."""
        from unittest.mock import patch

        import app.db.session as db_session
        from app.jobs import build_pmtiles

        rec = self._RecordingDB()
        with patch.object(db_session, "SessionLocal", lambda: rec), \
                patch.object(build_pmtiles.shutil, "which", return_value="/usr/bin/tippecanoe"), \
                patch.object(build_pmtiles, "export_geojson", return_value=0), \
                patch.object(build_pmtiles, "run_tippecanoe"), \
                patch.object(build_pmtiles, "_upload_to_export_bucket"), \
                patch.object(build_pmtiles, "_emit_heat_quality_metrics"), \
                patch.object(build_pmtiles.os.path, "getsize", return_value=0), \
                patch.object(build_pmtiles.os, "unlink"):
            # min_uc=1 is the argparse default — the exact path prod runs.
            build_pmtiles.main("/tmp", 6, 15, min_uc=1)

        count_queries = [q for q in rec.queries if "COUNT(*)" in q]
        assert count_queries, "expected the pre-build COUNT to run"
        # The load-bearing invariant (#442): a COUNT must never touch raw
        # ``heat_edges``. A regression to ``COUNT(*) FROM heat_edges`` still
        # fails here (``_references_raw_heat_edges`` matches ``heat_edges`` not
        # followed by ``_``); ``heat_edges_agg`` / ``activities`` counts pass.
        for q in count_queries:
            assert not _references_raw_heat_edges(q), (
                f"pre-build COUNT scanned raw heat_edges — this severs the "
                f"f1-micro connection before the build. SQL: {q}"
            )
        # Keep biting on the ORIGINAL bug: the pre-build size COUNT must still
        # read the light aggregate (not be silently dropped).
        assert any("heat_edges_agg" in q for q in count_queries), (
            "the pre-build size COUNT must read heat_edges_agg (the #441/#442 fix)"
        )

    def test_main_drop_grid_path_issues_zero_raw_heat_edges_scans(self):
        """End-to-end: ``main`` on the DROP-GRID path (min_uc=1,
        drop_grid_fallback=True — the ops fallback
        ``HEATMAP_KEEP_GRID_FALLBACK=false`` / ``--drop-grid-fallback``)
        issues NO raw ``heat_edges`` query. Every recorded statement is
        checked against ``FROM heat_edges`` (without ``_agg``). Only the
        post-upload, resilient ``_emit_heat_quality_metrics`` may touch raw
        heat_edges — it is stubbed here and separately pinned to run strictly
        AFTER the deliverables (see ``test_upload_precedes_quality_metrics``).

        NOTE (2026-07-20 desire-lines decision): the DEFAULT path now KEEPS
        grid fallback, which legitimately reads the NULL-osm_way_id half of
        heat_edges live — so this zero-scan invariant is pinned on the drop
        path, where the #442 OOM guarantee must keep holding.

        Drives the REAL ``export_geojson`` (light ``build_agg_read_sql`` path)
        against a recording DB so the aggregation query is exercised too.
        """
        from unittest.mock import patch

        import app.db.session as db_session
        from app.jobs import build_pmtiles

        rec = self._RecordingDB(feed_export_row=True)  # >=1 feature → deliverables run

        def _fake_tippecanoe(geojson_path, output, *a, **k):
            with open(output, "w"):
                pass

        with patch.object(db_session, "SessionLocal", lambda: rec), \
                patch.object(build_pmtiles.shutil, "which", return_value="/usr/bin/tippecanoe"), \
                patch.object(build_pmtiles, "run_tippecanoe", side_effect=_fake_tippecanoe), \
                patch.object(build_pmtiles, "_upload_to_export_bucket") as _up, \
                patch.object(build_pmtiles, "_emit_heat_quality_metrics") as _q:
            build_pmtiles.main("/tmp", 6, 15, min_uc=1, drop_grid_fallback=True)

        raw = [q for q in rec.queries if _references_raw_heat_edges(q)]
        assert not raw, (
            "default build path must issue ZERO raw heat_edges scans "
            f"(f1-micro OOM). Offending queries: {raw}"
        )
        assert not any("FROM heat_edges " in q or "FROM heat_edges\n" in q
                       for q in rec.queries), (
            "no statement may read `FROM heat_edges` (without _agg) on the "
            "default build path"
        )
        # And the deliverables actually ran before quality metrics.
        assert _q.called, "quality metrics should still be invoked (post-upload)"
        assert _up.called, "the GCS export upload should still be invoked"
        # Sanity: the light agg read WAS exercised.
        assert any("heat_edges_agg" in q for q in rec.queries), (
            "expected the light heat_edges_agg aggregation to run"
        )

    def test_upload_precedes_quality_metrics(self):
        """The PMTiles + GCS upload is the deliverable; a raise in the
        post-build quality audit must NOT prevent it. Proves the ordering
        invariant — ``_emit_heat_quality_metrics`` (the only raw-heat_edges
        touch left) runs strictly AFTER ``_upload_to_export_bucket``, so even
        if a heavy raw scan there severs the connection, the artefact already
        shipped.
        """
        from unittest.mock import patch

        import app.db.session as db_session
        from app.jobs import build_pmtiles

        rec = self._RecordingDB()

        with patch.object(db_session, "SessionLocal", lambda: rec), \
                patch.object(build_pmtiles.shutil, "which", return_value="/usr/bin/tippecanoe"), \
                patch.object(build_pmtiles, "export_geojson", return_value=1), \
                patch.object(build_pmtiles, "run_tippecanoe"), \
                patch.object(build_pmtiles, "_upload_to_export_bucket") as _up, \
                patch.object(build_pmtiles, "_emit_heat_quality_metrics",
                             side_effect=RuntimeError("connection severed")), \
                patch.object(build_pmtiles.os.path, "getsize", return_value=0), \
                patch.object(build_pmtiles.os, "unlink"), \
                pytest.raises(RuntimeError):
            build_pmtiles.main("/tmp", 6, 15, min_uc=1)

        assert _up.called, (
            "the PMTiles upload (deliverable) must run BEFORE the quality "
            "audit — a raise in quality metrics can't prevent the upload"
        )


class TestPMTilesContinuousHeatmap:
    """Integration: emitted GeoJSON is one smooth feature per OSM way.

    This guards the "continuous heatmap" property at the data level —
    if the SQL ever regresses to per-heat_edge geometry, this test fails
    immediately rather than waiting for someone to spot the spaghetti
    in a screenshot.
    """

    _OSM_WAY_ID = 999_888_777_001  # arbitrary, picked to not collide
    _OSM_BBOX = "ST_MakeEnvelope(-18.5, 65.5, -17.5, 66.0, 4326)"

    @pytest.fixture(autouse=True)
    def _clean_osm(self):
        def _wipe():
            db = SessionLocal()
            try:
                db.execute(sa_text(
                    "DELETE FROM osm_road_edges WHERE osm_way_id = :w"
                ), {"w": self._OSM_WAY_ID})
                db.execute(sa_text(
                    "DELETE FROM osm_ways WHERE osm_way_id = :w"
                ), {"w": self._OSM_WAY_ID})
                db.execute(sa_text(
                    "DELETE FROM heat_edges_agg WHERE osm_way_id = :w"
                ), {"w": self._OSM_WAY_ID})
                db.commit()
            finally:
                db.close()
        _wipe()
        yield
        _wipe()

    def _insert_osm_way_with_5_points(self):
        """Insert one OSM way carrying a 5-point smooth curve."""
        db = SessionLocal()
        try:
            # 5 points along a curve in the Iceland bbox
            line_wkt = (
                "LINESTRING(-18.10 65.70, -18.099 65.7005, "
                "-18.098 65.7008, -18.097 65.7012, -18.096 65.7015)"
            )
            # Real schema (0056): tile_key BIGINT, osm_way_id, segment_idx,
            # surface, highway, geometry; the full-way polyline lives in the
            # osm_ways side-table.
            db.execute(sa_text("""
                INSERT INTO osm_road_edges (
                    tile_key, osm_way_id, segment_idx,
                    surface, highway, geometry
                )
                VALUES (
                    0, :w, 0,
                    'asphalt', 'residential',
                    ST_GeomFromText(:wkt, 4326)
                )
            """), {"w": self._OSM_WAY_ID, "wkt": line_wkt})
            db.execute(sa_text("""
                INSERT INTO osm_ways (osm_way_id, way_geometry)
                VALUES (:w, ST_GeomFromText(:wkt, 4326))
                ON CONFLICT (osm_way_id) DO UPDATE
                SET way_geometry = EXCLUDED.way_geometry
            """), {"w": self._OSM_WAY_ID, "wkt": line_wkt})
            db.commit()
        finally:
            db.close()

    def _insert_heat_edge(
        self, lon1: float, lat1: float, lon2: float, lat2: float,
        user_count: int, osm_way_id: int | None,
    ):
        """Insert one heat_edge with explicit user_count + osm_way_id."""
        db = SessionLocal()
        try:
            edge_key = (
                f"{_SPORT}/{lat1:.5f},{lon1:.5f}/{lat2:.5f},{lon2:.5f}"
            )
            db.execute(sa_text("""
                INSERT INTO heat_edges (
                    edge_key, sport, geometry, user_count, pass_count,
                    forward_count, backward_count, osm_way_id
                )
                VALUES (
                    :ek, :sp,
                    ST_SetSRID(ST_MakeLine(
                        ST_MakePoint(:lo1, :la1), ST_MakePoint(:lo2, :la2)
                    ), 4326),
                    :uc, :uc, :uc, 0, :ow
                )
                ON CONFLICT (edge_key, sport) DO UPDATE SET
                    user_count = EXCLUDED.user_count,
                    osm_way_id = EXCLUDED.osm_way_id
            """), {
                "ek": edge_key, "sp": _SPORT,
                "lo1": lon1, "la1": lat1, "lo2": lon2, "la2": lat2,
                "uc": user_count, "ow": osm_way_id,
            })
            db.commit()
        finally:
            db.close()

    def _run_export(self, **kw) -> list[dict]:
        """Run export_geojson against the real DB, return parsed features."""
        from app.jobs import build_pmtiles
        from app.jobs.rebuild_heat_agg import recompute_heat_agg_for_ways

        out_path = "/tmp/pmtiles_continuous_test.geojsonl"
        db = SessionLocal()
        try:
            # export_geojson now reads the pre-materialised heat_edges_agg
            # (migration 0057) for the OSM-matched half — populate the agg row
            # for the seeded way first (mirrors the incremental ingest hook).
            recompute_heat_agg_for_ways(db, [self._OSM_WAY_ID])
            db.commit()
            build_pmtiles.export_geojson(
                db, out_path, ['user_count', 'sport', 'heat_score'],
                min_uc=1, **kw,
            )
        finally:
            db.close()
        features = []
        with open(out_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    features.append(json.loads(line))
        return features

    def _features_in_bbox(self, features: list[dict]) -> list[dict]:
        """Keep only features that fall inside the Iceland test bbox.

        Handles both LineString (``[[lon,lat], …]``) and MultiLineString
        (``[[[lon,lat], …], …]``) — the latter is what ST_LineMerge
        emits when 2-point heat_edge segments don't all share endpoints
        exactly (5-dp snap noise).
        """
        keep = []
        for feat in features:
            coords = feat["geometry"]["coordinates"]
            if not coords:
                continue
            # Normalise to a flat list of [lon, lat] pairs. MultiLineString
            # nests one level deeper: ``first[0]`` is itself a list.
            first = coords[0]
            first_pt = first[0] if isinstance(first[0], list) else first
            lon, lat = first_pt[0], first_pt[1]
            if -18.5 <= lon <= -17.5 and 65.5 <= lat <= 66.0:
                keep.append(feat)
        return keep

    def test_three_heat_edges_on_one_osm_way_emit_one_feature(self):
        """Three heat_edges along one OSM way collapse to ONE feature.

        This is the non-regression guard for the "continuous heatmap"
        property. Pre-fix, three heat_edges along the same OSM way
        produced three overlapping 2-point segments, rendering as
        criss-crossing zigzags at z16+.
        """
        self._insert_osm_way_with_5_points()
        # Three heat_edges along the same OSM way, slightly different snaps
        # (typical of three different GPS traces on the same road).
        self._insert_heat_edge(-18.100, 65.700, -18.099, 65.7005,
                               user_count=3, osm_way_id=self._OSM_WAY_ID)
        self._insert_heat_edge(-18.099, 65.7005, -18.098, 65.7008,
                               user_count=2, osm_way_id=self._OSM_WAY_ID)
        self._insert_heat_edge(-18.098, 65.7008, -18.097, 65.7012,
                               user_count=4, osm_way_id=self._OSM_WAY_ID)

        bbox_features = self._features_in_bbox(self._run_export())
        assert len(bbox_features) == 1, (
            f"3 heat_edges along 1 OSM way must collapse to 1 feature, "
            f"got {len(bbox_features)}"
        )

    def test_emitted_geometry_uses_full_osm_way_curve(self):
        """The single feature's geometry has the OSM way's 5 points,
        not the heat_edge's 2-point grid snap.

        Counts points across the geometry, regardless of whether it's a
        single LineString or a MultiLineString (ST_LineMerge sometimes
        emits the latter even for 1-row inputs after the GROUP BY).
        """
        self._insert_osm_way_with_5_points()
        self._insert_heat_edge(-18.100, 65.700, -18.099, 65.7005,
                               user_count=3, osm_way_id=self._OSM_WAY_ID)

        bbox_features = self._features_in_bbox(self._run_export())
        assert len(bbox_features) == 1
        coords = bbox_features[0]["geometry"]["coordinates"]
        first = coords[0]
        # MultiLineString sums points across sub-lines; LineString is flat.
        n_pts = sum(len(sub) for sub in coords) if isinstance(first[0], list) else len(coords)
        # Full OSM way has 5 points; if export drops to heat_edges.geometry
        # (the 2-point snap), we'd see 2 points total.
        assert n_pts == 5, (
            f"emitted geometry must use OSM way's 5-point curve, "
            f"got {n_pts} points"
        )

    def test_solo_grid_fallback_kept_by_default(self, monkeypatch):
        """INTENDED CHANGE 2026-07-20 (desire lines — Paul: "j'ai pas envie
        de perdre les lignes de désir"): a grid-fallback heat_edge
        (osm_way_id NULL) with user_count=1 IS kept by the DEFAULT export
        (env HEATMAP_KEEP_GRID_FALLBACK default true, confirmation floor
        follows min_uc/K = 1). Pre-2026-07-20 this asserted the opposite."""
        monkeypatch.delenv("HEATMAP_KEEP_GRID_FALLBACK", raising=False)
        monkeypatch.delenv("HEATMAP_GRID_FALLBACK_MIN_UC", raising=False)
        self._insert_heat_edge(-18.20, 65.80, -18.1999, 65.8001,
                               user_count=1, osm_way_id=None)

        bbox_features = self._features_in_bbox(self._run_export())
        assert len(bbox_features) == 1, (
            "solo grid-fallback desire line must be KEPT by the default "
            f"export (2026-07-20 decision), got {len(bbox_features)} features"
        )

    def test_confirmed_grid_fallback_dropped_on_drop_path(self):
        """Even confirmed grid-fallback (user_count >= 2) is dropped when
        the ops fallback ``drop_grid_fallback=True`` (env
        HEATMAP_KEEP_GRID_FALLBACK=false / --drop-grid-fallback) is chosen —
        the pre-2026-07-20 Komoot-quality behaviour stays available and
        complete."""
        self._insert_heat_edge(-18.20, 65.80, -18.1999, 65.8001,
                               user_count=2, osm_way_id=None)

        bbox_features = self._features_in_bbox(
            self._run_export(drop_grid_fallback=True))
        assert len(bbox_features) == 0, (
            "drop-grid export must drop grid-fallback even at user_count>=2 "
            "(Komoot-quality ops fallback)"
        )


class TestSharedAggregationContinuousHeatmap:
    """The SHARED by-OSM-way aggregation builder must apply the
    continuous-heatmap invariants. Both display paths (the static PMTiles
    export AND the live ``/heatmap/tiles/{sport}/{z}/{x}/{y}.mvt`` endpoint,
    z11+) call ``build_heat_aggregation_sql`` — so a regression here breaks
    BOTH at once instead of letting them silently drift (which is why the
    triplicated copies were unified in June 2026; the matview was dropped).

    These are cheap source-of-truth string guards on the builder's OUTPUT.
    The real execute-and-assert end-to-end coverage lives in
    ``test_heat_aggregation_builder.py`` (group-by-way, MAX user_count,
    way_geometry replaces 2-pt, solo grid-fallback dropped) and
    ``test_pmtiles_kanon_export.py`` (K-anon gate).
    """

    def _aggregation_sql(self) -> str:
        from app.services.heat_aggregation import build_heat_aggregation_sql
        return build_heat_aggregation_sql()

    def test_groups_by_osm_way_for_continuity(self):
        """OSM-matched edges must collapse to one row per (way, sport)."""
        sql = self._aggregation_sql()
        assert "GROUP BY osm_way_id, sport" in sql, (
            "shared builder must aggregate by (osm_way_id, sport) for "
            "continuous lines"
        )

    def test_joins_osm_road_edges_for_smooth_curve(self):
        """OSM way's full multi-point geometry must replace the 2-point snap."""
        sql = self._aggregation_sql()
        assert "osm_road_edges" in sql
        # The full-way polyline lives in the osm_ways side-table (0056),
        # joined by PK; COALESCE makes it win over the heat_edges 2-point snap.
        assert "LEFT JOIN osm_ways" in sql
        assert "COALESCE(w.way_geometry" in sql

    def test_drops_solo_grid_fallback(self):
        """Solo grid-fallback (osm_way_id NULL, user_count = 1) must be
        dropped — the grid-fallback confirmation filter requires >= 2."""
        sql = self._aggregation_sql()
        # Default grid_fallback_min_uc=2 → the `heat` CTE drops uc<2 grid edges.
        assert "he.osm_way_id IS NULL AND he.user_count < 2" in sql, (
            "shared builder must filter solo grid-fallback "
            "(osm_way_id NULL AND user_count < 2)"
        )

    def test_live_and_pmtiles_share_one_builder(self):
        """Both display paths draw from the SAME aggregation SSOT
        (``app.services.heat_aggregation``) — guards against a future fork
        that reintroduces a divergent copy.

        Since the ``heat_edges_agg`` incremental-aggregate refactor
        (migration 0057) the READ side goes through ``build_agg_read_sql``
        (reads the pre-materialised table) while the agg table is WRITTEN by
        ``build_heat_aggregation_sql`` (backfill + per-way recompute). Both
        functions live in the one module, so the aggregation definition is
        still single-source; this guard checks both display paths import it."""
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        heatmap_src = (root / "app" / "api" / "heatmap.py").read_text()
        pmtiles_src = (root / "app" / "jobs" / "build_pmtiles.py").read_text()
        assert "from app.services.heat_aggregation import" in heatmap_src, (
            "live MVT endpoint must import from the shared aggregation module"
        )
        assert "from app.services.heat_aggregation import" in pmtiles_src, (
            "PMTiles export must import from the shared aggregation module"
        )
        # The read side of both now serves from heat_edges_agg.
        assert "build_agg_read_sql" in heatmap_src
        assert "build_agg_read_sql" in pmtiles_src


class TestSpaghettiFilterIngest:
    """_update_heat_edges drops grid-fallback edges longer than 60m."""

    def _geojson(self, coords: list) -> str:
        return json.dumps({"type": "LineString", "coordinates": coords})

    def _count_test_edges(self, only_long_grid: bool = False) -> int:
        clauses = [f"ST_Intersects(geometry, {_TEST_BBOX})"]
        if only_long_grid:
            clauses.append("osm_way_id IS NULL")
            clauses.append("ST_Length(geometry::geography) > 60")
        sql = "SELECT COUNT(*) FROM heat_edges WHERE " + " AND ".join(clauses)
        db = SessionLocal()
        try:
            return int(db.execute(sa_text(sql)).scalar() or 0)
        finally:
            db.close()

    def test_normal_short_edge_is_kept(self):
        # ~5m apart in Iceland — well below the 60m grid-fallback cap.
        coords = [[-18.10000, 65.70000], [-18.099945, 65.70000]]
        _update_heat_edges("u1", _SPORT, self._geojson(coords))
        assert self._count_test_edges() >= 1, "short trace must produce an edge"

    def test_gps_jump_does_not_create_long_grid_edge(self):
        # Two points ~1.4km apart simulate a GPS dropout. The densifier's
        # >500m guard appends p2 directly without interpolation, which
        # used to produce one multi-km grid-fallback edge — pure spaghetti.
        # After the fix, ingest must skip edges > 60m.
        coords = [[-18.10000, 65.70000], [-18.07000, 65.70000]]
        _update_heat_edges("u1", _SPORT, self._geojson(coords))
        assert self._count_test_edges(only_long_grid=True) == 0


class TestOffroadPartition:
    """sport='offroad' lands in its own partition, not rewritten to gravel."""

    def test_normalizer_keeps_offroad(self):
        from app.services.ingest import _normalize_heat_edge_sport
        assert _normalize_heat_edge_sport("offroad") == "offroad"

    def test_normalizer_keeps_real_sports(self):
        from app.services.ingest import _normalize_heat_edge_sport
        for s in ("road", "gravel", "mtb", "offroad", "running"):
            assert _normalize_heat_edge_sport(s) == s

    def test_normalizer_strips_test_prefix(self):
        from app.services.ingest import _normalize_heat_edge_sport
        assert _normalize_heat_edge_sport("test_anything") == "gravel"
        assert _normalize_heat_edge_sport("cache_test_x") == "gravel"

    def test_offroad_partition_exists(self):
        db = SessionLocal()
        try:
            row = db.execute(sa_text(
                "SELECT 1 FROM pg_class WHERE relname = 'heat_edges_offroad'"
            )).fetchone()
            assert row is not None, "heat_edges_offroad partition must exist (migration 0035)"
        finally:
            db.close()


class TestOsmMatchProjectsToOsmGeometry:
    """OSM-matched edges align to the OSM way, not the noisy GPS coords.

    Regression for the May 2026 spaghetti screenshot: two riders walking
    the same OSM-tagged trail with slight perpendicular GPS noise used
    to produce parallel offset 2-point edges that never merged. Visual
    result was a "feather" of fan-shaped segments around the trail. The
    fix in `_match_to_osm` projects each GPS point onto its matched OSM
    segment via `_project_onto_segment` so multiple riders' edges
    collapse to identical positions.

    Tested as a unit on `_project_onto_segment` plus an integration on
    `_match_to_osm` with a hand-built segment grid (no DB round-trip,
    no matview refresh — the segment cache + osm tile loader make the
    full ingest path too brittle for a regression test).
    """

    def test_project_onto_segment_collapses_perpendicular_noise(self):
        """Two points 3 m on opposite sides of a horizontal OSM segment
        project to the *same* on-segment position (within numerical
        precision). This is the property that makes the heat_edges
        merge instead of fanning out."""
        from app.services.ingest import _project_onto_segment

        # Horizontal OSM segment at lat=65.70000 from lon=-18.10 to -18.099.
        ax, ay, bx, by = -18.10000, 65.70000, -18.09900, 65.70000
        # Same lon, jittered lat (~3m perpendicular each direction)
        north_lon, north_lat = -18.09950, 65.70003
        south_lon, south_lat = -18.09950, 65.69997
        n_proj = _project_onto_segment(north_lon, north_lat, ax, ay, bx, by)
        s_proj = _project_onto_segment(south_lon, south_lat, ax, ay, bx, by)
        # Both must collapse to the same point on the OSM line
        assert abs(n_proj[0] - s_proj[0]) < 1e-9
        assert abs(n_proj[1] - s_proj[1]) < 1e-9
        # And the projection must be ON the segment line (lat=65.70000)
        assert abs(n_proj[1] - 65.70000) < 1e-9

    def test_project_onto_segment_clamps_to_endpoints(self):
        """A point past the end of the segment clamps to the endpoint —
        matches PostGIS ST_ClosestPoint behavior."""
        from app.services.ingest import _project_onto_segment

        ax, ay, bx, by = -18.10000, 65.70000, -18.09900, 65.70000
        # Way past the east end (bx)
        proj = _project_onto_segment(-18.05, 65.70000, ax, ay, bx, by)
        assert abs(proj[0] - bx) < 1e-9
        assert abs(proj[1] - by) < 1e-9
        # Way past the west end (ax)
        proj = _project_onto_segment(-18.20, 65.70000, ax, ay, bx, by)
        assert abs(proj[0] - ax) < 1e-9
        assert abs(proj[1] - ay) < 1e-9

    def test_snap_along_segment_collapses_sub_metre_along_track_offset(self):
        """Two on-segment points <1m apart ALONG the segment direction
        collapse to the same arc-length bucket → identical (lon, lat).

        This is the property the deleted 5dp `_snap_fine` did NOT have
        (its lon/lat-axis rounding ignored segment orientation, so two
        points 0.5m apart along a 45° segment could still snap to
        different cells AND get pushed off the polyline). Arc-length
        bucketing respects orientation AND keeps the point ON the line.

        Uses France coords (Clapiers ~43.65°) so the latitude-cosine
        baked into `_DEG_TO_M * _COS_LAT_FRANCE` matches reality.

        Points are placed mid-bucket (s ≈ 23.6m and ≈ 23.9m) so they
        don't straddle a bucket boundary — see the docstring on
        `_snap_along_segment` re: 1m buckets having 0.5m boundary
        zones. Worst-case "within 1m → different bucket" is still
        possible at boundaries; producer-side dedup is statistical,
        not absolute.
        """
        from app.services.ingest import _snap_along_segment

        # Horizontal segment, lat=43.65° (Clapiers). 2.26e-3 deg lon
        # ≈ 178m at this latitude (≈ 111320 × cos(45°) per
        # `_COS_LAT_FRANCE`).
        ax, ay, bx, by = 3.87800, 43.65000, 3.88026, 43.65000
        # Mid-bucket positions (s ≈ 23.6m and ≈ 23.9m). 0.3m along the
        # trail ≈ 3.73e-6 deg lon.
        pa_lon, pa_lat = 3.87830, 43.65000
        pb_lon, pb_lat = 3.87830 + 3.73e-6, 43.65000
        snap_a = _snap_along_segment(pa_lon, pa_lat, ax, ay, bx, by)
        snap_b = _snap_along_segment(pb_lon, pb_lat, ax, ay, bx, by)
        assert snap_a == snap_b, (
            f"Sub-metre along-track offset should collapse to the same "
            f"bucket; got A={snap_a} B={snap_b}"
        )
        assert abs(snap_a[1] - 43.65000) < 1e-9

    def test_snap_along_segment_distinguishes_separated_buckets(self):
        """Points >>1m apart along the segment land in DIFFERENT buckets.
        The bucketing is a producer-side dedup, NOT a coarsening — it
        must not over-merge unrelated edges.
        """
        from app.services.ingest import _snap_along_segment

        ax, ay, bx, by = 3.87800, 43.65000, 3.88026, 43.65000
        # ~10 m apart along the segment.
        pa = _snap_along_segment(3.87830, 43.65000, ax, ay, bx, by)
        pb = _snap_along_segment(3.87843, 43.65000, ax, ay, bx, by)
        assert pa != pb

    def test_match_to_osm_collapses_cross_rider_along_track_offset(self):
        """End-to-end: two riders with GPS sample offsets along the OSM
        segment direction (parallel offsets, not just perpendicular)
        produce the SAME edge_keys after arc-length bucketing.

        The prior `test_match_to_osm_uses_projection_for_edge_geometry`
        only validated perpendicular noise, which trivially collapses
        under any projection. The harder case — riders sampling at
        slightly different points ALONG the trail — only collapses with
        arc-length bucketing. This is the K-anonymity property fix.
        """
        from app.services.ingest import _match_to_osm, _OsmSegment

        # France-latitude segment (Clapiers ~43.65°). 2.26e-3 deg lon
        # ≈ 182m.
        seg = _OsmSegment(
            lon1=3.87800, lat1=43.65000,
            lon2=3.88026, lat2=43.65000,
            surface="dirt", highway="path",
            osm_way_id=99002, seg_idx=0,
        )
        from app.services import ingest as ingest_mod
        orig_get_cached = ingest_mod._get_cached_tile_segments
        ingest_mod._get_cached_tile_segments = lambda *_args, **_kw: [seg]
        with ingest_mod._osm_grid_cache_lock:
            ingest_mod._osm_grid_cache.clear()
        try:
            # Rider A at offset (~3 m north).
            # Rider B at lon-shifted by 0.3m east + (~3 m south).
            # 0.3 m at lat=43.65° ≈ 3.73e-6 deg lon (sub-metre,
            # below 1m bucket → must collapse).
            rider_a = [
                [3.87830, 43.65003],
                [3.87870, 43.65003],
                [3.87910, 43.65003],
            ]
            rider_b = [
                [3.87830 + 3.73e-6, 43.64997],
                [3.87870 + 3.73e-6, 43.64997],
                [3.87910 + 3.73e-6, 43.64997],
            ]
            edges_a, _ = _match_to_osm(rider_a, "running", db=None)
            edges_b, _ = _match_to_osm(rider_b, "running", db=None)
        finally:
            ingest_mod._get_cached_tile_segments = orig_get_cached
            with ingest_mod._osm_grid_cache_lock:
                ingest_mod._osm_grid_cache.clear()

        assert edges_a and edges_b, "both riders should match OSM"
        keys_a = {e["edge_key"] for e in edges_a}
        keys_b = {e["edge_key"] for e in edges_b}
        # ALL edges from each rider should share keys with the other
        # rider, not just intersect. Producer-side dedup is the whole
        # point of arc-length bucketing.
        assert keys_a == keys_b, (
            f"Cross-rider along-track offset should produce identical "
            f"edge_keys after arc-length bucketing. Got A={keys_a} "
            f"B={keys_b} (symmetric diff = {keys_a ^ keys_b})"
        )

    def test_match_to_osm_uses_projection_for_edge_geometry(self):
        """End-to-end on `_match_to_osm` with a hand-built segment grid.
        Two riders' offset GPS samples must produce the *same* edge_key
        (proof that projection collapsed them to the same on-segment
        snap cells).
        """
        from app.services.ingest import _match_to_osm, _OsmSegment

        # OSM segment at lat=65.70000 (horizontal, ~22 m wide).
        seg = _OsmSegment(
            lon1=-18.10000, lat1=65.70000,
            lon2=-18.09980, lat2=65.70000,
            surface="dirt", highway="path",
            osm_way_id=99001, seg_idx=0,
        )

        # Monkey-patch the cached helpers so `_match_to_osm` uses our
        # in-memory grid and doesn't hit the DB.
        from app.services import ingest as ingest_mod
        orig_get_cached = ingest_mod._get_cached_tile_segments
        ingest_mod._get_cached_tile_segments = lambda *_args, **_kw: [seg]
        # Also bypass the per-tile-set grid cache so we use the one we built.
        with ingest_mod._osm_grid_cache_lock:
            ingest_mod._osm_grid_cache.clear()
        try:
            rider_a = [
                [-18.10000, 65.70003],  # ~3m north
                [-18.09994, 65.70003],
                [-18.09988, 65.70003],
            ]
            rider_b = [
                [-18.10000, 65.69997],  # ~3m south
                [-18.09994, 65.69997],
                [-18.09988, 65.69997],
            ]
            edges_a, _ = _match_to_osm(rider_a, "running", db=None)
            edges_b, _ = _match_to_osm(rider_b, "running", db=None)
        finally:
            ingest_mod._get_cached_tile_segments = orig_get_cached
            with ingest_mod._osm_grid_cache_lock:
                ingest_mod._osm_grid_cache.clear()

        assert edges_a, "rider_a should have produced OSM-matched edges"
        assert edges_b, "rider_b should have produced OSM-matched edges"
        # Both riders' edge_keys must overlap — that's the merge property.
        keys_a = {e["edge_key"] for e in edges_a}
        keys_b = {e["edge_key"] for e in edges_b}
        assert keys_a & keys_b, (
            f"Offset riders should share at least one edge_key after "
            f"projection. Got A={keys_a} B={keys_b}"
        )


class TestDensifierTrackBreak:
    """`_densify_coords` two-tier gap handling (see `_DENSIFY_MAX_BRIDGE_M`):

    - small / sampling gaps (≤ 2500 m) are densified across (bridged) so the
      OSM matcher keeps a downsampled-GPX run CONTINUOUS;
    - only > 2500 m jumps (genuine dropout / car transfer) stay a hard break.
    """

    def test_short_gap_no_break(self):
        from app.services.ingest import _densify_coords
        # 100m apart — normal contiguous recording; no None expected.
        coords = [[3.87, 43.61], [3.871, 43.61]]
        out = _densify_coords(coords)
        assert None not in out

    def test_sampling_gap_bridged_not_broken(self):
        from app.services.ingest import _densify_coords
        # ~2.4 km apart — past the old 500 m guard but within the bridge
        # band. This is the sparse-sampling case (downsampled export): it
        # must be BRIDGED (densified across), NOT broken, so the OSM matcher
        # can keep the run continuous. No None sentinel; interpolated points.
        coords = [[3.87, 43.61], [3.90, 43.61]]  # 2415 m
        out = _densify_coords(coords)
        assert None not in out, (
            "sampling-band gap must be bridged, not broken — a downsampled "
            "ride on a real path should stay continuous"
        )
        assert len(out) > 100, "the 2.4 km bridge should produce many ~15 m steps"
        assert out[0] is not None and out[-1] is not None

    def test_genuine_dropout_inserts_break(self):
        from app.services.ingest import _densify_coords
        # ~4 km apart — past the 2500 m bridge cap. Overwhelmingly a real
        # dropout or a transfer between two rides; must insert exactly one
        # None sentinel so no phantom line is painted across it.
        coords = [[3.87, 43.61], [3.92, 43.61]]  # ~4026 m
        out = _densify_coords(coords)
        assert None in out, "densifier must break on a >2500 m dropout gap"
        assert out[0] is not None and out[-1] is not None

    def test_break_separates_two_segments(self):
        from app.services.ingest import _densify_coords
        # Pattern: short / huge dropout / short. Densifier should produce
        # interpolated points for the two short legs and one None sentinel
        # between them (the huge gap is past the bridge cap).
        coords = [
            [3.87, 43.61],
            [3.871, 43.61],   # ~80m
            [3.92, 43.61],    # ~4km gap — break here
            [3.921, 43.61],   # ~80m
        ]
        out = _densify_coords(coords)
        none_count = sum(1 for p in out if p is None)
        assert none_count == 1, f"expected exactly one break, got {none_count}"
