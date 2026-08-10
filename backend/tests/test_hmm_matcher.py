"""Unit pins for the HMM/Viterbi spatial map-matcher (Newson-Krumm).

Drives the REAL `_viterbi_point_matches` / `_candidate_segments` /
`_build_way_adjacency` / `_hmm_transition_cost` with SYNTHETIC in-memory OSM
segments — no DB, no PBF, so these run everywhere (incl. CI). They pin the two
behaviours that motivated the matcher (the maintainer's brief: "model the whole
trajectory, not each point in isolation"):

  1. PARALLEL-FEATURE REJECTION — a GPS trace on a road, noisy toward a parallel
     cycleway, must stay on the ROAD way at every point. The legacy per-point
     nearest-snap flips to the cycleway wherever a noisy point is momentarily
     closer; the Viterbi transition term (hopping to a disconnected parallel way
     is hugely penalised) keeps the whole trajectory committed to the road.
  2. APEX / OFF-RADIUS TOLERANCE — a point that drifts beyond the legacy 15 m
     hard radius (but within the wider search radius) stays MATCHED instead of
     breaking the run to grid-fallback.
"""

from app.services import ingest

_M_PER_DEG_LAT = ingest._DEG_TO_M  # 111320


def _lat_off(meters: float) -> float:
    return meters / _M_PER_DEG_LAT


def _road_segments(way_id: int, lat: float, n: int = 10) -> list:
    """A straight W→E way at `lat`, n sub-segments of ~0.001° lon (~80 m) each,
    consecutive sub-segments sharing exact nodes (so same-way topology holds)."""
    segs = []
    for i in range(n):
        lon1 = 3.8000 + i * 0.001
        lon2 = 3.8000 + (i + 1) * 0.001
        segs.append(ingest._OsmSegment(lon1, lat, lon2, lat, "asphalt", "tertiary", osm_way_id=way_id, seg_idx=i))
    return segs


def test_candidate_segments_returns_closest_per_way():
    road = _road_segments(1, 43.6000)
    cycle = _road_segments(2, 43.6000 + _lat_off(12))  # parallel, 12 m north
    grid = ingest._build_segment_grid(road + cycle)
    # a point on the road, well within search radius of both ways
    cands = ingest._candidate_segments(3.8035, 43.6000, grid, max_dist_m=30.0)
    by_way = {seg.osm_way_id: dist for seg, dist in cands}
    assert set(by_way) == {1, 2}, "both parallel ways should be candidates within 30 m"
    assert by_way[1] < 1.0, "road (on the point) should be ~0 m away"
    assert 10.0 < by_way[2] < 14.0, "cycleway should be ~12 m away"


def test_adjacency_connects_shared_node_not_parallel():
    road = _road_segments(1, 43.6000)
    cycle = _road_segments(2, 43.6000 + _lat_off(12))
    # a 3rd way that STARTS at the road's shared node (3.803, 43.6) → connected
    spur = [ingest._OsmSegment(3.8030, 43.6000, 3.8030, 43.6000 + _lat_off(50), "asphalt", "tertiary", osm_way_id=3, seg_idx=0)]
    adj = ingest._build_way_adjacency(road + cycle + spur)
    assert 3 in adj.get(1, set()), "spur sharing the road's node must be connected to the road"
    assert 2 not in adj.get(1, set()), "the parallel cycleway shares NO node → must NOT be connected"


def test_viterbi_rejects_parallel_cycleway():
    """Trajectory along the road, GPS noise biasing several points toward (and
    momentarily closer to) the parallel cycleway 12 m north. Every matched point
    must stay on the ROAD (way 1)."""
    road = _road_segments(1, 43.6000)
    cycle = _road_segments(2, 43.6000 + _lat_off(12))
    grid = ingest._build_segment_grid(road + cycle)
    adj = ingest._build_way_adjacency(road + cycle)

    # 9 points W→E; lateral noise (north, toward the cycleway) up to ~9 m — so a
    # naive nearest-snap would flip to the cycleway at the noisiest points.
    noise_m = [0, 3, 7, 9, 5, 8, 2, 6, 4]
    coords = [[3.8005 + i * 0.001, 43.6000 + _lat_off(noise_m[i])] for i in range(9)]

    pm = ingest._viterbi_point_matches(coords, "gravel", grid, adj)
    assert all(seg is not None for seg in pm), "all points are within range of the road"
    ways = {seg.osm_way_id for seg in pm}
    assert ways == {1}, f"trajectory must stay on the road (way 1), got ways={ways}"

    # Contrast: the legacy per-point nearest-snap DOES flip to the cycleway at
    # the points that are momentarily closer to it — proving the Viterbi fixed a
    # real failure, not a no-op.
    flipped = 0
    for c in coords:
        seg, _ = ingest._nearest_osm_segment(float(c[0]), float(c[1]), grid, max_dist_m=30.0)
        if seg and seg.osm_way_id == 2:
            flipped += 1
    assert flipped > 0, "nearest-snap should have flipped at least one point to the cycleway"


def test_viterbi_tolerates_off_radius_apex():
    """A single point drifts 22 m off the way — beyond the legacy 15 m hard
    radius — but the trajectory keeps it MATCHED (no run break)."""
    road = _road_segments(1, 43.6000)
    grid = ingest._build_segment_grid(road)
    adj = ingest._build_way_adjacency(road)

    coords = [
        [3.8015, 43.6000],
        [3.8025, 43.6000 + _lat_off(22)],  # apex drift, 22 m north of the way
        [3.8035, 43.6000],
    ]
    pm = ingest._viterbi_point_matches(coords, "gravel", grid, adj)
    assert all(seg is not None and seg.osm_way_id == 1 for seg in pm), (
        "the 22 m apex point must stay matched to the way (Viterbi tolerance), "
        f"got {[None if s is None else s.osm_way_id for s in pm]}"
    )
    # The legacy 15 m nearest-snap drops that apex point → run break → fallback.
    seg, _ = ingest._nearest_osm_segment(3.8025, 43.6000 + _lat_off(22), grid, max_dist_m=15.0)
    assert seg is None, "legacy 15 m radius should NOT match the 22 m-off apex point"


def test_transition_cost_orders_same_connected_disconnected():
    """The transition term: same way ≈ 0 < connected junction < disconnected."""
    a = ingest._OsmSegment(0, 0, 0.001, 0, "x", "y", osm_way_id=1)
    same = ingest._OsmSegment(0.001, 0, 0.002, 0, "x", "y", osm_way_id=1)
    conn = ingest._OsmSegment(0.001, 0, 0.001, 0.001, "x", "y", osm_way_id=2)
    far = ingest._OsmSegment(5, 5, 5.001, 5, "x", "y", osm_way_id=9)
    adj = {1: {2}, 2: {1}}
    c_same = ingest._hmm_transition_cost(a, same, adj)
    c_conn = ingest._hmm_transition_cost(a, conn, adj)
    c_disc = ingest._hmm_transition_cost(a, far, adj)
    assert c_same == 0.0
    assert 0.0 < c_conn < c_disc
    assert c_disc >= ingest._HMM_DISCONNECTED_PENALTY_M / ingest._HMM_BETA_M - 1e-9
