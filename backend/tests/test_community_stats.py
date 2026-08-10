"""Real execute-and-assert tests for the homepage community-stats source.

``build_pmtiles.compute_community_stats`` produces the three hero-banner
numbers (contributeurs / traces / km de chemins) at heatmap-BUILD time; they
are published as a static ``stats.json`` (DB-free at request time — the whole
robustness point, since the API is db-f1-micro with min-instances=0). These
tests drive the REAL functions (``compute_community_stats``,
``publish_stats_json``) against the seeded PostGIS DB and assert:

- ``traces`` == ``COUNT(*)`` activities (exact delta from N inserted rows),
- ``contributors`` == ``COUNT(DISTINCT user_id)`` activities (exact delta),
- ``km`` == unique network km from ``heat_edges_agg`` — deduped by
  ``osm_way_id`` (a way mapped under gravel+mtb counts ONCE) and filtered by
  ``user_count >= min_uc``,
- ``publish_stats_json`` writes ``stats.json`` with the documented keys.

Non-destructive on the shared local DB: synthetic user_ids / osm_way_ids and
a ``finally`` that deletes exactly what it inserted — no TRUNCATE (concurrent
sessions rebuild heat_edges; see MEMORY). Needs the PostGIS DB (ST_Length +
the ``heat_edges_agg`` table from migration 0057); skips cleanly if absent.
"""
import json
import uuid

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.jobs.build_pmtiles import compute_community_stats, publish_stats_json


def _db_available() -> bool:
    try:
        db = SessionLocal()
        try:
            db.execute(sa_text("SELECT 1 FROM heat_edges_agg LIMIT 1"))
            db.execute(sa_text("SELECT 1 FROM activities LIMIT 1"))
            return True
        finally:
            db.close()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="needs the PostGIS DB with migration 0057 (heat_edges_agg + activities)",
)

# Synthetic ids well above the real OSM way-id space (~1.5e10) so they can
# never collide with imported ways.
_WAY_BASE = 9_999_200_000


def _insert_agg(db, way_id: int, sport: str, wkt: str, uc: int) -> None:
    db.execute(
        sa_text(
            """
            INSERT INTO heat_edges_agg (osm_way_id, sport, geometry, user_count)
            VALUES (:wid, :sport, ST_GeomFromText(:wkt, 4326), :uc)
            """
        ),
        {"wid": way_id, "sport": sport, "wkt": wkt, "uc": uc},
    )


# A trivial-but-valid LineString so ``geometry_geojson IS NOT NULL`` holds for
# community-eligible rows (the stats queries never parse it — only test NULLity).
_GEOJSON = '{"type":"LineString","coordinates":[[3.0,43.6],[3.01,43.6]]}'


def _insert_activity(
    db,
    act_id: str,
    user_id: str,
    dist_m: float,
    *,
    source: str | None = "manual_upload",
    geometry_geojson: str | None = _GEOJSON,
    contribute_heatmap: bool = True,
) -> None:
    """Insert one activity. Defaults produce a COMMUNITY-ELIGIBLE row
    (manual_upload + geometry + contribute_heatmap); override the kwargs to
    build the personal / non-consented / no-geometry exclusion cases."""
    db.execute(
        sa_text(
            """
            INSERT INTO activities
              (id, user_id, provider, sport, distance_m, source,
               geometry_geojson, contribute_heatmap)
            VALUES (:id, :uid, 'test', 'road', :dist, :src, :geo, :contrib)
            """
        ),
        {
            "id": act_id, "uid": user_id, "dist": dist_m, "src": source,
            "geo": geometry_geojson, "contrib": contribute_heatmap,
        },
    )


def _length_km(db, wkt: str) -> float:
    return float(
        db.execute(
            sa_text("SELECT ST_Length(ST_GeomFromText(:w, 4326)::geography)"),
            {"w": wkt},
        ).scalar()
    ) / 1000.0


def test_traces_and_contributors_exact_delta():
    """traces = COUNT(*) activities; contributors = COUNT(DISTINCT user_id)."""
    db = SessionLocal()
    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    ids = [str(uuid.uuid4()) for _ in range(3)]
    try:
        base = compute_community_stats(db, min_uc=1)
        # 3 activities across 2 distinct, brand-new users.
        _insert_activity(db, ids[0], user_a, 1000.0)
        _insert_activity(db, ids[1], user_a, 2000.0)
        _insert_activity(db, ids[2], user_b, 3000.0)
        db.commit()

        after = compute_community_stats(db, min_uc=1)
        assert after["traces"] == base["traces"] + 3
        assert after["contributors"] == base["contributors"] + 2
        assert isinstance(after["traces"], int)
        assert isinstance(after["contributors"], int)
    finally:
        db.execute(
            sa_text("DELETE FROM activities WHERE id IN (:a, :b, :c)"),
            {"a": ids[0], "b": ids[1], "c": ids[2]},
        )
        db.commit()
        db.close()


def test_only_community_eligible_activities_are_counted():
    """NON-REGRESSION (fix/community-stats-contributors): contributors + traces
    count ONLY community-eligible activities (manual_upload + geometry +
    contribute_heatmap). A strava_api ride, a legacy-NULL-source ride, a
    no-geometry ride, and a non-consented (contribute_heatmap=false) ride must
    NOT inflate either number — they are personal-only and never on the map.

    On the OLD code (``COUNT(DISTINCT user_id) FROM activities`` /
    ``COUNT(*) FROM activities``, no WHERE) this FAILS: it counted all 5 rows /
    5 users. On the fix it counts exactly the ONE eligible row / ONE user.
    """
    db = SessionLocal()
    u_ok = str(uuid.uuid4())        # eligible → counts
    u_strava = str(uuid.uuid4())    # strava_api → excluded
    u_legacy = str(uuid.uuid4())    # source NULL → excluded
    u_nogeo = str(uuid.uuid4())     # no geometry → excluded
    u_noconsent = str(uuid.uuid4())  # contribute_heatmap=false → excluded
    ids = [str(uuid.uuid4()) for _ in range(5)]
    try:
        base = compute_community_stats(db, min_uc=1)

        _insert_activity(db, ids[0], u_ok, 1000.0)  # eligible defaults
        _insert_activity(db, ids[1], u_strava, 2000.0, source="strava_api")
        _insert_activity(db, ids[2], u_legacy, 3000.0, source=None)
        _insert_activity(db, ids[3], u_nogeo, 4000.0, geometry_geojson=None)
        _insert_activity(db, ids[4], u_noconsent, 5000.0, contribute_heatmap=False)
        db.commit()

        after = compute_community_stats(db, min_uc=1)
        # Exactly ONE new eligible trace / ONE new distinct eligible user.
        assert after["traces"] == base["traces"] + 1
        assert after["contributors"] == base["contributors"] + 1
    finally:
        db.execute(
            sa_text("DELETE FROM activities WHERE id IN (:a,:b,:c,:d,:e)"),
            {"a": ids[0], "b": ids[1], "c": ids[2], "d": ids[3], "e": ids[4]},
        )
        db.commit()
        db.close()


def test_km_fallback_excludes_non_community_activities():
    """NON-REGRESSION: the km ACTIVITIES-DISTANCE fallback (taken when
    heat_edges_agg is empty) is gated to community-eligible rows too — a big
    strava_api ride must not inflate 'km de chemins'.

    Skips unless heat_edges_agg is empty at min_uc (otherwise the primary
    network branch is used and there is no fallback to exercise)."""
    db = SessionLocal()
    ids = [str(uuid.uuid4()) for _ in range(2)]
    try:
        # Only meaningful when the network branch yields 0 (fallback path).
        primary = float(db.execute(sa_text(
            "SELECT COALESCE(SUM(ST_Length(geometry::geography)),0) FROM ("
            "  SELECT DISTINCT ON (osm_way_id) geometry FROM heat_edges_agg"
            "  WHERE user_count >= 1 ORDER BY osm_way_id) w"
        )).scalar() or 0.0)
        if primary > 0:
            pytest.skip("heat_edges_agg non-empty → primary network km branch, no fallback")

        base = compute_community_stats(db, min_uc=1)["km"]
        # A 50 km strava_api ride (excluded) + a 10 km eligible ride (counted).
        _insert_activity(db, ids[0], str(uuid.uuid4()), 50_000.0, source="strava_api")
        _insert_activity(db, ids[1], str(uuid.uuid4()), 10_000.0)
        db.commit()

        after = compute_community_stats(db, min_uc=1)["km"]
        # Only the 10 km eligible ride moves the number; the 50 km strava_api
        # ride is excluded. OLD code (SUM over all activities) → +60.
        assert after - base == 10
    finally:
        db.execute(
            sa_text("DELETE FROM activities WHERE id IN (:a, :b)"),
            {"a": ids[0], "b": ids[1]},
        )
        db.commit()
        db.close()


def test_km_network_dedup_and_min_uc_filter():
    """km = unique network km: dedupe by osm_way_id, filter user_count>=min_uc."""
    db = SessionLocal()
    w_sentinel, w1, w2 = _WAY_BASE + 1, _WAY_BASE + 2, _WAY_BASE + 3
    wkt0 = "LINESTRING(3.00 43.70, 3.05 43.70)"   # ~4 km
    wkt1 = "LINESTRING(3.00 43.50, 3.10 43.50)"   # ~8 km
    wkt2 = "LINESTRING(3.00 43.60, 3.20 43.60)"   # ~16 km
    try:
        # Sentinel guarantees heat_edges_agg is non-empty, so both the
        # baseline and post-seed reads use the NETWORK branch (not the
        # activities-distance fallback) — the delta is then attributable
        # purely to the rows we add.
        _insert_agg(db, w_sentinel, "road", wkt0, uc=5)
        db.commit()

        base1 = compute_community_stats(db, min_uc=1)["km"]
        base3 = compute_community_stats(db, min_uc=3)["km"]

        _insert_agg(db, w1, "gravel", wkt1, uc=3)
        _insert_agg(db, w1, "mtb", wkt1, uc=3)   # SAME way, 2nd sport → must dedupe
        _insert_agg(db, w2, "gravel", wkt2, uc=1)
        db.commit()

        a1 = compute_community_stats(db, min_uc=1)["km"]
        a3 = compute_community_stats(db, min_uc=3)["km"]

        len1 = _length_km(db, wkt1)
        len2 = _length_km(db, wkt2)

        # min_uc=1: w1 (deduped → counted ONCE despite 2 sport rows) + w2.
        # Had the dedup failed, this delta would be ~len1+len1+len2 and blow
        # the ±1 rounding tolerance.
        assert abs((a1 - base1) - round(len1 + len2)) <= 1
        # min_uc=3: w1 (uc=3) counted once; w2 (uc=1) filtered out.
        assert abs((a3 - base3) - round(len1)) <= 1
    finally:
        db.execute(
            sa_text("DELETE FROM heat_edges_agg WHERE osm_way_id IN (:a, :b, :c)"),
            {"a": w_sentinel, "b": w1, "c": w2},
        )
        db.commit()
        db.close()


def test_publish_stats_json_writes_expected_keys(tmp_path, monkeypatch):
    """publish_stats_json emits stats.json with the documented key set."""
    monkeypatch.delenv("HEATMAP_GCS_BUCKET", raising=False)  # local-only write
    db = SessionLocal()
    try:
        returned = publish_stats_json(db, str(tmp_path), min_uc=1)

        path = tmp_path / "stats.json"
        assert path.exists(), "publish_stats_json must write a local stats.json"
        data = json.loads(path.read_text())

        for key in (
            "contributors", "traces", "km",
            "license", "license_url", "attribution",
            "generated_at", "min_uc",
        ):
            assert key in data, f"stats.json missing key {key!r}"

        # File content matches the returned dict (same computation).
        assert data["contributors"] == returned["contributors"]
        assert data["traces"] == returned["traces"]
        assert data["km"] == returned["km"]
        assert data["license"] == "ODbL-1.0"
        assert data["min_uc"] == 1
        assert isinstance(data["contributors"], int)
        assert isinstance(data["traces"], int)
        assert isinstance(data["km"], int)
    finally:
        db.close()


# ── RAW-pivot km: heat_edges_agg is DROPPED in prod → the km "de chemins" must
#    come from the lattice network estimate (build path) or the ridden-distance
#    fallback (live endpoint), NEVER from the (gone) agg table. ─────────────────

def test_km_raw_mode_uses_lattice_override_not_agg(monkeypatch):
    """RAW mode: km = the lattice ``network_m_override`` from export_raw_geojson,
    and heat_edges_agg is NOT summed even when it exists locally.

    Fails on the pre-pivot code (which always summed heat_edges_agg and had no
    override param); passes on the fix."""
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")
    db = SessionLocal()
    w = _WAY_BASE + 21
    try:
        # A fat agg row the OLD code would have summed into km (~72 km). In raw
        # mode it must be IGNORED in favour of the override.
        _insert_agg(db, w, "road", "LINESTRING(3.00 43.70, 3.90 43.70)", uc=5)
        db.commit()
        stats = compute_community_stats(db, min_uc=1, network_m_override=1_234_567.0)
        assert stats["km"] == round(1_234_567 / 1000)  # 1235, from the override
    finally:
        db.execute(sa_text("DELETE FROM heat_edges_agg WHERE osm_way_id = :w"), {"w": w})
        db.commit()
        db.close()


def test_km_raw_mode_without_override_uses_ridden_distance(monkeypatch):
    """RAW mode + no override (the live /heatmap/stats fallback path): km drops
    to community-eligible ridden distance, never the heat_edges_agg sum."""
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")
    db = SessionLocal()
    w = _WAY_BASE + 22
    a = str(uuid.uuid4())
    try:
        # ~72 km agg row present — must be ignored in raw mode.
        _insert_agg(db, w, "road", "LINESTRING(3.00 43.70, 3.90 43.70)", uc=5)
        base = compute_community_stats(db, min_uc=1)["km"]
        _insert_activity(db, a, str(uuid.uuid4()), 10_000.0)  # +10 km eligible ridden
        db.commit()
        after = compute_community_stats(db, min_uc=1)["km"]
        # Only the ridden distance moves km; the 72 km agg row is not summed.
        assert after - base == 10
    finally:
        db.execute(sa_text("DELETE FROM activities WHERE id = :a"), {"a": a})
        db.execute(sa_text("DELETE FROM heat_edges_agg WHERE osm_way_id = :w"), {"w": w})
        db.commit()
        db.close()
