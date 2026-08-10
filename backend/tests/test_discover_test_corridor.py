"""Tests for ``app.cli.discover_test_corridor`` — the helper that powers
the Layer-2 drag-edit Playwright spec.

Three scenarios:
1. **Empty bbox** → ``found: false``.
2. **Connected chain ≥ min_length** → ``found: true`` with the expected
   edge count + the merged geometry.
3. **Disconnected edges, each below min_length individually but their
   union meets the bbox threshold** → still ``found: false`` because the
   algorithm requires CONNECTED edges (the whole point of "candidate
   corridor").

All tests use an off-grid bbox in northern Iceland (same pattern as
`test_heat_edges_same_user_idempotence.py`) so they don't collide with
ingested prod data.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text as sa_text

from app.cli.discover_test_corridor import discover_corridor
from app.db.session import SessionLocal
from app.services.ingest import _update_heat_edges

# Bbox distinct from the other Iceland test files (we shift further
# north so concurrent test runs don't fight over heat_edges rows).
_SPORT = "gravel"
_BBOX = (-22.5, 66.0, -22.0, 66.2)


def _wipe_bbox() -> None:
    """Delete heat_edges + contributors that intersect ``_BBOX``."""
    min_lon, min_lat, max_lon, max_lat = _BBOX
    db = SessionLocal()
    try:
        db.execute(sa_text(
            """
            DELETE FROM heat_edge_contributors WHERE edge_key IN (
                SELECT edge_key FROM heat_edges
                WHERE ST_Intersects(
                    geometry,
                    ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
                )
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat})
        db.execute(sa_text(
            """
            DELETE FROM heat_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat})
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clear_bbox():
    _wipe_bbox()
    yield
    _wipe_bbox()


def _geojson(coords: list) -> str:
    return json.dumps({"type": "LineString", "coordinates": coords})


def _ingest_trace(user_id: str, activity_id: str, coords: list) -> None:
    """Wrapper around ``_update_heat_edges`` (3D coords supported)."""
    _update_heat_edges(user_id, _SPORT, _geojson(coords), activity_id=activity_id)


def _make_activity_id(token: str) -> str:
    """Build a deterministic UUID-shaped activity_id from a free-form
    token. The contributors table casts the column to uuid via the
    `activity_id` column type (added by migration 0052), so the helper
    needs a real `00000000-0000-0000-0000-XXXXXXXXXXXX` shape — bare
    strings like "discABab02" hit a PG DataError on insert.
    """
    # Hex-friendly hash slot — 12 chars at the end of a v0 UUID.
    h = abs(hash(token)) % (16 ** 12)
    return f"00000000-0000-0000-0000-{h:012x}"


def _ingest_connected_chain(prefix: str) -> None:
    """Ingest a 5-vertex line by 3 distinct users so K-anonymity (>=2) is
    met on every segment. Coords spaced by ~0.0008° (~88 m at lat 66) so
    after densification + grid-snap they collapse into ~4 contiguous
    edges in the bbox center.
    """
    base_lon = -22.25
    base_lat = 66.10
    step = 0.0008
    chain = [[base_lon + i * step, base_lat + i * step * 0.5] for i in range(5)]
    for u_idx, user in enumerate(("ua", "ub", "uc")):
        _ingest_trace(user, _make_activity_id(f"{prefix}-{user}-{u_idx}"), chain)


def _ingest_disconnected_edges(prefix: str) -> None:
    """Ingest two SEPARATE short traces far apart in the bbox — each by
    3 users (so user_count>=2) but the two edges share NO endpoint.
    """
    # Edge A — short (~57 m) and isolated near the north of the bbox.
    # Keep each edge WELL under _DENSIFY_GPS_BREAK_M (500 m): the two-tier
    # densifier now BRIDGES intra-trace gaps in [500 m, 2500 m] (continuity
    # fix, PR #420), so an edge spanning ~600 m would densify into one long
    # ~0.6 km connected component — exactly what this test must NOT have.
    chain_a = [[-22.40, 66.15], [-22.3992, 66.1504]]
    # Edge B — short (~57 m) near the south of the bbox; ~11 km from A so the
    # two edges can never bridge to each other (gap >> 2500 m cap).
    chain_b = [[-22.10, 66.05], [-22.0992, 66.0504]]
    for u_idx, user in enumerate(("ua", "ub", "uc")):
        for letter, chain in (("a", chain_a), ("b", chain_b)):
            _ingest_trace(user, _make_activity_id(f"{prefix}-{letter}-{user}-{u_idx}"), chain)


# ── Tests ────────────────────────────────────────────────────────────────────


def test_empty_bbox_returns_not_found():
    result = discover_corridor(
        bbox=_BBOX,
        sport=_SPORT,
        min_user_count=2,
        min_length_km=0.05,  # very small, but bbox is empty
    )
    assert result["found"] is False
    assert "reason" in result


def test_connected_chain_meeting_threshold_returns_found():
    _ingest_connected_chain("conn")
    result = discover_corridor(
        bbox=_BBOX,
        sport=_SPORT,
        min_user_count=2,
        # 5-vertex chain spans ~350 m. Threshold 50 m so we comfortably
        # pass even after grid-snap collapses some segments.
        min_length_km=0.05,
    )
    assert result["found"] is True, f"Expected found=True, got: {result}"
    assert result["sport"] == _SPORT
    assert result["edge_count"] >= 1
    assert result["max_user_count"] >= 2
    geom = result["geometry"]
    assert geom["type"] == "LineString"
    coords = geom["coordinates"]
    assert len(coords) >= 2
    # Sanity: every stitched coord is inside the bbox.
    for lon, lat in coords:
        assert _BBOX[0] <= lon <= _BBOX[2], f"lon {lon} outside bbox"
        assert _BBOX[1] <= lat <= _BBOX[3], f"lat {lat} outside bbox"
    # Start/end/midpoint must be 2-element [lon, lat] inside the bbox.
    for k in ("start", "end", "midpoint"):
        pt = result[k]
        assert len(pt) == 2
        assert _BBOX[0] <= pt[0] <= _BBOX[2]
        assert _BBOX[1] <= pt[1] <= _BBOX[3]
    assert 0.0 <= result["bearing_deg"] < 360.0
    # The total_length_km must be at least 0.05 (we asserted threshold).
    assert result["total_length_km"] >= 0.05


def test_min_length_above_chain_returns_not_found():
    """The connected chain is ~350 m. Requiring 5 km must return
    ``found: false`` with a reason that names the longest path / component
    we did discover."""
    _ingest_connected_chain("short")
    result = discover_corridor(
        bbox=_BBOX,
        sport=_SPORT,
        min_user_count=2,
        min_length_km=5.0,  # way more than the chain spans
    )
    assert result["found"] is False
    assert "reason" in result
    # The diagnostic fields tell the caller WHY — they should mention
    # the longest component we found OR the simple-path length.
    assert (
        "longest_component_km" in result
        or "component_length_km" in result
        or "simple_path_km" in result
    )


def test_disconnected_edges_pick_longest_component():
    """When the bbox contains two separate connected edges (each meeting
    user_count) but no shared vertex, the algorithm picks the LONGEST
    component. With both edges sized identically and well below
    ``min_length_km``, the function returns ``found: false`` and the
    diagnostic reports the longest fragment (~50-60 m).

    This pins the behaviour: disconnected edges DO NOT get spliced
    together. The drag-edit test relies on this — a fake "corridor"
    stitched across a gap would produce a misleading drag target.
    """
    _ingest_disconnected_edges("disc")
    result = discover_corridor(
        bbox=_BBOX,
        sport=_SPORT,
        min_user_count=2,
        # 1 km threshold > either disconnected fragment (~50 m each).
        min_length_km=1.0,
    )
    assert result["found"] is False
    # Diagnostic must surface the longest CONNECTED fragment, not their
    # combined length — that's the contract the spec depends on.
    longest = result.get("longest_component_km")
    if longest is not None:
        # Each fragment is short (well under 1 km). If the algorithm
        # had stitched them, this number would be much larger.
        assert longest < 0.5, (
            f"longest connected component should be a single short edge; "
            f"got {longest} km — possible cross-component stitch bug"
        )


def test_low_user_count_filter_excludes_unpopular_edges():
    """With ``min_user_count=10`` (very high), no edges qualify even if
    a chain exists in the bbox."""
    _ingest_connected_chain("singleuser")
    result = discover_corridor(
        bbox=_BBOX,
        sport=_SPORT,
        min_user_count=10,
        min_length_km=0.05,
    )
    assert result["found"] is False
    assert "no heat_edges" in result["reason"].lower() or "user_count" in result["reason"].lower()


def test_corridor_deterministic_across_runs():
    """Two consecutive calls against the same DB must return the SAME
    corridor (same `edge_keys` in the same order, same start/end). The
    ``ORDER BY edge_key`` on the SQL pull is what guarantees this — the
    BFS over the loaded edges picks the same simple path only if the
    input row order is stable. Without the ORDER BY, two runs can
    produce different corridors and the Playwright spec becomes flaky.
    """
    _ingest_connected_chain("deterministic")
    result1 = discover_corridor(
        bbox=_BBOX,
        sport=_SPORT,
        min_user_count=2,
        min_length_km=0.05,
    )
    result2 = discover_corridor(
        bbox=_BBOX,
        sport=_SPORT,
        min_user_count=2,
        min_length_km=0.05,
    )
    assert result1["found"] is True and result2["found"] is True
    assert result1["edge_keys"] == result2["edge_keys"], (
        f"Two runs against the same DB produced different edge_keys.\n"
        f"Run 1: {result1['edge_keys']}\nRun 2: {result2['edge_keys']}\n"
        f"Without `ORDER BY edge_key` on the SQL pull, the BFS over "
        f"loaded edges picks the corridor non-deterministically — "
        f"Playwright spec becomes flaky."
    )
    assert result1["start"] == result2["start"]
    assert result1["end"] == result2["end"]


def test_max_length_km_caps_corridor():
    """``max_length_km`` short-circuits ``found: false`` when the
    picked simple path exceeds the cap. The diagnostic must surface
    the actual simple-path length so the operator can adjust the cap
    knowing where they stand.
    """
    _ingest_connected_chain("maxlen")
    # The chain is ~350 m. Cap at 0.1 km (100 m) — the picked path
    # exceeds it → found: false.
    result = discover_corridor(
        bbox=_BBOX,
        sport=_SPORT,
        min_user_count=2,
        min_length_km=0.05,
        max_length_km=0.1,
    )
    assert result["found"] is False
    assert "reason" in result
    # Diagnostic surfaces the simple-path length so operators can tune
    # the cap.
    assert "simple_path_km" in result
    # And the reason must mention max_length_km so debugging is obvious.
    assert "max_length_km" in result["reason"]


def test_max_length_km_none_disables_cap():
    """Calling with ``max_length_km=None`` (or 0 via the CLI) does not
    apply any upper bound — equivalent to pre-PR behaviour.
    """
    _ingest_connected_chain("nomax")
    result = discover_corridor(
        bbox=_BBOX,
        sport=_SPORT,
        min_user_count=2,
        min_length_km=0.05,
        max_length_km=None,
    )
    assert result["found"] is True, (
        "max_length_km=None must NOT short-circuit; got: " + str(result)
    )
