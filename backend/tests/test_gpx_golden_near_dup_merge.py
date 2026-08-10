"""Golden — the near-duplicate MERGE invariant (the [[project_dedup_bug]] killer).

Paul's north star after the ingestion tests: *"une heatmap propre et surtout
CONTINUE (pas de fragmentation) ou de GPX très proche sur la carte et qui
auraient dû être fusionnées."* — two rides on the SAME physical path, separated
only by GPS noise, must render as ONE heat line, not parallel near-duplicates.

How the merge ACTUALLY works (verified against the shared aggregation builder
`app/services/heat_aggregation.py`): the display aggregation groups OSM-matched
heat_edges by ``osm_way_id`` and emits the OSM *way* geometry —
``COALESCE(w.way_geometry, o.geometry, g.heat_geom)`` with ``w`` = the
``osm_ways`` side-table (migration 0056) — ONE row per (osm_way_id, sport). So N riders on the same way collapse to one rendered line
at the way's true geometry. Feathering ("criss-crossing micro-zigzags") can
therefore only come from edges that DON'T get a way id — the
``osm_way_id IS NULL`` grid-fallback rows.

That makes the anti-feather invariant precise and lets this test be READ-ONLY
(no DB writes, no restore): ingest is deterministic given the matcher, so we
compare what `_match_to_osm` produces for a ride vs. a GPS-OFFSET copy of it
(a constant ~7 m parallel shift = a textbook second-rider GPS bias):

  (1) SAME WAYS — the offset copy snaps to (near) the SAME ``osm_way_id`` set.
      → both collapse to the same aggregation rows → ONE line, no parallels.
  (2) LOW GRID-FALLBACK — few edges land with ``osm_way_id IS NULL`` (those are
      the only ones that can feather), on both the ride and its offset copy.

NOTE on `edge_key` overlap: it is INTENTIONALLY low between offset riders
(edge endpoints follow GPS sample positions, so the fine segmentation differs).
That is by design — cross-rider merge for DISPLAY is by ``osm_way_id``, not
``edge_key``. The pass_count golden pins the `edge_key`-level accumulation on the
subset that does align. Here we log the edge_key overlap as characterization,
and assert only the way-level invariant. See [[reference_heatmap_quality_invariants]].

golden-marked → auto-skips when the occitanie PBF is absent (CI).
"""
import json
import os

import pytest

from app.db.session import SessionLocal
from app.services import ingest
from app.services.gpx import parse_gpx
from tests.conftest import osm_present_in_bbox

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "herault_gravel_golden.gpx")
_SPORT = "gravel"

pytestmark = [
    pytest.mark.golden,
    pytest.mark.skipif(
        not (osm_present_in_bbox() and os.path.exists(_FIXTURE)),
        reason="needs the occitanie OSM PBF in osm_road_edges + the golden fixture; CI has neither.",
    ),
]


def _coords() -> list:
    with open(_FIXTURE, "rb") as fh:
        return json.loads(parse_gpx(fh.read())["geometry_geojson"])["coordinates"]


def _offset(coords: list, east_m: float, north_m: float) -> list:
    """Shift every point by a CONSTANT (east_m, north_m) — a parallel GPS bias.
    Constant (not random) so the test is deterministic and reproducible."""
    dlon = east_m / (ingest._DEG_TO_M * ingest._COS_LAT_FRANCE)
    dlat = north_m / ingest._DEG_TO_M
    out = []
    for p in coords:
        q = [p[0] + dlon, p[1] + dlat]
        if len(p) > 2:
            q.append(p[2])
        out.append(q)
    return out


def _match(coords: list, db):
    """Read-only: (edge_keys, osm_way_ids, grid_fallback_count) for a ride."""
    edges, _ = ingest._match_to_osm(ingest._resegment_coords(coords), _SPORT, db)
    keys = {e["edge_key"] for e in edges}
    ways = {e["osm_way_id"] for e in edges if e.get("osm_way_id") is not None}
    grid = sum(1 for e in edges if e.get("osm_way_id") is None)
    return keys, ways, grid, len(edges)


def test_gps_offset_copy_collapses_to_same_ways():
    orig = _coords()
    # ~4 m parallel offset (3 m E + 3 m N): a realistic difference between two
    # GPS recordings of the same path (typical consumer noise is 3-5 m).
    shifted = _offset(orig, east_m=3.0, north_m=3.0)

    db = SessionLocal()
    try:
        keys_o, ways_o, grid_o, n_o = _match(orig, db)
        keys_s, ways_s, grid_s, n_s = _match(shifted, db)
    finally:
        db.close()

    assert ways_o, "fixture must match real OSM ways"

    # (0) SANE WAY COUNT — the DIRECT guard on the run-level osm_way_id bug.
    # A 69.5 km ride crosses hundreds of OSM ways; the matcher must tag them
    # per-point. The bug tagged every edge in a run with the run's single way
    # id → this 321-way ride collapsed to 16 (origin/main) or 2 (with the
    # densify-bridge). Floor 100 cleanly separates correct (321) from either
    # collapse. This is the metric the BEFORE/AFTER showed is the true
    # discriminator (way_overlap below barely moves — 0.81→0.82 — because a
    # collapsed offset copy overlaps a collapsed original).
    assert len(ways_o) >= 100, (
        f"the {len(ways_o)}-way ride collapsed onto too few osm_way_ids — the "
        "spatial matcher is tagging edges with a run-level (not per-point) way "
        "id, which breaks the by-way aggregation merge"
    )

    # (1) SAME WAYS — both rides snap to (near) the same osm_way_id set, so the
    # aggregation groups them into the SAME single rows → one line, no parallels.
    #
    # MEASURED BASELINE (local occitanie PBF, this fixture, ~4 m offset): 82 %.
    # The residual ~18 % is way-boundary FLIPS — at the ~hundreds of OSM way
    # breaks a 4 m offset shifts a point onto the adjacent way; those render as
    # neighbouring (not parallel) lines, which is correct. It is NOT same-way
    # feathering. Threshold 0.75 = baseline minus margin; it cleanly catches the
    # per-point-osm_way_id regression that gave 50 % (a ride collapsed onto ~2
    # run-level way ids → aggregation mis-grouped). See ingest.py spatial-match
    # `osm_way_id` assignment + [[reference_heatmap_quality_invariants]].
    way_overlap = len(ways_o & ways_s) / len(ways_o)
    assert way_overlap >= 0.75, (
        f"GPS-offset copy shares only {way_overlap:.0%} of the original's "
        f"osm_way_ids ({len(ways_o & ways_s)}/{len(ways_o)}) — below the 0.75 "
        "floor. A ride collapsing onto a few way ids (run-level instead of "
        "per-point) breaks the by-way aggregation merge → parallel heat lines"
    )

    # (2) LOW GRID-FALLBACK — osm_way_id-NULL edges are the ONLY ones that can
    # feather (they don't group by way). Keep that fraction small on both.
    gf_o = grid_o / n_o
    gf_s = grid_s / n_s
    assert gf_o <= 0.10, f"ride grid-fallback ratio {gf_o:.0%} too high (feathering risk)"
    assert gf_s <= 0.10, f"offset-copy grid-fallback ratio {gf_s:.0%} too high"

    # Characterization (NOT asserted high — intentionally fine-grained): the
    # edge_key overlap is expected to be low; cross-rider merge is by way.
    ek_overlap = len(keys_o & keys_s) / len(keys_o)
    print(f"\n[near-dup] ways={len(ways_o)} way_overlap={way_overlap:.1%} "
          f"grid_fallback={gf_o:.1%}/{gf_s:.1%} edge_key_overlap={ek_overlap:.1%}")
