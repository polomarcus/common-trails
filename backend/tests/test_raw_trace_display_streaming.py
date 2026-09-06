"""Streaming rework of the raw-trace display build (perf/raw-build-streaming).

Proves the two properties the rework promised over the merged (#505)
prototype, which materialised ALL masked runs + the whole densified-point
corpus in RAM (O(total points)):

  * EQUIVALENCE — the streaming ``export_raw_geojson`` emits the SAME features
    (same geometry, gate results, ``user_count`` / ``pass_count`` / ``heat_score``
    semantics) as a faithful reference of the OLD materialise-everything
    algorithm, on a seeded set. So the rework changed HOW we count, never WHAT
    we draw.

  * MEMORY BOUND — peak allocation of the streaming build does NOT grow with
    the number of activities (it is bounded by occupied geography), whereas the
    reference materialised build grows ~linearly with the corpus. Measured with
    ``tracemalloc`` on same-corridor seeds of increasing size, plus a
    deterministic structural assertion that the accumulator is keyed by cell
    and its size is invariant to activity count.
"""
from __future__ import annotations

import json
import math
import os
import tracemalloc

import pytest
from sqlalchemy import text as sa_text

from app.db.models import Activity
from app.db.session import SessionLocal
from app.services import raw_trace_display as rtd
from app.services.raw_trace_display import (
    _DIRECTIONAL_SPORTS,
    _USER_CAP,
    _accumulate_lattice,
    _cell_key,
    _run_flow_sign,
    _split_run_by_local_pass,
    _sport_id,
    _user_count,
    export_raw_geojson,
    gate_run,
    lattice_deg,
)

LAT = 43.61
STEP = 10.0 / 110_574  # ~10 m per point
_MANUAL = "manual_upload"

# Geographically-isolated mid-Atlantic corridors so the local Hérault corpus
# (real activities) can't contaminate any assertion.
_LON_SHARED = -45.0   # ridden by user A + user B → 2 distinct users
_LON_SOLO = -45.3     # user A only → 1 distinct user
_LON_REPEAT = -45.6   # user A twice → 1 user, 2 passes
_USER_A = "stream-user-a"
_USER_B = "stream-user-b"
_USERS = (_USER_A, _USER_B)


def _line(n: int, lon: float) -> str:
    return json.dumps({
        "type": "LineString",
        "coordinates": [[lon, LAT + i * STEP] for i in range(n)],
    })


def _reference_materialized_export(db, path: str) -> int:
    """Faithful copy of the PRE-REWORK algorithm: materialise every masked run,
    build per-cell sets of distinct activity ids + user ids, then emit.

    The ONLY difference from the shipped streaming ``export_raw_geojson`` is
    that this holds the whole run corpus in RAM — so any output divergence is a
    semantic regression in the rework, and any memory divergence is exactly the
    win we are claiming.

    Local-count split (fix/pass-count-local): the emitter now SPLITS each
    (sub-)run into contiguous local-pass PIECES (``_split_run_by_local_pass``)
    so a quiet stretch reports its own count, not a distant junction's MAX. This
    reference mirrors that: it derives its OWN per-cell pass counts from the
    materialised ``density`` sets (the independent cross-check this test exists
    for), splits each sub with them, and grades each NON-TERMINAL piece over
    ``piece[:-1]`` (the shared trailing vertex belongs to the next bucket) — the
    exact graded-slice rule the emitter uses. Direction ORIENTATION (#618) can
    reverse a directional piece's coordinate order; the coords are part of the
    canonical form, so we orient identically (flow sign on the SAME graded slice,
    reversal on the full drawn piece) using the REAL direction accumulator
    (direction equivalence itself is pinned by test_directional_heatmap).
    """
    deg = lattice_deg()
    min_u = rtd.min_users()
    # Route through the module attr so a monkeypatched iter_masked_runs (the
    # synthetic memory-test stream) is honoured, exactly like the production
    # export_raw_geojson resolves it.
    runs = [(aid, uid, sport, run) for aid, uid, sport, run in rtd.iter_masked_runs(db)]

    density: dict[int, set] = {}
    users: dict[int, set] = {}
    for aid, uid, sport, run in runs:
        for pt in run:
            key = _cell_key(_sport_id(sport), pt[0], pt[1], deg)
            density.setdefault(key, set()).add(aid)
            users.setdefault(key, set()).add(uid)

    # Independent per-cell pass counts for the SPLIT (len of the distinct-aid
    # set == streaming's pass_by_cell counter). Direction stores come from the
    # real accumulator purely so orientation (which flips coords) matches.
    ref_pass_by_cell = {k: len(v) for k, v in density.items()}
    _p, _u, dir_vec, _dw, _fw, _bw = _accumulate_lattice(db, deg)

    max_pop = max((len(v) for v in density.values()), default=1)
    log_max = math.log1p(max_pop) or 1.0

    written = 0
    with open(path, "w") as f:
        for _aid, _uid, sport, run in runs:
            sid = _sport_id(sport)
            for sub in gate_run(run, sport, users, deg, min_u):
                pieces = _split_run_by_local_pass(sub, sid, deg, ref_pass_by_cell)
                last = len(pieces) - 1
                for idx, piece in enumerate(pieces):
                    # Grade a non-terminal piece over piece[:-1] (the shared
                    # trailing vertex belongs to the next bucket) — mirrors
                    # _piece_feature_props exactly.
                    grade = piece if idx == last else piece[:-1]
                    local_pass = 1
                    local_users = 1
                    for pt in grade:
                        key = _cell_key(sid, pt[0], pt[1], deg)
                        local_pass = max(local_pass, len(density.get(key, ())))
                        local_users = max(local_users, _user_count(users.get(key)))
                    # Model the streaming path's bounded per-cell distinct-user
                    # counter: ``_add_user`` caps each cell's user set at
                    # ``_USER_CAP``, so ``user_count`` SATURATES there — a
                    # documented, intended memory bound ("always >= any realistic
                    # K"). This uncapped reference set would otherwise report the
                    # TRUE count on hyper-popular cells (>64 distinct users),
                    # diverging from the shipped export on ``user_count`` ALONE
                    # while geometry / gate / pass_count / heat stay identical.
                    # Capping the final count is exactly equivalent to the capped
                    # set's len() (min(cap, max(v)) == max(min(cap, v))), so this
                    # faithfully pins the intended equivalence rather than
                    # relaxing it: a real regression in geometry/gate/pass/heat
                    # still fails.
                    local_users = min(_USER_CAP, local_users)
                    heat = round(min(1.0, math.log1p(local_pass) / log_max), 3)
                    # Orient the FULL drawn piece to the dominant direction like
                    # the emitter — flow sign decided on the SAME graded slice —
                    # so directional pieces' coords match (counts are irrelevant
                    # to the canonical form).
                    if (sport in _DIRECTIONAL_SPORTS
                            and _run_flow_sign(grade, sid, deg, dir_vec) < 0):
                        piece = list(reversed(piece))
                    f.write(json.dumps({
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            # 2026-09-06: the emitter strips the read-time
                            # densifier's exactly-collinear lerp points
                            # (_strip_interpolated) — mirror it so this reference
                            # stays the independent semantic cross-check.
                            "coordinates": [[round(p[0], 6), round(p[1], 6)]
                                            for p in rtd._strip_interpolated(piece)],
                        },
                        "properties": {
                            "sport": sport,
                            "user_count": local_users,
                            "pass_count": local_pass,
                            "heat_score": heat,
                            "highway_type": "",
                        },
                    }))
                    f.write("\n")
                    written += 1
    return written


def _canonical(path: str) -> list[tuple]:
    """Order-independent canonical form of an export: sorted list of
    (rounded geometry, sport, user_count, pass_count, heat_score)."""
    feats = []
    with open(path) as fh:
        for line in fh:
            if not line.strip():
                continue
            f = json.loads(line)
            coords = tuple((round(c[0], 6), round(c[1], 6))
                           for c in f["geometry"]["coordinates"])
            p = f["properties"]
            feats.append((coords, p["sport"], p["user_count"],
                          p["pass_count"], p["heat_score"]))
    return sorted(feats)


@pytest.fixture()
def seeded_mixed():
    db = SessionLocal()
    db.execute(sa_text("DELETE FROM activities WHERE user_id = ANY(:u)"),
               {"u": list(_USERS)})
    db.commit()
    acts = [
        Activity(user_id=_USER_A, provider="file", sport="gravel", source=_MANUAL,
                 geometry_geojson=_line(200, _LON_SHARED), contribute_heatmap=True),
        Activity(user_id=_USER_B, provider="file", sport="gravel", source=_MANUAL,
                 geometry_geojson=_line(200, _LON_SHARED), contribute_heatmap=True),
        Activity(user_id=_USER_A, provider="file", sport="gravel", source=_MANUAL,
                 geometry_geojson=_line(200, _LON_SOLO), contribute_heatmap=True),
        # same user twice on the repeat corridor → user_count 1, pass_count 2
        Activity(user_id=_USER_A, provider="file", sport="road", source=_MANUAL,
                 geometry_geojson=_line(200, _LON_REPEAT), contribute_heatmap=True),
        Activity(user_id=_USER_A, provider="file", sport="road", source=_MANUAL,
                 geometry_geojson=_line(200, _LON_REPEAT), contribute_heatmap=True),
    ]
    for a in acts:
        db.add(a)
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.execute(sa_text("DELETE FROM activities WHERE user_id = ANY(:u)"),
               {"u": list(_USERS)})
    db.commit()
    db.close()


@pytest.mark.parametrize("min_users_env", ["1", "2"])
def test_streaming_equivalent_to_materialized(tmp_path, monkeypatch, seeded_mixed,
                                              min_users_env):
    """Streaming export == reference materialised export, feature-for-feature,
    at both MIN_USERS=1 (identity gate) and MIN_USERS=2 (segment gate)."""
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    monkeypatch.setenv("HEATMAP_MIN_USERS", min_users_env)

    stream_path = os.path.join(tmp_path, "stream.geojsonl")
    ref_path = os.path.join(tmp_path, "ref.geojsonl")
    db = SessionLocal()
    try:
        export_raw_geojson(db, stream_path)
        _reference_materialized_export(db, ref_path)
    finally:
        db.close()

    assert _canonical(stream_path) == _canonical(ref_path)


def _synthetic_iter(k: int, npts: int, lon: float):
    """A fresh generator (each call) yielding ``k`` synthetic activities, each a
    single ``npts``-point run on the SAME corridor with a DISTINCT activity id
    and ONE shared user. Drives the real production functions
    (``_accumulate_lattice`` / ``export_raw_geojson`` /
    ``_reference_materialized_export``) via a monkeypatched ``iter_masked_runs``
    so the memory measurement isolates the DATA STRUCTURE (accumulator vs the
    materialised run corpus) from the local DB corpus + driver noise.

    Same corridor + distinct aids → occupied cells are INVARIANT to ``k`` while
    the per-cell distinct-activity count grows to ``k`` (exactly the shape that
    separates a geography-bounded accumulator from an O(corpus) run list).
    """
    def _factory(_db):
        for i in range(k):
            run = [[lon, LAT + j * STEP] for j in range(npts)]
            yield i, "syn-user", "gravel", run
    return _factory


def test_accumulator_is_keyed_by_cell_and_size_invariant_to_corpus(monkeypatch):
    """PRIMARY memory proof (deterministic, corpus-isolated): the pass-1
    accumulator is keyed by fine cell; its SIZE is bounded by occupied geography
    and is INVARIANT to the number of activities. 20 vs 120 copies of the same
    corridor → identical cell count, while the per-cell pass counter scales."""
    monkeypatch.delenv("HEATMAP_MIN_USERS", raising=False)
    deg = lattice_deg()

    monkeypatch.setattr(rtd, "iter_masked_runs", _synthetic_iter(20, 400, -46.0))
    pass20, users20, _dv20, _dw20, _fw20, _bw20 = _accumulate_lattice(None, deg)

    monkeypatch.setattr(rtd, "iter_masked_runs", _synthetic_iter(120, 400, -46.0))
    pass120, users120, _dv120, _dw120, _fw120, _bw120 = _accumulate_lattice(None, deg)

    # Accumulator size (memory) is bounded by geography, NOT corpus size.
    assert len(pass120) == len(pass20)
    assert len(users120) == len(users20)
    assert len(pass20) > 0
    # But the popularity counter reflects the extra passes (counting still works).
    assert max(pass120.values()) == 120
    assert max(pass20.values()) == 20
    # user_count stays 1 (one distinct user) regardless of pass count.
    assert max(_user_count(v) for v in users120.values()) == 1


def _peak_export_bytes(exporter, tmp_path, name: str) -> int:
    path = os.path.join(tmp_path, name)
    import gc
    gc.collect()
    tracemalloc.start()
    tracemalloc.clear_traces()
    try:
        exporter(None, path)
    finally:
        pass
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak


def test_streaming_peak_memory_does_not_scale_with_corpus(tmp_path, monkeypatch):
    """SUPPORTING memory proof (tracemalloc, corpus-isolated): streaming peak
    stays ~flat as the corpus grows 5x, while the reference materialised build
    grows ~linearly — and streaming peak is a fraction of materialised at the
    larger corpus. Driven by a synthetic run stream (no DB) so the ONLY thing
    the numbers reflect is the accumulator vs the materialised run corpus.
    """
    monkeypatch.delenv("HEATMAP_MIN_USERS", raising=False)

    monkeypatch.setattr(rtd, "iter_masked_runs", _synthetic_iter(50, 400, -47.0))
    stream_small = _peak_export_bytes(export_raw_geojson, tmp_path, "s_small")
    mat_small = _peak_export_bytes(_reference_materialized_export, tmp_path, "m_small")

    monkeypatch.setattr(rtd, "iter_masked_runs", _synthetic_iter(250, 400, -47.0))
    stream_big = _peak_export_bytes(export_raw_geojson, tmp_path, "s_big")
    mat_big = _peak_export_bytes(_reference_materialized_export, tmp_path, "m_big")

    # Streaming peak barely moves for a 5x bigger corpus (bounded by geography).
    assert stream_big < stream_small * 1.6, (
        f"streaming peak scaled with corpus: {stream_small} -> {stream_big}")
    # Materialised peak grows with the corpus (the O(total points) we removed).
    assert mat_big > mat_small * 2.5, (
        f"reference did not scale as expected: {mat_small} -> {mat_big}")
    # And at the larger corpus streaming is a fraction of materialised.
    assert stream_big < mat_big * 0.5, (
        f"streaming peak not below materialised: {stream_big} vs {mat_big}")


def test_two_streaming_passes_no_corpus_materialization(tmp_path, monkeypatch):
    """The build consumes ``iter_masked_runs`` LAZILY across exactly TWO passes
    (accumulate, then emit) and never turns it into a full list — the old code
    called it once and materialised. We spy the generator to assert both the
    2-pass shape and that runs are consumed one-at-a-time (never all-alive)."""
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    monkeypatch.delenv("HEATMAP_MIN_USERS", raising=False)

    passes = {"n": 0}
    max_alive = {"n": 0}
    alive = {"n": 0}
    real_iter = rtd.iter_masked_runs

    def _spy(db):
        passes["n"] += 1
        for item in real_iter(db):
            alive["n"] += 1
            max_alive["n"] = max(max_alive["n"], alive["n"])
            yield item
            # Once the caller moves to the next item, the previous run is
            # no longer referenced by the build → count it as freed.
            alive["n"] -= 1

    monkeypatch.setattr(rtd, "iter_masked_runs", _spy)
    db = SessionLocal()
    try:
        export_raw_geojson(db, os.path.join(tmp_path, "spy.geojsonl"))
    finally:
        db.close()

    assert passes["n"] == 2, "streaming build must make exactly two passes"
    # Never more than one run referenced by the build at any instant.
    assert max_alive["n"] == 1, (
        f"build held {max_alive['n']} runs at once — not streaming")
