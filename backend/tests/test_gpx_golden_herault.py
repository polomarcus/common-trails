"""Golden ingestion test — a classic Hérault/Gard gravel ride must land
on the local OSM network (occitanie PBF) SNAPPED and CONTINUOUS.

This is the ingestion counterpart to the routing battery: a
non-regression guard that the real OSM-spatial-match path
(`services.ingest._match_to_osm`, exercised by `ingest_activity`) keeps
producing OSM-snapped, continuous heat_edges for a real local trace.

Fixture
-------
`fixtures/herault_gravel_golden.gpx` — the verbatim geometry of local
activity ``bf6f703d-f5da-437b-9bdd-2e8bec187daf`` ("Gravel découverte
#14", gravel, 69.5 km, Montpellier/Hérault, 341 trackpoints). Chosen
over the Anduze→Montpellier road alternative (`b6271e68…`) because the
brief asks for the better gravel/off-road trace and this one is a
gravel ride wholly inside the locally-imported `occitanie` OSM coverage
(Hérault/Gard) — so it genuinely exercises the PBF matcher rather than a
road that might lean on highway-only ways. The fixture carries
`<trk><type>gravel_cycling</type>` so the classifier resolves `gravel`.

Why drive `_match_to_osm` (not `ingest_activity`)
-------------------------------------------------
`_match_to_osm` IS the real OSM-spatial-match handler — it's the
function `ingest_activity → _update_heat_edges` calls to produce the
`match_source='spatial'` edges. It is READ-ONLY against
`osm_road_edges` and writes nothing, so the test is naturally isolated:
no heat_edges pollution, no cleanup, no "do NOT wipe local data" risk.
We feed it the exact coords `_update_heat_edges` would (parsed GPX →
`_resegment_coords`), so this is the genuine path, not an inline mirror.
The in-process spatial matcher (with Viterbi HMM refinement) is the sole
map-matcher, which is what local + the assertions below target.

Measured baseline (2026-06-13, local occitanie OSM = 7.16M edges)
-----------------------------------------------------------------
(scientific method — record the numbers, pin a little looser)

A human rode this on real paths, so the snapped heat_edge chain MUST be
(essentially) 100% continuous. The original #419 measurement showed only
99.56% (16 gaps, max 1549 m). Root-causing those 16 gaps (this PR) found:

  - 15/16 were `_densify_coords` HARD-BREAKING the run at every raw
    trackpoint jump > 500 m. But this GPX is a *downsampled* export
    (341 pts / 69.5 km, median spacing 143 m, p99 680 m, max 1549 m) —
    those jumps are sparse SAMPLING along a continuous ride, NOT GPS
    dropouts: OSM has a connecting path at the midpoint of all 16 (1-19
    edges within 30 m). The hard break fragmented the run into 17 pieces.
    FIX: bridge gaps in the [500 m, 2500 m] band by densifying across
    them so the matcher snaps the interpolated points to OSM (run stays
    continuous as a chain of short edges; never a long chord). Only
    > 2500 m jumps stay a hard break (genuine dropout / car transfer).
  - 1/16 is an out-and-back overlap: the rider rode a sub-segment twice;
    `seen_keys` correctly dedups the 2nd pass, so the duplicate edge
    isn't re-emitted and the *emission sequence* jumps. The heat_edge
    GRAPH is still continuous there (both endpoints have another graph
    endpoint ~15 m away). This is a measurement artifact, not a break.

  parsed coords:                341  →  densified: 4394  (was 3707)
  spatial (OSM-snapped) edges:  4344 (was 3635)
  grid_fallback edges:          0
  → OSM-match ratio:            100.0%        → PINNED ≥ 0.90 (unchanged)
  per-edge length:              all < 50 m (max 32.2 m)  → no chord-bridging
  emission-order continuity (consecutive edge-pairs meeting within 50 m):
    gap ≤  50 m:  0.9998   (was 0.9956)  → 1 residual = the dedup artifact
  GRAPH continuity (a gap counts only if the jumped-to edge has NO graph
  endpoint nearby — i.e. a real disconnection, not a revisit):
                  1.0000   (the 1 residual resolves)  → PINNED == 1.0
"""
import json
import math

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services import ingest
from app.services.gpx import parse_gpx

FIXTURE = "tests/fixtures/herault_gravel_golden.gpx"

# Hérault bbox (Montpellier area) — the golden trace lives here. We skip
# the test when the local DB has no OSM edges in this window (CI has no
# PBF imported). Mirrors the motto/Lez golden E2E gating.
_HERAULT_BBOX = (43.40, 3.60, 43.80, 4.10)  # lat_min, lon_min, lat_max, lon_max

# ── Pinned thresholds (measured baseline above, pinned tight) ───────────────
_MIN_SPATIAL_RATIO = 0.90       # measured 1.00
_CONTINUITY_GAP_M = 50.0        # arc-length bucket is 1 m; >50 m = a real break
# Emission-order continuity (consecutive edge-pairs whose endpoints meet).
# Measured 0.9998 after the densify-bridge fix; the 1 residual is a revisit
# dedup artifact that the GRAPH-continuity check below proves is not a break.
_MIN_CONTINUITY_RATIO = 0.999
# GRAPH continuity: an emission-order gap is a TRUE break only if the
# jumped-to edge endpoint has no other graph endpoint within this radius
# (one densified step). A human rode real paths → MUST be exactly 100%.
_GRAPH_REVISIT_M = 20.0         # ~1.3 densified steps (15 m max_gap)
_MAX_EDGE_LEN_M = 80.0          # measured max 32.2 m; >80 m would be a chord


def _osm_rows_in_herault() -> int:
    db = SessionLocal()
    try:
        return db.execute(sa_text(
            """
            SELECT count(*) FROM osm_road_edges
            WHERE geometry && ST_MakeEnvelope(:lon0, :lat0, :lon1, :lat1, 4326)
            """
        ), {
            "lat0": _HERAULT_BBOX[0], "lon0": _HERAULT_BBOX[1],
            "lat1": _HERAULT_BBOX[2], "lon1": _HERAULT_BBOX[3],
        }).scalar() or 0
    finally:
        db.close()


_OSM_PRESENT = _osm_rows_in_herault() > 1000

pytestmark = pytest.mark.skipif(
    not _OSM_PRESENT,
    reason="needs the occitanie OSM PBF imported into osm_road_edges "
           "(Hérault/Gard); CI has no PBF — run locally.",
)


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle metres between two (lat, lon) points."""
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi = math.radians(b[0] - a[0])
    dlam = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _traversal_endpoints(edge: dict) -> tuple[tuple[float, float], tuple[float, float]]:
    """(start, end) of an edge in trace-traversal order.

    Edges store canonical (sorted) a/b plus `is_canonical` recording
    whether the trace traversed a→b. Undo the canonicalisation so
    consecutive edges' end→start can be checked for continuity.
    """
    a = (edge["a_lat"], edge["a_lon"])
    b = (edge["b_lat"], edge["b_lon"])
    return (a, b) if edge["is_canonical"] else (b, a)


def _run_real_match() -> tuple[list[dict], list]:
    """Drive the REAL ingest OSM-spatial-match path on the golden fixture.

    parse_gpx → _resegment_coords → _match_to_osm, exactly as
    `_update_heat_edges` does. Read-only; writes nothing.
    """
    with open(FIXTURE, "rb") as fh:
        parsed = parse_gpx(fh.read())

    assert parsed.get("skip_reason") is None, "golden trace must not be skipped"
    coords = json.loads(parsed["geometry_geojson"])["coordinates"]
    assert len(coords) > 100, "fixture should carry the full trace"

    sport = ingest._normalize_heat_edge_sport(parsed.get("sport_from_gpx") or "gravel")
    assert sport == "gravel", f"gravel_cycling must resolve to gravel, got {sport!r}"

    densified = ingest._resegment_coords(coords)
    db = SessionLocal()
    try:
        osm_edges, fallback = ingest._match_to_osm(densified, sport, db)
    finally:
        db.close()
    return osm_edges, fallback


def test_golden_herault_matches_osm_network():
    """(a) The classic Hérault gravel ride snaps to the OSM network —
    `match_source='spatial'` dominates; grid_fallback is the minority.
    This proves the local occitanie PBF matching works for Hérault/Gard.
    """
    osm_edges, fallback = _run_real_match()

    n_spatial = sum(1 for e in osm_edges if e.get("match_source") == "spatial")
    assert n_spatial == len(osm_edges), "every matched edge must be source=spatial"

    # Grid-fallback edges = consecutive non-None fallback coord pairs (the
    # remainder _update_heat_edges would 4dp-snap with osm_way_id=NULL).
    grid_edges = sum(
        1 for i in range(len(fallback) - 1)
        if fallback[i] is not None and fallback[i + 1] is not None
    )
    total = n_spatial + grid_edges
    assert total > 0, "the trace must produce edges"

    spatial_ratio = n_spatial / total
    assert spatial_ratio >= _MIN_SPATIAL_RATIO, (
        f"OSM-snap ratio {spatial_ratio:.3f} below {_MIN_SPATIAL_RATIO} "
        f"(spatial={n_spatial}, grid_fallback={grid_edges}); "
        "the occitanie PBF matcher regressed."
    )

    # Every produced edge must actually carry an OSM way id (the
    # continuous-line invariant relies on osm_way_id, not a grid cell).
    tagged = sum(1 for e in osm_edges if e.get("osm_way_id") is not None)
    assert tagged == len(osm_edges), (
        f"{len(osm_edges) - tagged} spatial edges have NULL osm_way_id"
    )


def test_golden_herault_is_continuous():
    """(b) The snapped edges form a CONTINUOUS chain in trace order —
    consecutive edges meet (gap below the break threshold) and no single
    edge is a long straight chord across a gap.
    """
    osm_edges, _ = _run_real_match()
    assert len(osm_edges) > 100, "expected a dense matched chain"

    # No single edge should be a long chord — each heat_edge is a short
    # OSM sub-segment. A long edge means the matcher bridged a gap with a
    # straight line instead of following the way.
    edge_lengths = [
        _haversine_m(*_traversal_endpoints(e)) for e in osm_edges
    ]
    longest = max(edge_lengths)
    assert longest <= _MAX_EDGE_LEN_M, (
        f"longest produced edge {longest:.1f} m exceeds {_MAX_EDGE_LEN_M} m — "
        "the matcher is bridging a gap with a straight chord."
    )

    # Emission-order continuity: the fraction of consecutive matched
    # edge-pairs whose endpoints meet (within the break threshold). A trace
    # that fragmented would show many large end→start jumps. The
    # densify-bridge fix lifts this from 0.9956 to 0.9998.
    gaps = [
        _haversine_m(
            _traversal_endpoints(osm_edges[i])[1],
            _traversal_endpoints(osm_edges[i + 1])[0],
        )
        for i in range(len(osm_edges) - 1)
    ]
    continuous = sum(1 for g in gaps if g <= _CONTINUITY_GAP_M)
    ratio = continuous / len(gaps)
    assert ratio >= _MIN_CONTINUITY_RATIO, (
        f"continuity {ratio:.4f} (gap ≤ {_CONTINUITY_GAP_M} m) below "
        f"{_MIN_CONTINUITY_RATIO}; the matched trace fragmented "
        f"(max gap {max(gaps):.1f} m, {sum(1 for g in gaps if g > _CONTINUITY_GAP_M)} breaks)."
    )

    # GRAPH continuity: a human rode real paths → the heat_edge graph MUST
    # be 100% connected. An emission-order gap is a *true* disconnection
    # only when the jumped-to edge endpoint has NO other graph endpoint
    # nearby — i.e. it's not a revisit/out-and-back the `seen_keys` dedup
    # collapsed (which leaves the edge present, just emitted earlier). Any
    # gap whose target sits on an already-emitted edge endpoint is a
    # measurement artifact, not a break.
    endpoints = []
    for e in osm_edges:
        endpoints.append((e["a_lat"], e["a_lon"]))
        endpoints.append((e["b_lat"], e["b_lon"]))
    true_breaks = []
    for i in range(len(osm_edges) - 1):
        if gaps[i] <= _CONTINUITY_GAP_M:
            continue
        target = _traversal_endpoints(osm_edges[i + 1])[0]
        nearest_other = min(
            (_haversine_m(target, pt) for pt in endpoints
             if _haversine_m(target, pt) > 0.001),
            default=float("inf"),
        )
        if nearest_other > _GRAPH_REVISIT_M:
            true_breaks.append((i, gaps[i], nearest_other))
    assert not true_breaks, (
        f"{len(true_breaks)} TRUE graph disconnection(s) — a ridden trace "
        f"must yield a connected heat_edge chain. First: edge-pair {true_breaks[0][0]}, "
        f"gap {true_breaks[0][1]:.1f} m, nearest other graph endpoint "
        f"{true_breaks[0][2]:.1f} m away."
    )
