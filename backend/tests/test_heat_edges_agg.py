"""Real execute-and-assert tests for the incremental ``heat_edges_agg`` table.

``heat_edges_agg`` pre-materialises the by-``(osm_way_id, sport)`` display
aggregation so the PMTiles build + live MVT fallback read an indexed table
instead of re-running the ~5 M-row GROUP BY (which OOMed the whole-world
build on db-f1-micro). Migration 0057. It is maintained INCREMENTALLY —
per-activity recompute-from-source — NOT the full-refresh matview 0055 dropped.

The load-bearing guarantee is PARITY: whatever ends up in ``heat_edges_agg``
(via the incremental per-way recompute OR the full backfill) MUST equal,
row-for-row (keys, MAX counts, geometry), the output of the LIVE shared
builder ``build_heat_aggregation_sql`` over the same ``heat_edges``. That is
the anti-drift contract — if they ever diverge, the map is wrong.

Every test drives the REAL handlers (``recompute_heat_agg_for_ways``,
``backfill_heat_agg``, ``build_agg_read_sql``, ``verify_heat_agg``), never an
inline copy. Isolation: a UNIQUE throwaway sport (routed to the
``heat_edges_default`` LIST partition) + synthetic osm_way_ids; a ``finally``
wipes everything. golden-marked → needs PostGIS.
"""
import uuid

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.jobs.rebuild_heat_agg import (
    backfill_heat_agg,
    recompute_heat_agg_for_ways,
)
from app.jobs.verify_heat_agg import verify_heat_agg
from app.services.heat_aggregation import (
    build_agg_read_sql,
    build_heat_aggregation_sql,
)


def _db_available() -> bool:
    try:
        db = SessionLocal()
        try:
            db.execute(sa_text("SELECT 1 FROM heat_edges_agg LIMIT 1"))
            return True
        finally:
            db.close()
    except Exception:
        return False


pytestmark = [
    pytest.mark.golden,
    pytest.mark.skipif(
        not _db_available(),
        reason="needs the PostGIS DB with migration 0057; CI runner without it skips.",
    ),
]

_WAY_BASE = 9_999_100_000


def _seed_way(db, way_id: int, way_wkt: str, highway: str = "tertiary") -> None:
    """Seed the OSM substrate (osm_road_edges segment + osm_ways polyline)."""
    first_seg = "LINESTRING(3.8000 43.6000, 3.8010 43.6000)"
    db.execute(sa_text(
        "INSERT INTO osm_road_edges (tile_key, osm_way_id, segment_idx, geometry, "
        "highway, surface, bridge_yes, tunnel_yes) VALUES "
        f"(:tk, :w, 0, ST_GeomFromText('{first_seg}',4326), :hw, 'asphalt', false, false)"
    ), {"tk": way_id, "w": way_id, "hw": highway})
    db.execute(sa_text(
        "INSERT INTO osm_ways (osm_way_id, way_geometry) VALUES "
        "(:w, ST_GeomFromText(:wg,4326)) "
        "ON CONFLICT (osm_way_id) DO UPDATE SET way_geometry = EXCLUDED.way_geometry"
    ), {"w": way_id, "wg": way_wkt})


def _seed_edge(db, sport: str, way_id: int | None, uc: int, pc: int,
               fwd: int, bwd: int, wkt: str, suffix: str) -> None:
    db.execute(sa_text(
        "INSERT INTO heat_edges (edge_key, sport, geometry, osm_way_id, "
        "user_count, pass_count, forward_count, backward_count, match_source) "
        f"VALUES (:k, :s, ST_GeomFromText('{wkt}',4326), :w, :uc, :pc, :fwd, :bwd, :ms)"
    ), {"k": f"{sport}-{suffix}", "s": sport, "w": way_id, "uc": uc, "pc": pc,
        "fwd": fwd, "bwd": bwd, "ms": "spatial" if way_id else "grid_fallback"})


def _read(cte: str, db, params: dict | None = None) -> dict[tuple, dict]:
    """Run a `combined`-producing CTE and return {(osm_way_id, sport): row}."""
    q = (
        f"{cte} SELECT osm_way_id, sport, user_count, pass_count, "
        f"forward_count, backward_count, highway_type, "
        f"ST_AsText(geometry) AS wkt FROM combined WHERE osm_way_id IS NOT NULL"
    )
    out: dict[tuple, dict] = {}
    for r in db.execute(sa_text(q), params or {}).mappings().all():
        out[(r["osm_way_id"], r["sport"])] = dict(r)
    return out


def _live(db, sport: str, min_uc: int = 1) -> dict[tuple, dict]:
    return _read(build_heat_aggregation_sql(
        min_uc=min_uc, extra_predicate=f"he.sport = '{sport}'",
        drop_grid_fallback=True), db)


def _agg(db, sport: str, min_uc: int = 1) -> dict[tuple, dict]:
    return _read(build_agg_read_sql(
        min_uc=min_uc, extra_predicate=f"he.sport = '{sport}'",
        drop_grid_fallback=True), db)


def _cleanup(sport: str, ways: list[int]) -> None:
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM heat_edges WHERE sport = :s"), {"s": sport})
        db.execute(sa_text("DELETE FROM heat_edges_agg WHERE sport = :s"), {"s": sport})
        if ways:
            db.execute(sa_text("DELETE FROM heat_edges_agg WHERE osm_way_id = ANY(:w)"), {"w": ways})
            db.execute(sa_text("DELETE FROM osm_road_edges WHERE osm_way_id = ANY(:w)"), {"w": ways})
            db.execute(sa_text("DELETE FROM osm_ways WHERE osm_way_id = ANY(:w)"), {"w": ways})
        db.commit()
    finally:
        db.close()


# ── PARITY: the anti-drift contract ─────────────────────────────────────────

def test_incremental_recompute_matches_live_row_for_row():
    """After the per-way recompute, reading heat_edges_agg (build_agg_read_sql)
    == the LIVE build_heat_aggregation_sql over the same heat_edges, key-for-key,
    including MAX counts AND geometry."""
    sport = f"_agg_{uuid.uuid4().hex[:8]}"
    ways = [_WAY_BASE + 1, _WAY_BASE + 2]
    db = SessionLocal()
    try:
        _seed_way(db, ways[0], "LINESTRING(3.8000 43.6000, 3.8005 43.6002, 3.8010 43.6000)")
        _seed_way(db, ways[1], "LINESTRING(3.9000 43.7000, 3.9005 43.7002, 3.9010 43.7000)", "primary")
        # way1: 3 sub-edges, distinct counts → MAX(uc)=3, MAX(pc)=9, fwd MAX=3, bwd MAX=2
        _seed_edge(db, sport, ways[0], 1, 4, 1, 0, "LINESTRING(3.8001 43.6001, 3.8003 43.6001)", "a0")
        _seed_edge(db, sport, ways[0], 3, 9, 3, 2, "LINESTRING(3.8003 43.6001, 3.8005 43.6001)", "a1")
        _seed_edge(db, sport, ways[0], 2, 5, 2, 1, "LINESTRING(3.8005 43.6001, 3.8007 43.6001)", "a2")
        # way2: single sub-edge
        _seed_edge(db, sport, ways[1], 4, 12, 4, 0, "LINESTRING(3.9001 43.7001, 3.9004 43.7001)", "b0")
        db.commit()

        recompute_heat_agg_for_ways(db, ways)
        db.commit()

        live = _live(db, sport)
        agg = _agg(db, sport)
        assert agg == live, f"PARITY DRIFT\nagg={agg}\nlive={live}"
        # Sanity on the MAX semantics of the grouped way.
        assert agg[(ways[0], sport)]["user_count"] == 3
        assert agg[(ways[0], sport)]["pass_count"] == 9
        assert agg[(ways[0], sport)]["forward_count"] == 3
        assert agg[(ways[0], sport)]["backward_count"] == 2
        # Geometry is the way's 3-point curve, not a 2-point heat snap.
        assert agg[(ways[0], sport)]["wkt"].count(",") == 2
    finally:
        _cleanup(sport, ways)
        db.close()


def test_backfill_matches_live_and_incremental():
    """The full backfill produces the SAME rows as the per-way recompute
    (both derive from the one SSOT builder)."""
    sport = f"_aggb_{uuid.uuid4().hex[:8]}"
    ways = [_WAY_BASE + 11, _WAY_BASE + 12]
    db = SessionLocal()
    try:
        _seed_way(db, ways[0], "LINESTRING(3.8100 43.6100, 3.8110 43.6100)")
        _seed_way(db, ways[1], "LINESTRING(3.8200 43.6200, 3.8210 43.6200)")
        _seed_edge(db, sport, ways[0], 2, 6, 2, 0, "LINESTRING(3.8101 43.6101, 3.8104 43.6101)", "a")
        _seed_edge(db, sport, ways[1], 5, 5, 3, 2, "LINESTRING(3.8201 43.6201, 3.8204 43.6201)", "b")
        db.commit()

        # Incremental path result.
        recompute_heat_agg_for_ways(db, ways)
        db.commit()
        incremental = _agg(db, sport)

        # Full backfill (whole DB) — must reproduce the same rows for our sport.
        backfill_heat_agg(db, truncate=True)
        after_backfill = _agg(db, sport)

        assert after_backfill == incremental == _live(db, sport)
    finally:
        _cleanup(sport, ways)
        db.close()


def test_full_corpus_parity_via_drift_check():
    """Load-bearing: backfill heat_edges_agg from the WHOLE local heat_edges
    corpus, then the drift check (a fresh live re-aggregation) reports ZERO
    missing / extra / mismatched rows. This is the realistic-corpus parity
    guarantee — not a 2-edge toy."""
    backfill_heat_agg(truncate=True)
    report = verify_heat_agg()
    assert report.clean, (
        f"heat_edges_agg drifted from a fresh live aggregation: "
        f"missing={report.missing} extra={report.extra} "
        f"mismatched={report.mismatched} (agg={report.agg_rows} live={report.live_rows})"
    )
    # agg row count must equal the live OSM-matched group count exactly.
    assert report.agg_rows == report.live_rows


# ── MAX not SUM ──────────────────────────────────────────────────────────────

def test_max_not_sum_across_sub_edges():
    """A way split into N sub-edges from M rides shows pass_count = M (MAX of
    the per-sub-edge counts), NOT N*M (SUM). Each of M rides traverses all N
    sub-edges → every sub-edge has pass_count = M; MAX = M."""
    sport = f"_aggm_{uuid.uuid4().hex[:8]}"
    way = _WAY_BASE + 21
    N, M = 4, 3
    db = SessionLocal()
    try:
        _seed_way(db, way, "LINESTRING(3.8300 43.6300, 3.8340 43.6300)")
        for i in range(N):
            lon0 = 3.8300 + i * 0.0005
            lon1 = 3.8300 + (i + 1) * 0.0005
            _seed_edge(db, sport, way, 1, M, M, 0,
                       f"LINESTRING({lon0:.4f} 43.6301, {lon1:.4f} 43.6301)", f"s{i}")
        db.commit()
        recompute_heat_agg_for_ways(db, [way])
        db.commit()

        row = _agg(db, sport)[(way, sport)]
        assert row["pass_count"] == M, (
            f"pass_count must be MAX={M} (busiest sub-segment), not SUM={N * M}. "
            f"got {row['pass_count']}"
        )
        assert row["forward_count"] == M
    finally:
        _cleanup(sport, [way])
        db.close()


# ── Incremental correctness: touched ways only + overlap ─────────────────────

def test_only_touched_ways_change_and_overlap_updates():
    """First activity touches way A → only A's agg row appears. A second,
    overlapping activity adds passes on A and touches new way B → A is
    recomputed-from-source (reflects both activities) and B appears; nothing
    else changes."""
    sport = f"_aggi_{uuid.uuid4().hex[:8]}"
    a, b = _WAY_BASE + 31, _WAY_BASE + 32
    db = SessionLocal()
    try:
        _seed_way(db, a, "LINESTRING(3.8400 43.6400, 3.8410 43.6400)")
        _seed_way(db, b, "LINESTRING(3.8500 43.6500, 3.8510 43.6500)")

        # Activity 1: one edge on A (uc=1, pc=1).
        _seed_edge(db, sport, a, 1, 1, 1, 0, "LINESTRING(3.8401 43.6401, 3.8404 43.6401)", "a1")
        db.commit()
        recompute_heat_agg_for_ways(db, [a])
        db.commit()
        agg1 = _agg(db, sport)
        assert set(agg1.keys()) == {(a, sport)}
        assert agg1[(a, sport)]["pass_count"] == 1

        # Activity 2: another pass on A (same geom, higher pc) + a new edge on B.
        _seed_edge(db, sport, a, 1, 4, 1, 0, "LINESTRING(3.8404 43.6401, 3.8407 43.6401)", "a2")
        _seed_edge(db, sport, b, 2, 2, 1, 1, "LINESTRING(3.8501 43.6501, 3.8504 43.6501)", "b1")
        db.commit()
        recompute_heat_agg_for_ways(db, [a, b])
        db.commit()
        agg2 = _agg(db, sport)

        assert set(agg2.keys()) == {(a, sport), (b, sport)}
        # A recomputed from source → MAX(pass_count) over its 2 edges = 4.
        assert agg2[(a, sport)]["pass_count"] == 4
        assert agg2[(b, sport)]["pass_count"] == 2
        assert _agg(db, sport) == _live(db, sport)
    finally:
        _cleanup(sport, [a, b])
        db.close()


# ── Staleness / row removal ──────────────────────────────────────────────────

def test_stale_agg_row_removed_when_way_loses_all_edges():
    """The classic incremental-maintenance rot: a way whose heat_edges all
    vanish must have its agg row DELETED by the next recompute-from-source,
    not left lingering."""
    sport = f"_aggs_{uuid.uuid4().hex[:8]}"
    way = _WAY_BASE + 41
    db = SessionLocal()
    try:
        _seed_way(db, way, "LINESTRING(3.8600 43.6600, 3.8610 43.6600)")
        _seed_edge(db, sport, way, 2, 3, 1, 1, "LINESTRING(3.8601 43.6601, 3.8604 43.6601)", "x")
        db.commit()
        recompute_heat_agg_for_ways(db, [way])
        db.commit()
        assert (way, sport) in _agg(db, sport)

        # The way's edges disappear (deleted / re-matched elsewhere).
        db.execute(sa_text("DELETE FROM heat_edges WHERE sport = :s"), {"s": sport})
        db.commit()
        deleted, upserted = recompute_heat_agg_for_ways(db, [way])
        db.commit()

        assert upserted == 0, "no heat_edges left → nothing to re-insert"
        assert deleted >= 1, "the stale agg row must be deleted"
        remaining = db.execute(sa_text(
            "SELECT COUNT(*) FROM heat_edges_agg WHERE osm_way_id = :w"
        ), {"w": way}).scalar()
        assert remaining == 0, "stale agg row lingered after its edges vanished"
    finally:
        _cleanup(sport, [way])
        db.close()


# ── Idempotency ──────────────────────────────────────────────────────────────

def test_recompute_twice_is_idempotent():
    sport = f"_aggd_{uuid.uuid4().hex[:8]}"
    way = _WAY_BASE + 51
    db = SessionLocal()
    try:
        _seed_way(db, way, "LINESTRING(3.8700 43.6700, 3.8710 43.6700)")
        _seed_edge(db, sport, way, 3, 7, 2, 1, "LINESTRING(3.8701 43.6701, 3.8704 43.6701)", "x")
        db.commit()

        recompute_heat_agg_for_ways(db, [way])
        db.commit()
        first = _agg(db, sport)
        recompute_heat_agg_for_ways(db, [way])
        db.commit()
        second = _agg(db, sport)

        assert first == second, "recompute-from-source must be idempotent"
        # Exactly one row (no duplication on re-run).
        n = db.execute(sa_text(
            "SELECT COUNT(*) FROM heat_edges_agg WHERE osm_way_id = :w"
        ), {"w": way}).scalar()
        assert n == 1
    finally:
        _cleanup(sport, [way])
        db.close()


# ── Multi-sport on one way ───────────────────────────────────────────────────

def test_multi_sport_on_one_way_updates_independently():
    """Two sports on the SAME osm_way_id get independent (osm_way_id, sport)
    agg rows; a recompute of the way maintains both."""
    suffix = uuid.uuid4().hex[:8]
    sport_a = f"_aggx_{suffix}"
    sport_b = f"_aggy_{suffix}"
    way = _WAY_BASE + 61
    db = SessionLocal()
    try:
        _seed_way(db, way, "LINESTRING(3.8800 43.6800, 3.8810 43.6800)")
        _seed_edge(db, sport_a, way, 2, 5, 2, 0, "LINESTRING(3.8801 43.6801, 3.8804 43.6801)", "a")
        _seed_edge(db, sport_b, way, 4, 8, 3, 1, "LINESTRING(3.8801 43.6801, 3.8804 43.6801)", "b")
        db.commit()
        recompute_heat_agg_for_ways(db, [way])
        db.commit()

        rows = db.execute(sa_text(
            "SELECT sport, user_count, pass_count FROM heat_edges_agg "
            "WHERE osm_way_id = :w ORDER BY sport"
        ), {"w": way}).mappings().all()
        by_sport = {r["sport"]: dict(r) for r in rows}
        assert set(by_sport) == {sport_a, sport_b}
        assert by_sport[sport_a]["pass_count"] == 5
        assert by_sport[sport_b]["pass_count"] == 8
        assert by_sport[sport_b]["user_count"] == 4
    finally:
        db2 = SessionLocal()
        try:
            db2.execute(sa_text("DELETE FROM heat_edges WHERE sport = ANY(:s)"),
                        {"s": [sport_a, sport_b]})
            db2.execute(sa_text("DELETE FROM heat_edges_agg WHERE osm_way_id = :w"), {"w": way})
            db2.execute(sa_text("DELETE FROM osm_road_edges WHERE osm_way_id = :w"), {"w": way})
            db2.execute(sa_text("DELETE FROM osm_ways WHERE osm_way_id = :w"), {"w": way})
            db2.commit()
        finally:
            db2.close()
        db.close()


# ── K-anonymity read filter (agg stores raw user_count) ──────────────────────

def test_kanon_read_filter_matches_live():
    """The agg stores RAW user_count; the K floor is applied at READ time.
    min_uc=2 hides a user_count=1 way; min_uc=1 shows it — and either way the
    agg read == the live builder at the SAME min_uc."""
    sport = f"_aggk_{uuid.uuid4().hex[:8]}"
    solo, busy = _WAY_BASE + 71, _WAY_BASE + 72
    db = SessionLocal()
    try:
        _seed_way(db, solo, "LINESTRING(3.8900 43.6900, 3.8910 43.6900)")
        _seed_way(db, busy, "LINESTRING(3.9000 43.7000, 3.9010 43.7000)")
        _seed_edge(db, sport, solo, 1, 1, 1, 0, "LINESTRING(3.8901 43.6901, 3.8904 43.6901)", "s")
        _seed_edge(db, sport, busy, 2, 3, 2, 1, "LINESTRING(3.9001 43.7001, 3.9004 43.7001)", "b")
        db.commit()
        recompute_heat_agg_for_ways(db, [solo, busy])
        db.commit()

        # min_uc=1: both ways visible; agg == live.
        assert set(_agg(db, sport, 1)) == {(solo, sport), (busy, sport)}
        assert _agg(db, sport, 1) == _live(db, sport, 1)

        # min_uc=2: the solo (uc=1) way is hidden; agg == live.
        agg2 = _agg(db, sport, 2)
        assert set(agg2) == {(busy, sport)}
        assert (solo, sport) not in agg2
        assert agg2 == _live(db, sport, 2)
    finally:
        _cleanup(sport, [solo, busy])
        db.close()


# ── offroad = gravel + mtb derived from the agg table ────────────────────────

def test_offroad_derived_from_gravel_and_mtb_agg():
    """offroad has no native heat_edges — the display derives it as
    gravel+mtb (+offroad). Reading the agg table with the offroad sport
    expansion returns both a gravel way and an mtb way; agg == live."""
    from app.config import expand_sport

    tag = uuid.uuid4().hex[:8]
    g_way, m_way = _WAY_BASE + 81, _WAY_BASE + 82
    key_g, key_m = f"_offg_{tag}", f"_offm_{tag}"
    db = SessionLocal()
    try:
        _seed_way(db, g_way, "LINESTRING(3.9100 43.7100, 3.9110 43.7100)", "track")
        _seed_way(db, m_way, "LINESTRING(3.9200 43.7200, 3.9210 43.7200)", "path")
        # Real gravel/mtb sports (the offroad derivation is over real sports),
        # tagged with a throwaway edge_key + synthetic ways for cleanup.
        _seed_edge(db, "gravel", g_way, 2, 4, 2, 0, "LINESTRING(3.9101 43.7101, 3.9104 43.7101)", key_g)
        _seed_edge(db, "mtb", m_way, 3, 6, 3, 1, "LINESTRING(3.9201 43.7201, 3.9204 43.7201)", key_m)
        db.commit()
        recompute_heat_agg_for_ways(db, [g_way, m_way])
        db.commit()

        sports = expand_sport("offroad")  # ["mtb", "offroad", "gravel"]
        sport_pred = "he.sport = ANY(:sports)"
        params = {"sports": sports}
        agg = _read(build_agg_read_sql(min_uc=1, extra_predicate=sport_pred,
                                       drop_grid_fallback=True), db, params)
        live = _read(build_heat_aggregation_sql(min_uc=1, extra_predicate=sport_pred,
                                                drop_grid_fallback=True), db, params)
        # Both synthetic ways present (gravel + mtb) under the offroad expansion.
        assert (g_way, "gravel") in agg
        assert (m_way, "mtb") in agg
        # And they match the live derivation for those keys.
        assert agg[(g_way, "gravel")] == live[(g_way, "gravel")]
        assert agg[(m_way, "mtb")] == live[(m_way, "mtb")]
    finally:
        db2 = SessionLocal()
        try:
            db2.execute(sa_text(
                "DELETE FROM heat_edges WHERE edge_key IN (:kg, :km)"),
                {"kg": f"gravel-{key_g}", "km": f"mtb-{key_m}"})
            db2.execute(sa_text("DELETE FROM heat_edges_agg WHERE osm_way_id = ANY(:w)"),
                        {"w": [g_way, m_way]})
            db2.execute(sa_text("DELETE FROM osm_road_edges WHERE osm_way_id = ANY(:w)"),
                        {"w": [g_way, m_way]})
            db2.execute(sa_text("DELETE FROM osm_ways WHERE osm_way_id = ANY(:w)"),
                        {"w": [g_way, m_way]})
            db2.commit()
        finally:
            db2.close()
        db.close()
