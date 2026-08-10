"""Discover a "candidate corridor" of high-`user_count` heat_edges in a bbox.

Test-only helper for the Layer-2 drag-edit E2E spec
(``e2e/tests/drag-edit-clapiers-heatmap.spec.ts``). NOT a prod API — the
Playwright spec invokes this via ``docker compose exec -T backend python
-m app.cli.discover_test_corridor ...`` and reads the resulting JSON.

See ``docs/drag-edit-e2e-testing-strategy.md`` for the full Layer-2 design.

## Usage

    python -m app.cli.discover_test_corridor \\
        --bbox 3.85,43.63,3.92,43.68 \\
        --sport offroad \\
        --min-user-count 2 \\
        --min-length-km 1.0 \\
        --max-length-km 5.0 \\
        --out /tmp/corridor.json

``--bbox`` is ``minLon,minLat,maxLon,maxLat`` (GeoJSON order).
``--min-user-count`` defaults to 2 (K-anonymity threshold in prod). Drop to
1 in dev when running against an under-populated local DB.
``--min-length-km`` defaults to 1.0 — enough room for a 150 m perpendicular
drag without snapping back to an endpoint.
``--max-length-km`` defaults to 5.0 — corridors longer than this become
unwieldy drag targets (a 300 km corner-to-corner "longest connected
component" is no good for a 150 m perpendicular drag). Pass 0 to
disable the cap.
``--out`` defaults to stdout; the test reads stdout when no path is set.

## Output shape

On success (``found: true``):

```json
{
    "found": true,
    "bbox": [3.85, 43.63, 3.92, 43.68],
    "sport": "offroad",
    "edge_keys": ["offroad/43.65,3.87/43.66,3.88", ...],
    "edge_count": 47,
    "total_length_km": 1.73,
    "max_user_count": 5,
    "geometry": {"type": "LineString", "coordinates": [...]},
    "start": [lon, lat],
    "end": [lon, lat],
    "midpoint": [lon, lat],
    "bearing_deg": 87.3
}
```

On no-match (``found: false``):

```json
{"found": false, "reason": "..."}
```

Exits 0 on ``found: true``, 1 on ``found: false`` — so the Playwright spec
can ``test.skip()`` cleanly when the local DB has no usable corridor.

## Algorithm

1. Pull every ``heat_edges`` row matching ``(sport, bbox, user_count >=
   min_user_count)``. We project the geometry to GeoJSON server-side
   (``ST_AsGeoJSON``) and grab the geography length in one pass.
2. Build a vertex → edge adjacency map keyed by snapped endpoint tuples
   (5dp, matching the ``_edge_key`` convention in ``ingest.py``).
3. Find connected components via BFS. Pick the component with the
   longest cumulative geography length.
4. Stitch its edges into a single ordered chain (or accept a forked
   chain — we pick the longest simple path through the component).
5. Derive ``start`` and ``end`` (the two simple-path endpoints), pick
   the geometric midpoint of the stitched LineString, and compute the
   great-circle bearing from ``start`` to ``end``.

The Clapiers bbox (``3.85,43.63,3.92,43.68``) is the canonical test
target. With a populated local DB the longest component is normally a
1-3 km segment of the Lez-towpath / Coulée-verte corridor.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections import deque

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Geo helpers ──────────────────────────────────────────────────────────────


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in metres. Inputs: (lon, lat)."""
    r = 6_371_000.0
    lon1, lat1 = a
    lon2, lat2 = b
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Initial great-circle bearing from a to b in degrees (0-360).
    Inputs: (lon, lat)."""
    lon1, lat1 = a
    lon2, lat2 = b
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlam)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _line_length_m(coords: list[list[float]]) -> float:
    """Total length of a [[lon, lat], ...] polyline in metres."""
    total = 0.0
    for i in range(1, len(coords)):
        total += _haversine_m(
            (coords[i - 1][0], coords[i - 1][1]),
            (coords[i][0], coords[i][1]),
        )
    return total


# ── Graph helpers ────────────────────────────────────────────────────────────


# 5dp ≈ 1 m. Same precision as `_edge_key` in `ingest.py` — we round here so
# tiny float noise in `ST_AsGeoJSON` output (which can emit > 5dp for some
# inputs) doesn't fracture connectivity.
_SNAP_DP = 5


def _snap(p: tuple[float, float]) -> tuple[float, float]:
    return (round(p[0], _SNAP_DP), round(p[1], _SNAP_DP))


def _edge_endpoints(coords: list[list[float]]) -> tuple[tuple[float, float], tuple[float, float]]:
    """First and last vertex of a polyline, snapped to 5dp."""
    return _snap((coords[0][0], coords[0][1])), _snap((coords[-1][0], coords[-1][1]))


def _components(
    edges: list[dict],
) -> list[list[int]]:
    """Connected components — returns list of edge-index lists.

    Two edges belong to the same component if they share a snapped
    endpoint. We index edges by both endpoints and BFS.
    """
    vertex_to_edges: dict[tuple[float, float], list[int]] = {}
    for idx, edge in enumerate(edges):
        a, b = edge["endpoints"]
        vertex_to_edges.setdefault(a, []).append(idx)
        vertex_to_edges.setdefault(b, []).append(idx)

    seen: set[int] = set()
    components: list[list[int]] = []
    for start_idx in range(len(edges)):
        if start_idx in seen:
            continue
        queue: deque[int] = deque([start_idx])
        comp: list[int] = []
        while queue:
            i = queue.popleft()
            if i in seen:
                continue
            seen.add(i)
            comp.append(i)
            a, b = edges[i]["endpoints"]
            for v in (a, b):
                for j in vertex_to_edges.get(v, []):
                    if j not in seen:
                        queue.append(j)
        components.append(comp)
    return components


def _longest_simple_path(
    edges: list[dict],
    edge_indices: list[int],
) -> list[int]:
    """Heuristic longest simple path through the subgraph defined by
    ``edge_indices``.

    Heat-edge components are small (rarely > 200 edges in a 1 km² area)
    and usually near-linear (a corridor) or branchy. We do a two-pass
    BFS:

    1. Pick any vertex with the highest degree-1 priority (a leaf of the
       subgraph). If no degree-1 vertex exists (loop), pick an arbitrary
       vertex.
    2. From that vertex, walk BFS labelling each vertex with the
       cumulative LENGTH of the heaviest path reaching it. The end of
       the heaviest path is the far endpoint.
    3. Re-run from the far endpoint to get the actual longest path
       (classic two-pass diameter algorithm on a tree). On non-tree
       components this is a heuristic — we accept whatever simple path
       BFS produces by tracking parents.

    Returns: ordered list of edge indices forming the chain.
    """
    if not edge_indices:
        return []

    edges_in_comp = set(edge_indices)
    # vertex → list of (edge_idx, other_endpoint, length_m)
    adj: dict[tuple[float, float], list[tuple[int, tuple[float, float], float]]] = {}
    for idx in edge_indices:
        a, b = edges[idx]["endpoints"]
        length = edges[idx]["length_m"]
        adj.setdefault(a, []).append((idx, b, length))
        adj.setdefault(b, []).append((idx, a, length))

    def _bfs_farthest(
        source: tuple[float, float],
    ) -> tuple[tuple[float, float], dict[tuple[float, float], tuple[float, int, tuple[float, float]] | None]]:
        """Return (far_vertex, parents) where ``parents[v]`` is
        ``(edge_idx_to_v, parent_vertex)`` or None for source. ``far_vertex``
        is the vertex with the largest cumulative length from ``source``."""
        # distance_m: vertex → cumulative length
        distance: dict[tuple[float, float], float] = {source: 0.0}
        parents: dict[tuple[float, float], tuple[float, int, tuple[float, float]] | None] = {source: None}
        # Visit edges, not vertices, to avoid revisiting the same edge.
        used_edges: set[int] = set()
        # Simple BFS by edge — since the graph is sparse and not weighted
        # in a Dijkstra sense (we just want the longest simple path
        # heuristic), this terminates after edge_count steps.
        queue: deque[tuple[float, float]] = deque([source])
        while queue:
            v = queue.popleft()
            for edge_idx, other, length in adj.get(v, []):
                if edge_idx in used_edges:
                    continue
                if edge_idx not in edges_in_comp:
                    continue
                new_dist = distance[v] + length
                if other not in distance or new_dist > distance[other]:
                    distance[other] = new_dist
                    parents[other] = (length, edge_idx, v)
                    used_edges.add(edge_idx)
                    queue.append(other)
        far_vertex = max(distance.items(), key=lambda kv: kv[1])[0]
        return far_vertex, parents

    # Pick a starting vertex: prefer a degree-1 leaf, else any.
    leaves = [v for v, e in adj.items() if len(e) == 1]
    start_vertex = leaves[0] if leaves else next(iter(adj.keys()))

    # First pass: find one end of the diameter.
    far1, _ = _bfs_farthest(start_vertex)
    # Second pass: from far1, find the actual farthest vertex.
    far2, parents = _bfs_farthest(far1)

    # Reconstruct path from far2 back to far1.
    path_edge_indices: list[int] = []
    cur: tuple[float, float] | None = far2
    while cur is not None and parents.get(cur) is not None:
        parent_info = parents[cur]
        assert parent_info is not None
        _, edge_idx, parent_v = parent_info
        path_edge_indices.append(edge_idx)
        cur = parent_v
    path_edge_indices.reverse()
    return path_edge_indices


def _stitch_chain(
    edges: list[dict],
    chain_indices: list[int],
) -> tuple[list[list[float]], tuple[float, float], tuple[float, float]]:
    """Walk the ordered ``chain_indices`` and assemble one continuous
    [[lon, lat], ...] polyline. Returns (stitched_coords, start_vertex,
    end_vertex). Each consecutive edge pair must share an endpoint —
    flips the orientation of edges that don't align."""
    if not chain_indices:
        return [], (0.0, 0.0), (0.0, 0.0)

    # Choose the orientation of edge 0 by checking which endpoint is
    # shared with edge 1 (if any).
    coords: list[list[float]] = []
    e0_coords = edges[chain_indices[0]]["coords"]
    e0_start, e0_end = edges[chain_indices[0]]["endpoints"]

    if len(chain_indices) == 1:
        coords = list(e0_coords)
        return coords, e0_start, e0_end

    e1_start, e1_end = edges[chain_indices[1]]["endpoints"]
    if e0_end in (e1_start, e1_end):
        # forward orientation
        coords = list(e0_coords)
        prev_endpoint = e0_end
    elif e0_start in (e1_start, e1_end):
        coords = list(reversed(e0_coords))
        prev_endpoint = e0_start
    else:
        # Shouldn't happen — the chain came from BFS over shared
        # endpoints. Bail safely: emit edge 0 forward and rely on
        # downstream length metric.
        coords = list(e0_coords)
        prev_endpoint = e0_end

    chain_start = coords[0]
    chain_start_vertex = _snap((chain_start[0], chain_start[1]))

    for idx in chain_indices[1:]:
        a, b = edges[idx]["endpoints"]
        c = edges[idx]["coords"]
        if a == prev_endpoint:
            coords.extend(c[1:])  # skip shared vertex
            prev_endpoint = b
        elif b == prev_endpoint:
            coords.extend(list(reversed(c))[1:])
            prev_endpoint = a
        else:
            # Disconnect — append a discontinuity. This shouldn't happen
            # if the chain comes from the BFS, but stay defensive.
            coords.extend(c)
            prev_endpoint = b

    chain_end_vertex = prev_endpoint
    return coords, chain_start_vertex, chain_end_vertex


def _polyline_midpoint(coords: list[list[float]]) -> list[float]:
    """Vertex closest to half the total length along the polyline."""
    if not coords:
        return [0.0, 0.0]
    if len(coords) == 1:
        return list(coords[0])
    total = _line_length_m(coords)
    half = total / 2.0
    walked = 0.0
    for i in range(1, len(coords)):
        seg = _haversine_m(
            (coords[i - 1][0], coords[i - 1][1]),
            (coords[i][0], coords[i][1]),
        )
        if walked + seg >= half:
            # Interpolate between coords[i-1] and coords[i]
            remaining = half - walked
            t = remaining / seg if seg > 0 else 0.0
            lon = coords[i - 1][0] + t * (coords[i][0] - coords[i - 1][0])
            lat = coords[i - 1][1] + t * (coords[i][1] - coords[i - 1][1])
            return [lon, lat]
        walked += seg
    return list(coords[-1])


# ── Core query ───────────────────────────────────────────────────────────────


def discover_corridor(
    bbox: tuple[float, float, float, float],
    sport: str,
    min_user_count: int,
    min_length_km: float,
    max_length_km: float | None = None,
) -> dict:
    """Returns the corridor descriptor or ``{"found": false, "reason": ...}``.

    ``max_length_km`` caps the simple-path length: when the picked path
    exceeds the cap, the function returns ``found: false`` (the
    Playwright spec relies on a tight corridor for drag-edit; a 300-km
    "corridor" stitched corner-to-corner of the bbox would produce a
    nonsense drag target).
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    db = SessionLocal()
    try:
        # `ORDER BY edge_key` is mandatory: BFS over the loaded edges
        # picks the corridor deterministically only if the input row
        # order is stable. Without it, two runs against the same DB
        # can produce different corridors (and thus different test
        # selectors / midpoints) — pure noise in the Playwright spec.
        rows = db.execute(
            sa_text(
                """
                SELECT
                    edge_key,
                    user_count,
                    pass_count,
                    ST_AsGeoJSON(geometry) AS geom_json,
                    ST_Length(geometry::geography) AS length_m
                FROM heat_edges
                WHERE sport = :sport
                  AND user_count >= :min_uc
                  AND ST_Intersects(
                      geometry,
                      ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
                  )
                ORDER BY edge_key
                """
            ),
            {
                "sport": sport,
                "min_uc": min_user_count,
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat,
            },
        ).fetchall()
    finally:
        db.close()

    if not rows:
        return {
            "found": False,
            "reason": (
                f"no heat_edges in bbox={bbox} sport={sport} "
                f"with user_count >= {min_user_count}"
            ),
        }

    edges: list[dict] = []
    for row in rows:
        try:
            geom = json.loads(row.geom_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        endpoints = _edge_endpoints(coords)
        edges.append({
            "edge_key": row.edge_key,
            "user_count": int(row.user_count or 0),
            "pass_count": int(row.pass_count or 0),
            "coords": coords,
            "endpoints": endpoints,
            "length_m": float(row.length_m or 0.0),
        })

    if not edges:
        return {
            "found": False,
            "reason": "all matching heat_edges had unparseable geometry",
        }

    components = _components(edges)
    # Pick the component with the largest cumulative length.
    def _comp_length(comp: list[int]) -> float:
        return sum(edges[i]["length_m"] for i in comp)

    components.sort(key=_comp_length, reverse=True)
    best_component = components[0]
    best_length_m = _comp_length(best_component)
    min_length_m = min_length_km * 1000.0

    if best_length_m < min_length_m:
        return {
            "found": False,
            "reason": (
                f"longest connected component is {best_length_m / 1000:.2f} km "
                f"< min_length_km={min_length_km}"
            ),
            "longest_component_km": round(best_length_m / 1000, 3),
            "component_edge_count": len(best_component),
            "component_count": len(components),
        }

    chain = _longest_simple_path(edges, best_component)
    stitched_coords, start_vertex, end_vertex = _stitch_chain(edges, chain)

    if len(stitched_coords) < 2:
        return {
            "found": False,
            "reason": "longest simple path produced empty geometry",
        }

    chain_length_m = _line_length_m(stitched_coords)
    if chain_length_m < min_length_m:
        return {
            "found": False,
            "reason": (
                f"longest simple path within component is {chain_length_m / 1000:.2f} km "
                f"< min_length_km={min_length_km}"
            ),
            "component_length_km": round(best_length_m / 1000, 3),
            "simple_path_km": round(chain_length_m / 1000, 3),
        }
    if max_length_km is not None and chain_length_m / 1000.0 > max_length_km:
        # Picked path is too long — likely corner-to-corner of the
        # bbox, which makes for a misleading drag target. Bail rather
        # than silently emit a "corridor" the spec can't sensibly
        # interact with. Trimming the path symmetrically is a future
        # improvement; for now `found: false` is the safe answer.
        return {
            "found": False,
            "reason": (
                f"longest simple path within component is {chain_length_m / 1000:.2f} km "
                f"> max_length_km={max_length_km}"
            ),
            "component_length_km": round(best_length_m / 1000, 3),
            "simple_path_km": round(chain_length_m / 1000, 3),
        }

    midpoint = _polyline_midpoint(stitched_coords)
    start_lonlat = (stitched_coords[0][0], stitched_coords[0][1])
    end_lonlat = (stitched_coords[-1][0], stitched_coords[-1][1])
    bearing = _bearing_deg(start_lonlat, end_lonlat)

    edge_keys = [edges[i]["edge_key"] for i in chain]
    max_user_count = max(edges[i]["user_count"] for i in chain)

    return {
        "found": True,
        "bbox": list(bbox),
        "sport": sport,
        "edge_keys": edge_keys,
        "edge_count": len(chain),
        "total_length_km": round(chain_length_m / 1000, 3),
        "max_user_count": max_user_count,
        "geometry": {
            "type": "LineString",
            "coordinates": [[c[0], c[1]] for c in stitched_coords],
        },
        "start": [start_lonlat[0], start_lonlat[1]],
        "end": [end_lonlat[0], end_lonlat[1]],
        "midpoint": [midpoint[0], midpoint[1]],
        "bearing_deg": round(bearing, 2),
        "_diagnostics": {
            "component_count": len(components),
            "best_component_edge_count": len(best_component),
            "component_total_length_km": round(best_length_m / 1000, 3),
            "start_vertex_5dp": list(start_vertex),
            "end_vertex_5dp": list(end_vertex),
        },
    }


# ── CLI entry point ──────────────────────────────────────────────────────────


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "bbox must be 'minLon,minLat,maxLon,maxLat' (4 comma-separated floats)"
        )
    try:
        nums = tuple(float(p) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"bbox values must be floats: {exc}") from exc
    min_lon, min_lat, max_lon, max_lat = nums
    if min_lon >= max_lon or min_lat >= max_lat:
        raise argparse.ArgumentTypeError(
            "bbox must have min < max for both lon and lat"
        )
    return (min_lon, min_lat, max_lon, max_lat)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover a connected high-user_count heat_edges corridor in a bbox.",
    )
    parser.add_argument(
        "--bbox",
        required=True,
        type=_parse_bbox,
        help="minLon,minLat,maxLon,maxLat (e.g. '3.85,43.63,3.92,43.68')",
    )
    parser.add_argument(
        "--sport",
        default="offroad",
        help="heat_edges partition (road/gravel/mtb/offroad/running; default offroad)",
    )
    parser.add_argument(
        "--min-user-count",
        type=int,
        default=2,
        help="minimum user_count per edge (default 2 = prod K-anonymity)",
    )
    parser.add_argument(
        "--min-length-km",
        type=float,
        default=1.0,
        help="minimum cumulative chain length in km (default 1.0)",
    )
    parser.add_argument(
        "--max-length-km",
        type=float,
        default=5.0,
        help=(
            "maximum cumulative chain length in km (default 5.0). "
            "Capped to keep the drag target tight; a 300 km corridor "
            "stitched corner-to-corner of the bbox is no good for "
            "a 150 m perpendicular drag. Pass 0 to disable the cap."
        ),
    )
    parser.add_argument(
        "--out",
        help="write JSON to this path (default: stdout)",
    )
    args = parser.parse_args(argv)

    if args.min_user_count < 1:
        parser.error("--min-user-count must be >= 1")
    if args.min_length_km <= 0:
        parser.error("--min-length-km must be > 0")
    if args.max_length_km < 0:
        parser.error("--max-length-km must be >= 0 (0 disables the cap)")
    if args.max_length_km and args.max_length_km < args.min_length_km:
        parser.error(
            "--max-length-km must be >= --min-length-km (or 0 to disable)"
        )

    result = discover_corridor(
        bbox=args.bbox,
        sport=args.sport,
        min_user_count=args.min_user_count,
        min_length_km=args.min_length_km,
        max_length_km=args.max_length_km or None,
    )

    payload = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        logger.info("Wrote corridor JSON to %s", args.out)
    else:
        # ALWAYS print to stdout for the Playwright spec.
        print(payload)

    return 0 if result.get("found") else 1


if __name__ == "__main__":
    sys.exit(main())
