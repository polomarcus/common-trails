"""Raw-trace display source (app.services.raw_trace_display) — flag + export.

Pins:
  * ``HEATMAP_DISPLAY_SOURCE`` defaults to matched (prod unchanged on merge).
  * raw export produces non-empty, frontend-shaped LineString features from
    seeded consented activities, with endpoint masking applied.
  * a trace shorter than 2 * mask contributes nothing (fully masked).
"""
from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import text as sa_text

from app.db.models import Activity
from app.db.session import SessionLocal
from app.services.raw_trace_display import (
    _USER_CAP,
    DEFAULT_LATTICE_DEG,
    _add_user,
    _cell_key,
    _in_bbox,
    _reject_far_outliers,
    _sport_id,
    _user_count,
    export_raw_geojson,
    iter_masked_runs,
    lattice_deg,
    max_span_km,
    min_users,
    plausible_bbox,
    raw_display_enabled,
)

LAT = 43.61
STEP = 10.0 / 110_574  # ~10 m
_TEST_USER = "rawproto-test-user"
# Seed in an empty mid-Atlantic longitude so the local Hérault + foreign
# corpus (950 real activities) can't contaminate the assertions — the shared
# export reads ALL consented activities, so we isolate our seed by geography.
_LON_BASE = -40.0
_LON_STRAVA = -42.0   # a strava_api activity → must be EXCLUDED (provenance)
_LON_NULL = -42.2     # legacy source=NULL activity → must be EXCLUDED
_MANUAL = "manual_upload"  # provenance.COMMUNITY_SOURCE


def _read_lines(path: str) -> list[str]:
    with open(path) as fh:
        return [line for line in fh if line.strip()]


def _is_ours(feature: dict) -> bool:
    return all(-40.5 < c[0] < -39.5 for c in feature["geometry"]["coordinates"])


def _line_geojson(n: int, lon: float) -> str:
    coords = [[lon, LAT + i * STEP] for i in range(n)]
    return json.dumps({"type": "LineString", "coordinates": coords})


@pytest.fixture()
def seeded_activities():
    db = SessionLocal()
    db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _TEST_USER})
    db.commit()
    acts = [
        # ~2 km manual_upload line → survives 200 m masking on both ends.
        Activity(user_id=_TEST_USER, provider="file", sport="gravel", source=_MANUAL,
                 geometry_geojson=_line_geojson(200, _LON_BASE), contribute_heatmap=True),
        # SAME user, SAME corridor, 2nd activity → 2 passes but 1 distinct user.
        Activity(user_id=_TEST_USER, provider="file", sport="gravel", source=_MANUAL,
                 geometry_geojson=_line_geojson(200, _LON_BASE), contribute_heatmap=True),
        # ~150 m line → fully masked at 200 m → contributes nothing.
        Activity(user_id=_TEST_USER, provider="file", sport="road", source=_MANUAL,
                 geometry_geojson=_line_geojson(15, _LON_BASE + 0.05), contribute_heatmap=True),
        # not consented → excluded.
        Activity(user_id=_TEST_USER, provider="file", sport="road", source=_MANUAL,
                 geometry_geojson=_line_geojson(200, _LON_BASE + 0.10), contribute_heatmap=False),
        # Strava-API provenance → MUST be excluded from the public ODbL map.
        Activity(user_id=_TEST_USER, provider="strava", sport="gravel", source="strava_api",
                 geometry_geojson=_line_geojson(200, _LON_STRAVA), contribute_heatmap=True),
        # legacy source=NULL → unknown provenance → MUST be excluded.
        Activity(user_id=_TEST_USER, provider="file", sport="gravel", source=None,
                 geometry_geojson=_line_geojson(200, _LON_NULL), contribute_heatmap=True),
    ]
    for a in acts:
        db.add(a)
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _TEST_USER})
    db.commit()
    db.close()


def _patch_build_side_effects(monkeypatch):
    """Neutralise everything build_pmtiles.main does except pick an exporter."""
    import app.jobs.build_pmtiles as bp

    monkeypatch.setattr(bp.shutil, "which", lambda _n: "/usr/bin/tippecanoe")
    monkeypatch.setattr(bp, "run_tippecanoe", lambda *a, **k: None)
    monkeypatch.setattr(bp, "_upload_to_export_bucket", lambda *a, **k: None)
    monkeypatch.setattr(bp, "publish_stats_json", lambda *a, **k: {})
    monkeypatch.setattr(bp, "capture_heatmap_metrics_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(bp, "_emit_heat_quality_metrics", lambda *a, **k: None)
    monkeypatch.setattr(bp.os.path, "getsize", lambda _p: 0)
    monkeypatch.setattr(bp.os, "unlink", lambda _p: None)
    return bp


def test_build_matched_mode_calls_matched_exporter(monkeypatch, tmp_path):
    """Default (flag unset) → build reads heat_edges_agg via export_geojson,
    NEVER the raw exporter. Prod behaviour unchanged on merge."""
    monkeypatch.delenv("HEATMAP_DISPLAY_SOURCE", raising=False)
    bp = _patch_build_side_effects(monkeypatch)
    calls = {"matched": 0, "raw": 0}

    def _fake_matched(db, path, *a, **k):
        calls["matched"] += 1
        open(path, "w").close()
        return 0

    def _fake_raw(db, path, *a, **k):
        calls["raw"] += 1
        open(path, "w").close()
        return 0

    monkeypatch.setattr(bp, "export_geojson", _fake_matched)
    monkeypatch.setattr("app.services.raw_trace_display.export_raw_geojson", _fake_raw)

    bp.main(str(tmp_path), 6, 15, 1)
    assert calls == {"matched": 1, "raw": 0}


def test_build_raw_mode_calls_raw_exporter(monkeypatch, tmp_path):
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")
    bp = _patch_build_side_effects(monkeypatch)
    calls = {"matched": 0, "raw": 0}

    def _fake_matched(db, path, *a, **k):
        calls["matched"] += 1
        open(path, "w").close()
        return 0

    def _fake_raw(db, path, *a, **k):
        calls["raw"] += 1
        open(path, "w").close()
        return 0

    monkeypatch.setattr(bp, "export_geojson", _fake_matched)
    monkeypatch.setattr("app.services.raw_trace_display.export_raw_geojson", _fake_raw)

    bp.main(str(tmp_path), 6, 15, 1)
    assert calls == {"matched": 0, "raw": 1}


_USER_A = "rawproto-user-a"
_USER_B = "rawproto-user-b"
# Distinct empty ocean corridors so the gate assertions can't be polluted.
_LON_SHARED = -41.0   # ridden by BOTH users → 2 distinct users
_LON_SOLO = -41.2     # ridden by user A only → 1 distinct user


def _in(feature: dict, lo: float, hi: float) -> bool:
    return all(lo < c[0] < hi for c in feature["geometry"]["coordinates"])


@pytest.fixture()
def seeded_two_users():
    db = SessionLocal()
    db.execute(sa_text("DELETE FROM activities WHERE user_id IN (:a, :b)"),
               {"a": _USER_A, "b": _USER_B})
    db.commit()
    acts = [
        # Shared corridor — both users ride the SAME ~2 km line → 2 users/cell.
        Activity(user_id=_USER_A, provider="file", sport="gravel", source=_MANUAL,
                 geometry_geojson=_line_geojson(200, _LON_SHARED), contribute_heatmap=True),
        Activity(user_id=_USER_B, provider="file", sport="gravel", source=_MANUAL,
                 geometry_geojson=_line_geojson(200, _LON_SHARED), contribute_heatmap=True),
        # Solo corridor — user A only → 1 user/cell.
        Activity(user_id=_USER_A, provider="file", sport="gravel", source=_MANUAL,
                 geometry_geojson=_line_geojson(200, _LON_SOLO), contribute_heatmap=True),
    ]
    for a in acts:
        db.add(a)
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.execute(sa_text("DELETE FROM activities WHERE user_id IN (:a, :b)"),
               {"a": _USER_A, "b": _USER_B})
    db.commit()
    db.close()


def test_min_users_default_is_one(monkeypatch):
    monkeypatch.delenv("HEATMAP_MIN_USERS", raising=False)
    assert min_users() == 1
    monkeypatch.setenv("HEATMAP_MIN_USERS", "2")
    assert min_users() == 2
    monkeypatch.setenv("HEATMAP_MIN_USERS", "garbage")
    assert min_users() == 1
    monkeypatch.setenv("HEATMAP_MIN_USERS", "0")
    assert min_users() == 1  # floored at 1


def test_gate_min_users_1_keeps_solo(tmp_path, monkeypatch, seeded_two_users):
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    monkeypatch.setenv("HEATMAP_MIN_USERS", "1")
    db = SessionLocal()
    try:
        path = os.path.join(tmp_path, "raw.geojsonl")
        export_raw_geojson(db, path)
    finally:
        db.close()
    feats = [json.loads(line) for line in _read_lines(path)]
    shared = [f for f in feats if _in(f, -41.05, -40.95)]
    solo = [f for f in feats if _in(f, -41.25, -41.15)]
    # Default gate = show everything: both corridors present.
    assert shared, "shared corridor must survive at MIN_USERS=1"
    assert solo, "solo corridor must survive at MIN_USERS=1"


def test_gate_min_users_2_drops_solo_keeps_multi(tmp_path, monkeypatch, seeded_two_users):
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    monkeypatch.setenv("HEATMAP_MIN_USERS", "2")
    db = SessionLocal()
    try:
        path = os.path.join(tmp_path, "raw.geojsonl")
        export_raw_geojson(db, path)
    finally:
        db.close()
    feats = [json.loads(line) for line in _read_lines(path)]
    shared = [f for f in feats if _in(f, -41.05, -40.95)]
    solo = [f for f in feats if _in(f, -41.25, -41.15)]
    # 2 distinct users on the shared corridor → survives.
    assert shared, "a cell with 2 distinct users must survive at MIN_USERS=2"
    # Solo-only corridor (1 user) → fully suppressed.
    assert solo == [], "single-user cells must be dropped at MIN_USERS=2"


def test_masked_endpoints_never_counted_toward_gate(monkeypatch, seeded_two_users):
    """The distinct-user lattice built from iter_masked_runs must have NO entry
    in the masked-endpoint region — home is trimmed before counting."""
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    deg = lattice_deg()
    db = SessionLocal()
    try:
        users: dict = {}
        for _aid, user_id, sport, run in iter_masked_runs(db):
            for pt in run:
                if not (-41.05 < pt[0] < -40.95):  # only the shared corridor
                    continue
                ilat = int(pt[1] // deg)
                ilon = int(pt[0] // deg)
                users.setdefault((sport, ilat, ilon), set()).add(user_id)
    finally:
        db.close()

    assert users, "the masked interior of the shared corridor must be counted"
    true_start_lat = LAT
    true_end_lat = LAT + 199 * STEP
    # 200 m mask ≈ 20 steps; every counted cell sits in the interior, ≥ ~180 m
    # from either true end. Masked (home) points were never counted.
    for (_sport, ilat, _ilon), riders in users.items():
        cell_lat = ilat * deg
        assert cell_lat > true_start_lat + 15 * STEP - deg
        assert cell_lat < true_end_lat - 15 * STEP + deg
        # And the interior really has both users (proves counting works).
        assert riders == {_USER_A, _USER_B}


def test_flag_defaults_to_matched(monkeypatch):
    monkeypatch.delenv("HEATMAP_DISPLAY_SOURCE", raising=False)
    assert raw_display_enabled() is False
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "matched")
    assert raw_display_enabled() is False
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")
    assert raw_display_enabled() is True
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "RAW")
    assert raw_display_enabled() is True


def test_lattice_default_is_memory_bounded_but_still_fine(monkeypatch):
    monkeypatch.delenv("HEATMAP_RAW_LATTICE_DEG", raising=False)
    # Default coarsened ~5 m → ~11 m to bound the accumulator's cell count
    # (~7 M cells at ~5 m OOM'd the prod build). Still far finer than the paint
    # ramp reads, so the render is unchanged; NEVER touches the drawn geometry.
    metres = lattice_deg() * 111_000
    assert 8.0 <= metres <= 12.0
    assert lattice_deg() == DEFAULT_LATTICE_DEG
    # Still env-overridable finer or coarser.
    monkeypatch.setenv("HEATMAP_RAW_LATTICE_DEG", "0.00002")
    assert lattice_deg() == 0.00002


def test_raw_export_non_empty_and_frontend_shaped(tmp_path, monkeypatch, seeded_activities):
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    db = SessionLocal()
    try:
        path = os.path.join(tmp_path, "raw.geojsonl")
        n = export_raw_geojson(db, path)
    finally:
        db.close()

    assert n >= 2
    all_features = [json.loads(line) for line in _read_lines(path)]
    assert len(all_features) == n
    # Every feature (ours or corpus) is frontend-shaped.
    for feat in all_features:
        assert feat["type"] == "Feature"
        assert feat["geometry"]["type"] == "LineString"
        assert len(feat["geometry"]["coordinates"]) >= 2
        props = feat["properties"]
        # Exactly the props community-heatmap-layers.ts / tippecanoe / the /map
        # tooltip read (oneway_score + forward/backward_count added by the
        # directional-heatmap features).
        assert set(props) == {"sport", "user_count", "pass_count",
                              "forward_count", "backward_count", "heat_score",
                              "oneway_score", "highway_type"}
        assert 0.0 <= props["heat_score"] <= 1.0
        assert 0.0 <= props["oneway_score"] <= 1.0
        assert props["user_count"] >= 1
        # Per-direction counts are non-negative; non-mtb/gravel keep them 0.
        assert props["forward_count"] >= 0 and props["backward_count"] >= 0
        if props["sport"] not in ("mtb", "gravel"):
            assert props["forward_count"] == 0 and props["backward_count"] == 0

    # Our geographically-isolated seed: only the two ~2 km gravel lines survive
    # (the ~150 m road line and the unconsented road line contribute nothing).
    ours = [f for f in all_features if _is_ours(f)]
    assert len(ours) == 2
    assert {f["properties"]["sport"] for f in ours} == {"gravel"}


def test_raw_export_masks_endpoints(tmp_path, monkeypatch, seeded_activities):
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    db = SessionLocal()
    try:
        path = os.path.join(tmp_path, "raw.geojsonl")
        export_raw_geojson(db, path)
    finally:
        db.close()
    ours = [json.loads(line) for line in _read_lines(path)]
    ours = [f for f in ours if _is_ours(f)]
    assert ours
    # Every kept vertex is ≥ ~180 m from the seeded lines' true start (lat=LAT)
    # and true end — the home-protection guarantee.
    true_start_lat = LAT
    true_end_lat = LAT + 199 * STEP
    for feat in ours:
        lats = [c[1] for c in feat["geometry"]["coordinates"]]
        # 200 m ≈ 20 steps; the kept run stays away from both extremes.
        assert min(lats) > true_start_lat + 15 * STEP
        assert max(lats) < true_end_lat - 15 * STEP


def test_provenance_excludes_strava_api_and_legacy_null(tmp_path, monkeypatch, seeded_activities):
    """The public raw map MUST NOT publish Strava-API (§5.4/§5.10) or
    unknown-provenance (legacy NULL) traces — only source='manual_upload'."""
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    db = SessionLocal()
    try:
        path = os.path.join(tmp_path, "raw.geojsonl")
        export_raw_geojson(db, path)
    finally:
        db.close()
    feats = [json.loads(line) for line in _read_lines(path)]
    manual = [f for f in feats if _in(f, -40.05, -39.95)]
    strava = [f for f in feats if _in(f, -42.05, -41.95)]
    legacy = [f for f in feats if _in(f, -42.25, -42.15)]
    assert manual, "manual_upload traces must be published"
    assert strava == [], "strava_api traces must be EXCLUDED from the public map"
    assert legacy == [], "legacy source=NULL traces must be EXCLUDED"


def test_user_count_is_distinct_users_not_activities(tmp_path, monkeypatch, seeded_activities):
    """One user with 2 activities over the same corridor → user_count == 1
    (NOT 2), pass_count == 2. False K on a public map would be a leak."""
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    db = SessionLocal()
    try:
        path = os.path.join(tmp_path, "raw.geojsonl")
        export_raw_geojson(db, path)
    finally:
        db.close()
    feats = [json.loads(line) for line in _read_lines(path)]
    ours = [f for f in feats if _is_ours(f)]  # the shared _LON_BASE corridor
    assert ours
    for feat in ours:
        assert feat["properties"]["user_count"] == 1
        assert feat["properties"]["pass_count"] == 2


# ── GPS-glitch outlier rejection (feat/raw-trace-sanity-bbox) ────────────────
# Raw mode draws GPS points VERBATIM, so a corrupt coordinate (a real prod case:
# two gravel rides each had an 8-point mid-Atlantic spike) drew a garbage line
# the matched pipeline rejected implicitly (no OSM match). `_reject_far_outliers`
# is the raw-mode guard: drop points implausibly far from the ride's own median.

def test_max_span_km_env_resolution(monkeypatch):
    monkeypatch.delenv("HEATMAP_RAW_MAX_SPAN_KM", raising=False)
    assert max_span_km() == 300.0
    monkeypatch.setenv("HEATMAP_RAW_MAX_SPAN_KM", "off")
    assert max_span_km() == 0.0          # guard disabled
    monkeypatch.setenv("HEATMAP_RAW_MAX_SPAN_KM", "0")
    assert max_span_km() == 0.0
    monkeypatch.setenv("HEATMAP_RAW_MAX_SPAN_KM", "150")
    assert max_span_km() == 150.0
    monkeypatch.setenv("HEATMAP_RAW_MAX_SPAN_KM", "garbage")
    assert max_span_km() == 300.0        # bad value → default


def test_reject_far_outliers_drops_glitch_keeps_body():
    """A France body + an 8-point spike ~2500 km south: the spike is dropped,
    every body point survives, and the guard is a pure no-op when disabled."""
    body = [[-40.0, LAT + i * STEP] for i in range(200)]   # the real ride
    glitch = [[-40.0, 20.0] for _ in range(8)]             # mid-ocean spike
    coords = body[:100] + glitch + body[100:]              # spike embedded mid-trace

    kept = _reject_far_outliers(coords, 300.0)
    assert len(kept) == len(body)                          # exactly the 8 dropped
    assert all(p[1] > 30.0 for p in kept)                  # no lat-20 glitch left
    # Disabled guard is the identity.
    assert _reject_far_outliers(coords, 0.0) == coords


@pytest.fixture()
def seeded_glitch_activity():
    """One consented gravel ride whose stored geometry contains an 8-point
    mid-ocean GPS spike embedded mid-trace (the exact prod failure shape)."""
    db = SessionLocal()
    db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _TEST_USER})
    db.commit()
    body = [[_LON_BASE, LAT + i * STEP] for i in range(200)]
    glitch = [[_LON_BASE, 20.0] for _ in range(8)]
    coords = body[:100] + glitch + body[100:]
    act = Activity(user_id=_TEST_USER, provider="file", sport="gravel", source=_MANUAL,
                   geometry_geojson=json.dumps({"type": "LineString", "coordinates": coords}),
                   contribute_heatmap=True)
    db.add(act)
    db.commit()
    aid = act.id
    db.close()
    yield aid
    db = SessionLocal()
    db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _TEST_USER})
    db.commit()
    db.close()


def test_iter_masked_runs_drops_glitch_end_to_end(monkeypatch, seeded_glitch_activity):
    """Drive the REAL iter_masked_runs: no emitted point of the glitch activity
    lands near the mid-ocean spike; the France body still yields runs."""
    monkeypatch.delenv("HEATMAP_RAW_MAX_SPAN_KM", raising=False)   # default 300 km on
    monkeypatch.setenv("TRACE_MASK_METERS", "50")                 # keep most of the ~2 km body
    _ = seeded_glitch_activity
    db = SessionLocal()
    try:
        # Isolate OUR seed by its unique mid-ocean longitude (lon == _LON_BASE);
        # the real corpus rides are in Europe (lon ~3), never here.
        pts = [pt for _a, _u, _s, run in iter_masked_runs(db)
               for pt in run if abs(pt[0] - _LON_BASE) < 0.5]
    finally:
        db.close()
    assert pts, "the France body must still produce runs"
    assert all(pt[1] > 30.0 for pt in pts), "no glitch (lat~20) point may survive"


# ── Region clip (opt-in bbox) — catches WHOLLY-corrupt traces ────────────────
# Prod had two 8-point activities located ENTIRELY mid-Atlantic (lat ~20). The
# median guard can't drop those (their own median IS the glitch); the opt-in
# HEATMAP_RAW_BBOX region gate does. Default OFF → "keep every desire line".

def test_plausible_bbox_off_by_default(monkeypatch):
    monkeypatch.delenv("HEATMAP_RAW_BBOX", raising=False)
    assert plausible_bbox() is None
    monkeypatch.setenv("HEATMAP_RAW_BBOX", "off")
    assert plausible_bbox() is None
    monkeypatch.setenv("HEATMAP_RAW_BBOX", "-12,34,32,62")
    assert plausible_bbox() == (-12.0, 34.0, 32.0, 62.0)
    # Semicolon form — how deploy-prod.sh ships it (survives gcloud's
    # comma-separated --update-env-vars without being split into 4 vars).
    monkeypatch.setenv("HEATMAP_RAW_BBOX", "-12;34;32;62")
    assert plausible_bbox() == (-12.0, 34.0, 32.0, 62.0)
    monkeypatch.setenv("HEATMAP_RAW_BBOX", "garbage")
    assert plausible_bbox() is None
    bb = (-12.0, 34.0, 32.0, 62.0)
    assert _in_bbox([3.8, 43.6], bb) and not _in_bbox([-29.8, 20.1], bb)


@pytest.fixture()
def seeded_ocean_activity():
    """One consented gravel ride whose ENTIRE geometry is a mid-ocean spike —
    the exact prod shape (8 points, all lat ~20). The median guard can't reject
    it; only the region clip can."""
    db = SessionLocal()
    db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _TEST_USER})
    db.commit()
    coords = [[-29.8, 20.09 + i * 1e-4] for i in range(8)]
    act = Activity(user_id=_TEST_USER, provider="file", sport="gravel", source=_MANUAL,
                   geometry_geojson=json.dumps({"type": "LineString", "coordinates": coords}),
                   contribute_heatmap=True)
    db.add(act)
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _TEST_USER})
    db.commit()
    db.close()


def test_bbox_drops_wholly_ocean_activity(monkeypatch, seeded_ocean_activity):
    """With a Europe bbox, the wholly-mid-ocean activity yields NO run."""
    monkeypatch.setenv("HEATMAP_RAW_BBOX", "-12,34,32,62")
    monkeypatch.setenv("TRACE_MASK_METERS", "0")   # masking off — isolate the bbox effect
    db = SessionLocal()
    try:
        ocean = [pt for _a, _u, _s, run in iter_masked_runs(db)
                 for pt in run if pt[1] < 30.0]
    finally:
        db.close()
    assert ocean == [], "no mid-ocean (lat<30) point may survive the region clip"


def test_build_zero_features_skips_tippecanoe_gracefully(monkeypatch, tmp_path):
    """A 0-feature raw corpus (no manual_upload activities yet) must NOT crash
    the build: tippecanoe aborts on empty input, so main() skips it + the upload
    and leaves the published tileset untouched. Regression for the prod-flip
    edge case (fix: build_pmtiles.main 0-feature guard)."""
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")
    bp = _patch_build_side_effects(monkeypatch)
    calls = {"tippecanoe": 0, "upload": 0}

    def _empty_raw(db, path, *a, **k):
        open(path, "w").close()   # 0 features written
        return 0

    monkeypatch.setattr("app.services.raw_trace_display.export_raw_geojson", _empty_raw)
    monkeypatch.setattr(bp, "run_tippecanoe",
                        lambda *a, **k: calls.__setitem__("tippecanoe", calls["tippecanoe"] + 1))
    monkeypatch.setattr(bp, "_upload_to_export_bucket",
                        lambda *a, **k: calls.__setitem__("upload", calls["upload"] + 1))

    bp.main(str(tmp_path), 6, 15, 1)   # must NOT raise
    assert calls == {"tippecanoe": 0, "upload": 0}, "empty corpus must skip build+upload"


# ── Bounded-memory lattice primitives (perf/raw-build-bounded-memory) ─────────
# The accumulator must fit ≤2 Gi at ~7 M cells. Two levers: a packed int64 cell
# key (replaces the ~120-byte tuple key) and a compact-by-arity per-cell user
# store (a bare id, promoted to a capped set only on a 2nd distinct user, vs a
# ~216-byte set per cell). These pins drive the REAL primitives.

def test_cell_key_is_unique_and_stable():
    deg = 0.0001
    sid = _sport_id("gravel")
    # Same cell (points within one deg-cell) → identical key.
    k1 = _cell_key(sid, 3.8000, 43.6000, deg)
    k2 = _cell_key(sid, 3.80005, 43.60005, deg)
    assert k1 == k2 and isinstance(k1, int)
    # Neighbouring cells → distinct keys (lat, lon, and sport axes).
    assert _cell_key(sid, 3.8002, 43.6000, deg) != k1
    assert _cell_key(sid, 3.8000, 43.6002, deg) != k1
    assert _cell_key(_sport_id("road"), 3.8000, 43.6000, deg) != k1
    # Negative (southern/western) coords pack without collision vs positives.
    keys = {
        _cell_key(sid, lon, lat, deg)
        for lon in (-40.0, 0.0, 40.0)
        for lat in (-20.0, 0.0, 43.6)
    }
    assert len(keys) == 9


def test_add_user_compact_and_exact_at_k2_boundary():
    users: dict = {}
    key = 12345
    # 1 distinct user → stored bare (no set object), count 1.
    _add_user(users, key, "u1")
    assert type(users[key]) is not set
    assert _user_count(users[key]) == 1
    # Same user again → still 1 (distinct users, not passes).
    _add_user(users, key, "u1")
    assert _user_count(users[key]) == 1
    # A genuine 2nd distinct user → promoted to a set, count EXACTLY 2 at the
    # K=2 gate boundary (no estimation error — this is the load-bearing pin).
    _add_user(users, key, "u2")
    assert type(users[key]) is set
    assert _user_count(users[key]) == 2
    # Absent cell → 0.
    assert _user_count(users.get(999)) == 0


def test_add_user_saturates_at_cap():
    users: dict = {}
    key = 7
    for i in range(_USER_CAP + 50):
        _add_user(users, key, f"u{i}")
    assert _user_count(users[key]) == _USER_CAP  # bounded, never unbounded


def test_lattice_memory_model_new_beats_old(tmp_path):
    """MODEL: at scale, the NEW (packed int64 key + compact user store) lattice
    is materially lighter per cell than the OLD (tuple key + per-cell set), and
    fits ≤2 Gi at 7.16 M cells. Drives the REAL _cell_key / _add_user."""
    import tracemalloc

    n = 200_000
    sid = _sport_id("gravel")
    uid = "user-42"

    # Mid-cell, 2-cell-spaced synthetic coords → each i maps to a distinct cell
    # (avoids float floor-boundary ambiguity; real GPS coords don't hit this).
    def _coords(i):
        return -40.0 + (i % 500) * 0.0002 + 0.00005, 43.6 + (i // 500) * 0.0002 + 0.00005

    tracemalloc.start()
    pass_old: dict = {}
    users_old: dict = {}
    for i in range(n):
        tkey = ("gravel", i // 500, i % 500)
        pass_old[tkey] = 1
        users_old.setdefault(tkey, set()).add(uid)
    _, peak_old = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del pass_old, users_old

    tracemalloc.start()
    pass_new: dict = {}
    users_new: dict = {}
    for i in range(n):
        lon, lat = _coords(i)
        key = _cell_key(sid, lon, lat, 0.0001)
        pass_new[key] = 1
        _add_user(users_new, key, uid)
    _, peak_new = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(pass_new) == n  # no key collisions
    del pass_new, users_new

    per_cell_new = peak_new / n
    # New is at least ~2.5× lighter than old, and comfortably ≤2 Gi at 7.16 M
    # cells (the prod-scale target for the ~5 m lattice; the ~11 m default
    # further cuts cell count ~4-5×).
    assert peak_new < peak_old / 2.5
    assert per_cell_new < 200.0
    assert per_cell_new * 7_160_000 < 2 * 1024**3


def test_drawn_coordinates_are_precise_not_lattice_snapped(tmp_path, monkeypatch):
    """The density lattice must NEVER reshape the drawn geometry: emitted
    coordinates are the verbatim round(p, 6) masked GPS points, not snapped to
    lattice-cell multiples."""
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    monkeypatch.delenv("HEATMAP_RAW_LATTICE_DEG", raising=False)  # default ~11 m
    # A vertical line at a deliberately OFF-lattice constant longitude, isolated
    # in empty ocean. ~1.3 km so the 200 m mask leaves a precise interior.
    off_lon = -40.012345          # not a 0.0001° multiple
    lat0 = 43.60007               # not a 0.0001° multiple
    coords = [[off_lon, round(lat0 + i * 0.00031, 6)] for i in range(40)]
    db = SessionLocal()
    db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _TEST_USER})
    db.commit()
    db.add(Activity(user_id=_TEST_USER, provider="file", sport="gravel", source=_MANUAL,
                    geometry_geojson=json.dumps({"type": "LineString", "coordinates": coords}),
                    contribute_heatmap=True))
    db.commit()
    try:
        path = os.path.join(tmp_path, "raw.geojsonl")
        export_raw_geojson(db, path)
        feats = [json.loads(line) for line in _read_lines(path)]
        ours = [f for f in feats if all(-40.02 < c[0] < -40.005
                                        for c in f["geometry"]["coordinates"])]
        assert ours, "the precise off-lattice line must survive masking"
        deg = lattice_deg()
        for feat in ours:
            for c in feat["geometry"]["coordinates"]:
                # Verbatim longitude (constant), NOT snapped to a lattice multiple.
                assert c[0] == off_lon
            lats = [c[1] for c in feat["geometry"]["coordinates"]]
            # At least one vertex is NOT a lattice-cell multiple (proves the
            # geometry is the raw trace, not the grid).
            assert any(abs((lat / deg) - round(lat / deg)) > 0.2 for lat in lats)
    finally:
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _TEST_USER})
        db.commit()
        db.close()


# ── Isolated (child-process) export — the 2026-09-02 4Gi OOM fix ─────────────
# The raw export's peak RAM (lattice + parse-arena high-water) used to stay
# resident while tippecanoe ran on top of it in the same memory cgroup (which
# also counts every tmpfs /tmp file). build_pmtiles now runs the export in a
# short-lived spawn-child by default so that memory is RETURNED to the OS.

def test_isolated_export_enabled_matrix(monkeypatch):
    """Explicit env wins in both directions; default = isolated everywhere
    EXCEPT under pytest (so monkeypatched exporter seams keep working)."""
    from app.jobs.build_pmtiles import _isolated_export_enabled

    monkeypatch.setenv("HEATMAP_EXPORT_ISOLATED", "true")
    assert _isolated_export_enabled() is True
    monkeypatch.setenv("HEATMAP_EXPORT_ISOLATED", "false")
    assert _isolated_export_enabled() is False
    # Unset → we ARE under pytest here (PYTEST_CURRENT_TEST set) → in-process.
    monkeypatch.delenv("HEATMAP_EXPORT_ISOLATED", raising=False)
    assert "PYTEST_CURRENT_TEST" in os.environ
    assert _isolated_export_enabled() is False
    # ...and without the pytest marker the default is ISOLATED (prod).
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert _isolated_export_enabled() is True


def test_isolated_export_child_matches_in_process(tmp_path, monkeypatch,
                                                  seeded_activities):
    """GOLDEN: the spawn-child export produces BYTE-IDENTICAL artifacts + stats
    to the in-process path (same corpus, same files) — the isolation must be a
    pure memory-lifecycle change, never a data change. Drives the REAL child
    (real spawn, its own DB session), not a mock."""
    from app.jobs.build_pmtiles import _run_raw_export

    db = SessionLocal()
    try:
        monkeypatch.setenv("HEATMAP_EXPORT_ISOLATED", "false")
        s_in: dict = {}
        w_in = _run_raw_export(db, str(tmp_path / "a.geojsonl"),
                               str(tmp_path / "a_pts.geojsonl"), s_in)

        monkeypatch.setenv("HEATMAP_EXPORT_ISOLATED", "true")
        s_child: dict = {}
        w_child = _run_raw_export(db, str(tmp_path / "b.geojsonl"),
                                  str(tmp_path / "b_pts.geojsonl"), s_child)
    finally:
        db.close()

    assert w_child == w_in > 0
    assert s_child == s_in
    assert (tmp_path / "b.geojsonl").read_bytes() == (tmp_path / "a.geojsonl").read_bytes()
    assert (tmp_path / "b_pts.geojsonl").read_bytes() == (tmp_path / "a_pts.geojsonl").read_bytes()
    # The stats hand-off file is cleaned up.
    assert not (tmp_path / "b.geojsonl.stats.json").exists()
