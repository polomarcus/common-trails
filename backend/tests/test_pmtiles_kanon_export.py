"""Real execute-and-assert tests for the PMTiles export K-ANONYMITY gate.

This is the FINAL privacy filter before the browser sees heat data —
``build_pmtiles.export_geojson`` applies three filters:
  1. ``user_count >= min_uc``                              (K-anonymity)
  2. drop ``osm_way_id IS NULL`` edges longer than max_grid_fallback_m (anti-spaghetti)
  3. drop ``osm_way_id IS NULL`` edges with ``user_count < grid_fallback_min_uc``

The existing `test_pmtiles_aggregation.py` only greps the SQL SOURCE STRING
(`build_pmtiles_source` fixture) — vacuous by the project's "drive the real
handler" rule: if a filter were silently dropped, the source test for the OTHER
clauses would still pass and PRIVATE single-user data could ship.

These tests EXECUTE the real ``export_geojson`` against seeded heat_edges and
parse the produced GeoJSON features. Isolation: a unique throwaway sport
(routed to the ``heat_edges_default`` LIST partition), restored in ``finally``.

golden-marked → needs PostGIS.
"""
import json
import os
import tempfile
import uuid

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.jobs.build_pmtiles import export_geojson
from app.jobs.rebuild_heat_agg import recompute_heat_agg_for_ways


def _db_available() -> bool:
    try:
        db = SessionLocal()
        try:
            db.execute(sa_text("SELECT 1 FROM heat_edges LIMIT 1"))
            return True
        finally:
            db.close()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="needs the PostGIS DB; CI runner without it skips.")

# ~16 m (short, under the 60 m grid-fallback cap) and ~161 m (long, over it).
_SHORT = "LINESTRING(3.8000 43.6000, 3.8002 43.6000)"
_LONG = "LINESTRING(3.8000 43.6000, 3.8020 43.6000)"


def _seed(db, sport: str):
    rows = [
        # (edge_key, osm_way_id, user_count, wkt)
        # OSM way 801: only a uc=1 edge → MAX < min_uc(2) → way EXCLUDED.
        ("osmA-1", 9_999_000_801, 1, _SHORT),
        # OSM way 802: uc=1 (filtered) + uc=3 → MAX=3 → INCLUDED with uc=3.
        ("osmB-1", 9_999_000_802, 1, _SHORT),
        ("osmB-2", 9_999_000_802, 3, _SHORT),
        # grid-fallback (NULL way): solo uc=1 → EXCLUDED (uc<min_uc).
        ("grid-solo", None, 1, _SHORT),
        # grid-fallback confirmed uc=2 SHORT → kept iff grid-fallback opted in.
        ("grid-conf", None, 2, _SHORT),
        # grid-fallback confirmed uc=2 but LONG (>60 m) → EXCLUDED by length cap.
        ("grid-long", None, 2, _LONG),
    ]
    for key, way, uc, wkt in rows:
        db.execute(sa_text(
            "INSERT INTO heat_edges (edge_key, sport, geometry, osm_way_id, "
            "user_count, pass_count, forward_count, backward_count, match_source) "
            "VALUES (:k, :s, ST_GeomFromText(:g,4326), :w, :uc, :uc, 0, 0, "
            ":ms)"
        ), {"k": f"{sport}-{key}", "s": sport, "g": wkt, "w": way, "uc": uc,
            "ms": "spatial" if way else "grid_fallback"})
    db.commit()
    # export_geojson reads heat_edges_agg for the OSM-matched half (grid rows
    # stay live) — populate the agg rows for the seeded ways so the export
    # sees them (mirrors what the incremental ingest hook does).
    _seed_ways = sorted({w for (_, w, _, _) in rows if w is not None})
    recompute_heat_agg_for_ways(db, _seed_ways)
    db.commit()


def _export_user_counts(db, sport: str, **kw) -> list[int]:
    """Run the REAL export_geojson, return user_counts of features for `sport`."""
    fd, path = tempfile.mkstemp(suffix=".geojson")
    os.close(fd)
    try:
        export_geojson(db, path, props=["user_count", "sport"], min_uc=2, **kw)
        out = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                feat = json.loads(line)
                if feat["properties"].get("sport") == sport:
                    out.append(feat["properties"]["user_count"])
        return sorted(out)
    finally:
        os.remove(path)


def test_export_enforces_k_anonymity_and_grid_filters():
    sport = f"_pmt_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        _seed(db, sport)

        # With grid-fallback opted IN: K-anon + length cap + confirmation all apply.
        ucs = _export_user_counts(db, sport, max_grid_fallback_m=60.0,
                                   grid_fallback_min_uc=2, drop_grid_fallback=False)
        # Survivors: OSM way 802 (uc=3) + the SHORT confirmed grid edge (uc=2).
        # Excluded: way 801 (MAX uc=1 < 2), grid solo (uc=1), grid LONG (length cap).
        assert ucs == [2, 3], (
            f"expected exactly the uc=3 OSM way + the short uc=2 grid edge, got {ucs}. "
            "If a uc=1 leaked → K-anonymity gate broken (PRIVATE data exposed); "
            "if two uc=2 → the grid length cap regressed (spaghetti)."
        )

        # Default Komoot-quality: drop_grid_fallback=True → only OSM ways survive.
        ucs_default = _export_user_counts(db, sport, max_grid_fallback_m=60.0,
                                          grid_fallback_min_uc=2, drop_grid_fallback=True)
        assert ucs_default == [3], (
            f"with drop_grid_fallback=True only the OSM way should remain, got {ucs_default}"
        )
    finally:
        clean = SessionLocal()
        try:
            clean.execute(sa_text("DELETE FROM heat_edges WHERE sport = :s"), {"s": sport})
            clean.execute(sa_text("DELETE FROM heat_edges_agg WHERE sport = :s"), {"s": sport})
            clean.commit()
        finally:
            clean.close()
        db.close()


def test_min_uc_2_excludes_single_user_osm_edge():
    """Direct pin for the June 2026 K-anon-bypass S1.

    The static PMTiles binary (browser + public GCS export) used to be
    built at min_uc=1, publishing single-user OSM-matched edges while the
    API enforced K=2. The rebuild call sites now pass
    HEATMAP_K_ANONYMITY (default 2). This asserts the export itself, at
    min_uc=2, drops an OSM-matched way whose ONLY edge has user_count=1 —
    so even an OSM-matched (osm_way_id IS NOT NULL) edge can't leak a
    single rider below K.
    """
    sport = f"_pmtk_{uuid.uuid4().hex[:8]}"
    osm_way = 9_999_001_777
    db = SessionLocal()
    try:
        # One OSM-matched edge, user_count=1, short (under the length cap).
        db.execute(sa_text(
            "INSERT INTO heat_edges (edge_key, sport, geometry, osm_way_id, "
            "user_count, pass_count, forward_count, backward_count, match_source) "
            "VALUES (:k, :s, ST_GeomFromText(:g,4326), :w, 1, 1, 0, 0, 'spatial')"
        ), {"k": f"{sport}-solo", "s": sport, "g": _SHORT, "w": osm_way})
        db.commit()
        # Populate the agg row (raw uc=1) — the read-time min_uc=2 filter is
        # what must exclude it, NOT the absence of the row.
        recompute_heat_agg_for_ways(db, [osm_way])
        db.commit()

        # Export at the prod K-anonymity floor.
        ucs = _export_user_counts(db, sport, max_grid_fallback_m=60.0,
                                  grid_fallback_min_uc=2, drop_grid_fallback=True)
        assert ucs == [], (
            "min_uc=2 must EXCLUDE a single-user OSM-matched edge; "
            f"got {ucs}. A non-empty result is a K-anonymity bypass — "
            "PRIVATE single-rider data shipped to the public PMTiles binary."
        )
    finally:
        clean = SessionLocal()
        try:
            clean.execute(sa_text("DELETE FROM heat_edges WHERE sport = :s"), {"s": sport})
            clean.execute(sa_text("DELETE FROM heat_edges_agg WHERE sport = :s"), {"s": sport})
            clean.commit()
        finally:
            clean.close()
        db.close()
