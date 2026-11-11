"""Real execute-and-assert tests for the homepage community-stats source.

``build_pmtiles.compute_community_stats`` produces the three hero-banner
numbers (contributeurs / traces / km de chemins) at heatmap-BUILD time; they
are published as a static ``stats.json`` (DB-free at request time — the whole
robustness point, since the API is db-f1-micro with min-instances=0). These
tests drive the REAL functions (``compute_community_stats``,
``publish_stats_json``) against the seeded PostGIS DB and assert:

- ``traces`` == ``COUNT(*)`` activities (exact delta from N inserted rows),
- ``contributors`` == ``COUNT(DISTINCT user_id)`` activities (exact delta),
- ``km`` == the build's lattice ``network_m_override`` when given, else the
  community-eligible ridden distance,
- ``publish_stats_json`` writes ``stats.json`` with the documented keys.

Non-destructive on the shared local DB: synthetic user_ids and a ``finally``
that deletes exactly what it inserted — no TRUNCATE. Needs the PostGIS DB;
skips cleanly if absent.
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
            db.execute(sa_text("SELECT 1 FROM activities LIMIT 1"))
            return True
        finally:
            db.close()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="needs the PostGIS DB (activities)",
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
    dated: bool = True,
) -> None:
    """Insert one activity. Defaults produce a COMMUNITY-ELIGIBLE row
    (manual_upload + geometry + contribute_heatmap + dated); override the
    kwargs to build the personal / non-consented / no-geometry / undated
    exclusion cases."""
    db.execute(
        sa_text(
            """
            INSERT INTO activities
              (id, user_id, provider, sport, distance_m, source,
               geometry_geojson, contribute_heatmap, activity_date)
            VALUES (:id, :uid, 'test', 'road', :dist, :src, :geo, :contrib,
                    CASE WHEN :dated THEN now() ELSE NULL END)
            """
        ),
        {
            "id": act_id, "uid": user_id, "dist": dist_m, "src": source,
            "geo": geometry_geojson, "contrib": contribute_heatmap,
            "dated": dated,
        },
    )


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
    u_undated = str(uuid.uuid4())   # activity_date NULL (planned route) → excluded
    ids = [str(uuid.uuid4()) for _ in range(6)]
    try:
        base = compute_community_stats(db, min_uc=1)

        _insert_activity(db, ids[0], u_ok, 1000.0)  # eligible defaults
        _insert_activity(db, ids[1], u_strava, 2000.0, source="strava_api")
        _insert_activity(db, ids[2], u_legacy, 3000.0, source=None)
        _insert_activity(db, ids[3], u_nogeo, 4000.0, geometry_geojson=None)
        _insert_activity(db, ids[4], u_noconsent, 5000.0, contribute_heatmap=False)
        _insert_activity(db, ids[5], u_undated, 6000.0, dated=False)
        db.commit()

        after = compute_community_stats(db, min_uc=1)
        # Exactly ONE new eligible trace / ONE new distinct eligible user.
        assert after["traces"] == base["traces"] + 1
        assert after["contributors"] == base["contributors"] + 1
    finally:
        db.execute(
            sa_text("DELETE FROM activities WHERE id IN (:a,:b,:c,:d,:e,:f)"),
            {"a": ids[0], "b": ids[1], "c": ids[2], "d": ids[3], "e": ids[4], "f": ids[5]},
        )
        db.commit()
        db.close()


def test_km_fallback_excludes_non_community_activities():
    """NON-REGRESSION: the km ACTIVITIES-DISTANCE fallback (taken without a
    lattice override) is gated to community-eligible rows too — a big
    strava_api ride must not inflate 'km de chemins'."""
    db = SessionLocal()
    ids = [str(uuid.uuid4()) for _ in range(2)]
    try:
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


# ── km "de chemins": the lattice network estimate (build path) or the
#    ridden-distance fallback (live endpoint). ─────────────────────────────────

def test_km_uses_lattice_override():
    """km = the lattice ``network_m_override`` from export_raw_geojson."""
    db = SessionLocal()
    try:
        stats = compute_community_stats(db, min_uc=1, network_m_override=1_234_567.0)
        assert stats["km"] == round(1_234_567 / 1000)  # 1235, from the override
    finally:
        db.close()


def test_km_without_override_uses_ridden_distance():
    """No override (the live /heatmap/summary path): km is the
    community-eligible ridden distance."""
    db = SessionLocal()
    a = str(uuid.uuid4())
    try:
        base = compute_community_stats(db, min_uc=1)["km"]
        _insert_activity(db, a, str(uuid.uuid4()), 10_000.0)  # +10 km eligible ridden
        db.commit()
        after = compute_community_stats(db, min_uc=1)["km"]
        assert after - base == 10
    finally:
        db.execute(sa_text("DELETE FROM activities WHERE id = :a"), {"a": a})
        db.commit()
        db.close()
