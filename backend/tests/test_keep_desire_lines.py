"""Desire-lines display policy — keep off-OSM grid-fallback edges (2026-07-20).

Paul's product decision: "j'ai pas envie de perdre les lignes de désir" —
grid-fallback heat_edges (``osm_way_id IS NULL``) are REAL riding signal
(MTB singletracks / DFCI paths that don't exist in OSM). The policy is
env-driven and SSOT'd in
``heat_aggregation.resolve_grid_fallback_display`` so the TWO display
readers (static PMTiles export + live MVT fallback) cannot drift:

* ``HEATMAP_KEEP_GRID_FALLBACK`` — default **true** (keep desire lines).
* ``HEATMAP_GRID_FALLBACK_MIN_UC`` — default empty → follow the effective
  ``min_uc``/K (1 in the K=1 beta, so solo singletracks SHOW).
* the 60 m grid length cap stays as-is (GPS-jump noise control).

Two tiers of tests:

1. PURE (no DB — CI always runs them): the env-resolution helper, the SQL
   the builders emit for the resolved params, and the wiring of both
   readers (the REAL ``export_geojson`` driven with a mocked db so the
   produced SQL is inspectable; source-level pin on the live MVT branch).
2. GOLDEN (needs the PostGIS DB, skipif otherwise): real execute-and-assert
   through ``export_geojson`` + the exact live-MVT builder, per the
   "drive the real handler" rule.
"""
import json
import os
import re
import tempfile
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text as sa_text

from app.services.heat_aggregation import (
    build_agg_read_sql,
    build_heat_aggregation_sql,
    resolve_grid_fallback_display,
)

# ─────────────────────────────────────────────────────────────────────────────
# Tier 1 — PURE (no DB)
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveGridFallbackDisplay:
    def test_default_keeps_and_follows_k(self, monkeypatch):
        monkeypatch.delenv("HEATMAP_KEEP_GRID_FALLBACK", raising=False)
        monkeypatch.delenv("HEATMAP_GRID_FALLBACK_MIN_UC", raising=False)
        assert resolve_grid_fallback_display(1) == (False, 1)
        assert resolve_grid_fallback_display(2) == (False, 2)

    @pytest.mark.parametrize("val", ["false", "FALSE", "0", "no", "off"])
    def test_keep_false_drops(self, monkeypatch, val):
        monkeypatch.setenv("HEATMAP_KEEP_GRID_FALLBACK", val)
        drop, _ = resolve_grid_fallback_display(1)
        assert drop is True

    @pytest.mark.parametrize("val", ["true", "TRUE", "1", "yes", ""])
    def test_keep_truthy_or_unrecognised_keeps(self, monkeypatch, val):
        # Anything that is not an explicit falsey token keeps desire lines
        # (fail-open toward Paul's decision).
        monkeypatch.setenv("HEATMAP_KEEP_GRID_FALLBACK", val)
        drop, _ = resolve_grid_fallback_display(1)
        assert drop is False

    def test_min_uc_env_override(self, monkeypatch):
        monkeypatch.setenv("HEATMAP_GRID_FALLBACK_MIN_UC", "3")
        assert resolve_grid_fallback_display(1) == (False, 3)

    def test_empty_min_uc_follows_k(self, monkeypatch):
        # The deploy script ships HEATMAP_GRID_FALLBACK_MIN_UC: "" — empty
        # MUST mean "follow K", not crash on int("").
        monkeypatch.setenv("HEATMAP_GRID_FALLBACK_MIN_UC", "")
        assert resolve_grid_fallback_display(2) == (False, 2)


class TestBuilderSqlStrings:
    """Pin the SQL the two builders emit for the resolved policy params."""

    def test_agg_read_keeps_grid_at_min_uc_1(self):
        sql = build_agg_read_sql(
            min_uc=1, grid_fallback_min_uc=1,
            max_grid_fallback_m=60.0, drop_grid_fallback=False,
        )
        assert "grid_fallback" in sql, "grid-fallback CTE missing when kept"
        assert "he.osm_way_id IS NULL" in sql
        # the confirmation floor is the RESOLVED 1, not a hardcoded 2
        assert re.search(r"he\.user_count >= 1\b", sql)
        assert not re.search(r"he\.user_count >= 2\b", sql)
        # the 60 m length cap survives (noise control stays)
        assert "> 60.0" in sql

    def test_agg_read_drop_has_zero_heat_edges_scan(self):
        sql = build_agg_read_sql(min_uc=1, drop_grid_fallback=True)
        assert "FROM heat_edges he" not in sql.replace("heat_edges_agg", "")
        assert "grid_fallback" not in sql

    def test_live_builder_grid_confirmation_floor_is_resolved(self):
        sql = build_heat_aggregation_sql(
            min_uc=1, grid_fallback_min_uc=1,
            max_grid_fallback_m=60.0, drop_grid_fallback=False,
        )
        assert "AND NOT (he.osm_way_id IS NULL AND he.user_count < 1)" in sql
        assert "> 60.0" in sql


class TestReadersWiring:
    """CI-runnable wiring pins — the REAL export path with a mocked db, and a
    source-level pin that the live MVT branch resolves via the SSOT helper
    (no hardcoded grid_fallback_min_uc=2 / drop_grid_fallback=False left)."""

    def _export_sql(self, monkeypatch, **env) -> str:
        for k in ("HEATMAP_KEEP_GRID_FALLBACK", "HEATMAP_GRID_FALLBACK_MIN_UC"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        from app.jobs.build_pmtiles import export_geojson

        db = MagicMock()
        db.execute.return_value = []
        fd, path = tempfile.mkstemp(suffix=".geojson")
        os.close(fd)
        try:
            export_geojson(db, path, props=["user_count", "sport"], min_uc=1)
        finally:
            os.remove(path)
        (stmt,), _ = db.execute.call_args
        return stmt.text

    def test_export_defaults_keep_desire_lines_at_k(self, monkeypatch):
        sql = self._export_sql(monkeypatch)
        assert "grid_fallback" in sql, (
            "export_geojson default must KEEP grid-fallback desire lines "
            "(HEATMAP_KEEP_GRID_FALLBACK default true — Paul 2026-07-20)"
        )
        assert re.search(r"he\.user_count >= 1\b", sql), (
            "grid confirmation floor must follow min_uc/K (1), not a hardcoded 2"
        )
        assert not re.search(r"he\.user_count >= 2\b", sql), (
            "a hardcoded uc>=2 floor leaked into the export SQL — solo desire "
            "lines would be hidden in the K=1 beta"
        )

    def test_export_env_false_drops_desire_lines(self, monkeypatch):
        sql = self._export_sql(monkeypatch, HEATMAP_KEEP_GRID_FALLBACK="false")
        assert "grid_fallback" not in sql

    def test_live_mvt_branch_uses_ssot_helper(self):
        import inspect

        import app.api.heatmap as heatmap_mod

        src = inspect.getsource(heatmap_mod._generate_tile)
        assert "resolve_grid_fallback_display" in src, (
            "the live MVT z11+ branch must resolve the desire-lines policy "
            "via the SSOT helper (zero-drift doctrine)"
        )
        assert "grid_fallback_min_uc=2" not in src, (
            "hardcoded grid_fallback_min_uc=2 reintroduced in the live MVT — "
            "this hides single-user desire lines in the K=1 beta and drifts "
            "from the PMTiles export"
        )
        assert "drop_grid_fallback=False" not in src, (
            "hardcoded drop_grid_fallback reintroduced in the live MVT — "
            "must come from resolve_grid_fallback_display"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2 — GOLDEN (real DB)
# ─────────────────────────────────────────────────────────────────────────────


def _db_available() -> bool:
    try:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            db.execute(sa_text("SELECT 1 FROM heat_edges LIMIT 1"))
            return True
        finally:
            db.close()
    except Exception:
        return False


needs_db = pytest.mark.skipif(
    not _db_available(), reason="needs the PostGIS DB; CI runner without it skips."
)

# ~16 m (under the 60 m grid cap) / ~161 m (over it), same shapes as
# test_pmtiles_kanon_export.
_SHORT = "LINESTRING(3.8000 43.6000, 3.8002 43.6000)"
_LONG = "LINESTRING(3.8000 43.6000, 3.8020 43.6000)"


def _seed_edge(db, sport: str, key: str, way, uc: int, wkt: str) -> None:
    db.execute(sa_text(
        "INSERT INTO heat_edges (edge_key, sport, geometry, osm_way_id, "
        "user_count, pass_count, forward_count, backward_count, match_source) "
        "VALUES (:k, :s, ST_GeomFromText(:g,4326), :w, :uc, :uc, 0, 0, :ms)"
    ), {"k": f"{sport}-{key}", "s": sport, "g": wkt, "w": way, "uc": uc,
        "ms": "spatial" if way else "grid_fallback"})


def _run_export(db, sport: str, min_uc: int) -> list[dict]:
    """Run the REAL export_geojson with env-resolved defaults; return this
    sport's features as (user_count, highway_type) dicts."""
    from app.jobs.build_pmtiles import export_geojson

    fd, path = tempfile.mkstemp(suffix=".geojson")
    os.close(fd)
    try:
        export_geojson(db, path,
                       props=["user_count", "sport", "highway_type"],
                       min_uc=min_uc)
        out = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                feat = json.loads(line)
                if feat["properties"].get("sport") == sport:
                    out.append(feat["properties"])
        return out
    finally:
        os.remove(path)


@needs_db
def test_solo_desire_line_survives_export_with_defaults(monkeypatch):
    """(a) KEEP=true (default) + min_uc=1 → a single-user NULL-way short edge
    SURVIVES the static PMTiles export. This is Paul's headline case: his solo
    MTB singletrack must show on the K=1 beta heatmap.
    (c) the >60 m grid edge is still dropped (noise control unchanged)."""
    from app.db.session import SessionLocal

    monkeypatch.delenv("HEATMAP_KEEP_GRID_FALLBACK", raising=False)
    monkeypatch.delenv("HEATMAP_GRID_FALLBACK_MIN_UC", raising=False)
    sport = f"_dl_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        _seed_edge(db, sport, "grid-solo", None, 1, _SHORT)
        _seed_edge(db, sport, "grid-long", None, 1, _LONG)
        db.commit()

        feats = _run_export(db, sport, min_uc=1)
        ucs = sorted(f["user_count"] for f in feats)
        assert ucs == [1], (
            f"expected exactly the short solo desire line (uc=1), got {feats}. "
            "Empty → desire lines lost (Paul's decision violated); "
            "two rows → the 60 m grid length cap regressed."
        )
    finally:
        clean = SessionLocal()
        try:
            clean.execute(sa_text("DELETE FROM heat_edges WHERE sport = :s"), {"s": sport})
            clean.commit()
        finally:
            clean.close()
        db.close()


@needs_db
def test_keep_false_drops_desire_line(monkeypatch):
    """(b) KEEP=false → the same solo desire line is DROPPED (reversible
    ops switch back to the Komoot-quality behaviour)."""
    from app.db.session import SessionLocal

    monkeypatch.setenv("HEATMAP_KEEP_GRID_FALLBACK", "false")
    monkeypatch.delenv("HEATMAP_GRID_FALLBACK_MIN_UC", raising=False)
    sport = f"_dl_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        _seed_edge(db, sport, "grid-solo", None, 1, _SHORT)
        db.commit()

        feats = _run_export(db, sport, min_uc=1)
        assert feats == [], (
            f"with HEATMAP_KEEP_GRID_FALLBACK=false grid-fallback edges must "
            f"be excluded from the export, got {feats}"
        )
    finally:
        clean = SessionLocal()
        try:
            clean.execute(sa_text("DELETE FROM heat_edges WHERE sport = :s"), {"s": sport})
            clean.commit()
        finally:
            clean.close()
        db.close()


@needs_db
def test_live_mvt_and_pmtiles_agree_on_grid_fallback(monkeypatch):
    """(d) Zero-drift doctrine: given the same envs, the live-MVT builder
    (``build_agg_read_sql`` with the resolved policy, as ``_generate_tile``
    z11+ all-time uses) and the PMTiles export produce IDENTICAL
    grid-fallback survivor sets."""
    from app.db.session import SessionLocal

    monkeypatch.delenv("HEATMAP_KEEP_GRID_FALLBACK", raising=False)
    monkeypatch.delenv("HEATMAP_GRID_FALLBACK_MIN_UC", raising=False)
    sport = f"_dl_{uuid.uuid4().hex[:8]}"
    k = 1
    db = SessionLocal()
    try:
        _seed_edge(db, sport, "grid-solo", None, 1, _SHORT)
        _seed_edge(db, sport, "grid-conf", None, 2, _SHORT)
        _seed_edge(db, sport, "grid-long", None, 3, _LONG)
        db.commit()

        # PMTiles side: real export, env-resolved defaults.
        pm_grid = sorted(
            f["user_count"] for f in _run_export(db, sport, min_uc=k)
            if f["highway_type"] == "unknown"
        )

        # Live-MVT side: EXACTLY what _generate_tile (z11+, all-time) builds.
        drop, grid_min_uc = resolve_grid_fallback_display(k)
        cte = build_agg_read_sql(
            min_uc=k,
            bbox_predicate=("he.geometry && ST_MakeEnvelope("
                            ":lon_min, :lat_min, :lon_max, :lat_max, 4326)"),
            extra_predicate="he.sport = ANY(:sports)",
            grid_fallback_min_uc=grid_min_uc,
            max_grid_fallback_m=60.0,
            drop_grid_fallback=drop,
        )
        mvt_grid = sorted(
            r[0] for r in db.execute(sa_text(
                f"{cte} SELECT user_count FROM combined "
                f"WHERE osm_way_id IS NULL"
            ), {"lon_min": 3.79, "lat_min": 43.59, "lon_max": 3.81,
                "lat_max": 43.61, "sports": [sport]})
        )

        assert pm_grid == mvt_grid == [1, 2], (
            f"display paths drifted: pmtiles={pm_grid} mvt={mvt_grid} — both "
            "must keep the two short desire lines (uc 1 and 2) and drop the "
            ">60 m one, given the same envs"
        )
    finally:
        clean = SessionLocal()
        try:
            clean.execute(sa_text("DELETE FROM heat_edges WHERE sport = :s"), {"s": sport})
            clean.commit()
        finally:
            clean.close()
        db.close()
