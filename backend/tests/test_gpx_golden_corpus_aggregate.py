"""Golden (slow) — AGGREGATE ingestion quality across ~100 real cycling rides
of the enlarged Montpellier zone.

Paul's ask: *"je veux surtout qu'après nos tests sur l'ingestion de GPX, on ait
une heatmap propre et surtout CONTINUE (pas de fragmentation)... tu peux me faire
un test d'import sur une 100 d'activités de la zone Mtp élargie et comparer
ensuite? pass_count correct, continue, etc."*

Where the per-ride goldens (`test_gpx_golden_herault`, `..._near_dup_merge`,
`..._pass_count_pair`) pin ONE trace each, this one runs the REAL OSM-spatial
matcher (`_match_to_osm`, read-only) over the whole local cycling corpus
(`data/strava-export/activities/*.gpx` filtered to cycling + the Mtp bbox via
`activities.csv`) and asserts the quality holds IN AGGREGATE — no ride
fragments, the OSM-match ratio stays high, and the corridor rides genuinely
share OSM ways (so the heatmap accumulates, doesn't feather).

It is read-only (writes nothing, no cleanup) and `slow`+`golden` marked:
auto-skips when the occitanie PBF or the corpus dir is absent (CI has neither).
Source of the continuity definition: `test_gpx_golden_herault.py` (kept in sync).
"""
import csv
import glob
import json
import math
import os
import statistics

import pytest

from app.db.session import SessionLocal
from app.services import ingest
from app.services.gpx import parse_gpx
from tests.conftest import HERAULT_BBOX, osm_present_in_bbox

# Enlarged Montpellier zone (Hérault + Gard corridor up to Anduze/Cévennes).
_BBOX = HERAULT_BBOX  # (lat0, lon0, lat1, lon1)
_CONTINUITY_GAP_M = 50.0
_GRAPH_REVISIT_M = 20.0
# Cap the sample so the slow matcher (loads ~65k OSM segments/ride) stays
# bounded; we LOG the cap so a reader never mistakes a sample for the corpus.
_MAX_RIDES = int(os.environ.get("CORPUS_AGG_MAX_RIDES", "40"))


def _corpus_dir() -> str | None:
    for root in ("data/strava-export", "/app/data/strava-export",
                 os.path.join(os.path.dirname(__file__), "..", "..", "data", "strava-export")):
        if os.path.isdir(os.path.join(root, "activities")):
            return root
    return None


_CORPUS = _corpus_dir()

pytestmark = [
    pytest.mark.golden,
    pytest.mark.slow,
    pytest.mark.skipif(
        not (osm_present_in_bbox() and _CORPUS),
        reason="needs the occitanie OSM PBF + data/strava-export corpus mounted; CI has neither.",
    ),
]


def _cycling_gpx() -> list[str]:
    """Plain-GPX cycling rides (Ride / E-Bike Ride) from activities.csv that
    exist on disk. Falls back to every .gpx if the CSV is missing."""
    if not _CORPUS:
        return []
    csv_path = os.path.join(_CORPUS, "activities.csv")
    files: list[str] = []
    if os.path.exists(csv_path):
        with open(csv_path, newline="") as fh:
            for row in csv.DictReader(fh):  # real CSV parser — names contain commas
                if row.get("Activity Type") in ("Ride", "E-Bike Ride"):
                    fn = row.get("Filename", "")
                    if fn.endswith(".gpx"):
                        p = os.path.join(_CORPUS, fn)
                        if os.path.exists(p):
                            files.append(p)
    if not files:
        files = sorted(glob.glob(os.path.join(_CORPUS, "activities", "*.gpx")))
    return files


def _haversine_m(a, b) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi = math.radians(b[0] - a[0])
    dlam = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _endpoints(e):
    a = (e["a_lat"], e["a_lon"])
    b = (e["b_lat"], e["b_lon"])
    return (a, b) if e["is_canonical"] else (b, a)


def _in_bbox(coords) -> bool:
    lat0, lon0, lat1, lon1 = _BBOX
    return any(
        p and lon0 <= p[0] <= lon1 and lat0 <= p[1] <= lat1
        for p in coords[:: max(1, len(coords) // 20)]
    )


def _graph_continuity(edges) -> float:
    """Fraction of consecutive edge-pairs that meet — a gap counts as a TRUE
    break only if the jumped-to endpoint has no other edge endpoint within
    _GRAPH_REVISIT_M (else it's an out-and-back revisit, not a disconnection).
    Same definition as the single-ride golden."""
    if len(edges) < 3:
        return 1.0
    all_pts = []
    for e in edges:
        s, t = _endpoints(e)
        all_pts.append(s)
        all_pts.append(t)
    breaks = 0
    pairs = 0
    for i in range(len(edges) - 1):
        _, end = _endpoints(edges[i])
        nxt, _ = _endpoints(edges[i + 1])
        pairs += 1
        if _haversine_m(end, nxt) <= _CONTINUITY_GAP_M:
            continue
        # revisit check: is `nxt` near ANY other endpoint?
        revisit = any(
            p is not nxt and _haversine_m(nxt, p) <= _GRAPH_REVISIT_M for p in all_pts
        )
        if not revisit:
            breaks += 1
    return 1.0 - breaks / pairs if pairs else 1.0


def test_corpus_aggregate_quality():
    all_files = _cycling_gpx()
    assert all_files, "no cycling GPX found in the corpus"

    per_ride = []          # (name, spatial_ratio, continuity, max_edge_m, n_edges)
    way_riders: dict[int, set[str]] = {}  # osm_way_id -> set of ride names

    db = SessionLocal()
    try:
        considered = 0
        # Cap on MATCHED in-bbox rides (not on files scanned) — the corpus
        # spans many regions, so we keep matching until _MAX_RIDES Mtp-zone
        # rides are covered. Parsing is cheap; matching is the slow part.
        for path in all_files:
            if considered >= _MAX_RIDES:
                break
            name = os.path.basename(path)
            try:
                with open(path, "rb") as fh:
                    parsed = parse_gpx(fh.read())
            except Exception:
                continue
            if parsed.get("skip_reason"):
                continue
            coords = json.loads(parsed["geometry_geojson"])["coordinates"]
            if len(coords) < 50 or not _in_bbox(coords):
                continue
            sport = ingest._normalize_heat_edge_sport(parsed.get("sport_from_gpx") or "gravel")
            edges, fallback = ingest._match_to_osm(ingest._resegment_coords(coords), sport, db)
            if len(edges) < 50:
                continue
            considered += 1
            n_spatial = len(edges)
            grid = sum(1 for i in range(len(fallback) - 1)
                       if fallback[i] is not None and fallback[i + 1] is not None)
            spatial_ratio = n_spatial / (n_spatial + grid) if (n_spatial + grid) else 0.0
            max_edge = max(_haversine_m(*_endpoints(e)) for e in edges)
            cont = _graph_continuity(edges)
            per_ride.append((name, spatial_ratio, cont, max_edge, n_spatial))
            for e in edges:
                w = e.get("osm_way_id")
                if w is not None:
                    way_riders.setdefault(w, set()).add(name)
    finally:
        db.close()

    assert considered >= 10, f"only {considered} in-bbox cycling rides matched — corpus too thin"

    ratios = [r[1] for r in per_ride]
    conts = [r[2] for r in per_ride]
    max_edges = [r[3] for r in per_ride]
    shared_ways = {w: rs for w, rs in way_riders.items() if len(rs) >= 2}

    print(f"\n[corpus-agg] rides_considered={considered} (Mtp-zone matches; "
          f"corpus has {len(all_files)} cycling GPX; cap={_MAX_RIDES})")
    print(f"[corpus-agg] spatial_ratio median={statistics.median(ratios):.3f} "
          f"min={min(ratios):.3f}")
    print(f"[corpus-agg] graph_continuity median={statistics.median(conts):.4f} "
          f"min={min(conts):.4f}")
    print(f"[corpus-agg] max_edge_m p50={statistics.median(max_edges):.1f} "
          f"max={max(max_edges):.1f}")
    print(f"[corpus-agg] ways touched={len(way_riders)} shared_by>=2_rides={len(shared_ways)}")

    # (1) OSM-match holds in aggregate (no swathe of grid-fallback feathering).
    assert statistics.median(ratios) >= 0.85, (
        f"median OSM-match ratio {statistics.median(ratios):.3f} < 0.85 — the "
        "matcher is dropping rides to grid-fallback (feathering risk)"
    )
    # (2) Rides are CONTINUOUS in aggregate — no fragmentation. A human rode
    # these, so the graph-continuity floor is high.
    assert statistics.median(conts) >= 0.99, (
        f"median graph-continuity {statistics.median(conts):.4f} < 0.99 — rides "
        "fragmenting in aggregate"
    )
    assert min(conts) >= 0.90, f"a ride fragmented badly (min continuity {min(conts):.4f})"
    # (3) No long straight chords anywhere (each edge is a short OSM sub-seg).
    assert statistics.median(max_edges) <= 80.0, (
        f"median longest-edge {statistics.median(max_edges):.1f} m > 80 m — chord-bridging"
    )
    # (4) The corridor genuinely overlaps: many OSM ways are ridden by >=2
    # distinct rides → the heatmap accumulates (pass_count/user_count > 1)
    # instead of laying down parallel near-dupes. This is the per-point
    # osm_way_id fix paying off at scale (pre-fix, rides collapsed to ~2 ways
    # each so cross-ride way-sharing was meaningless).
    assert len(shared_ways) >= 20, (
        f"only {len(shared_ways)} OSM ways shared by >=2 rides — the corpus "
        "should overlap heavily on the Mtp road/trail network"
    )
