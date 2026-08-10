"""Heat-edge disconnection metrics — the "spaghetti alert" backbone.

`compute_disconnection_metrics(...)` runs a single SQL query that
classifies every heat_edge in a bounding box and returns counts:

- `edges_total` — all edges in bbox, all sports.
- `grid_fallback` — `osm_way_id IS NULL` (NOT matched to an OSM way).
  When grid_fallback / edges_total is high in a populated area, your
  OSM coverage is missing or `group_edges_osm` hasn't run.
- `dangling_endpoint` — edge has at least one endpoint that no other
  edge in the same sport shares (within 5dp ≈ 1.1 m).
- `fully_isolated` — both endpoints unique. These are the "stub"
  edges that make the heatmap look like dropped pasta at z17+.

The metric semantics are identical to the 2026-05-14 spaghetti audit,
but the SQL was rewritten 2026-07 to O(n): the original used a
correlated subquery per endpoint against a non-materialized `deg` CTE
(the planner re-ran the whole degree aggregation per row — one region
took >29 min at 2.3M heat_edges). Now the degree-1 endpoints are
materialized once and hash-joined; each region completes in seconds.

Thresholds the build_pmtiles job uses for alerting:

- `grid_fallback_ratio > 0.20` in a populated bbox → action: import
  more OSM PBFs or re-run `group_edges_osm`.
- `fully_isolated_ratio > 0.05` → action: investigate ingest. Most
  likely the densifier or grid step changed.

Both are advisory — the function returns the raw counts and lets the
caller decide what to do.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session


@dataclass
class DisconnectionMetrics:
    """Spaghetti metric counts for a bounding box."""
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    sport: str | None  # None = all sports
    edges_total: int
    grid_fallback: int
    dangling_endpoint: int
    fully_isolated: int

    @property
    def grid_fallback_ratio(self) -> float:
        return self.grid_fallback / self.edges_total if self.edges_total else 0.0

    @property
    def dangling_ratio(self) -> float:
        return self.dangling_endpoint / self.edges_total if self.edges_total else 0.0

    @property
    def isolated_ratio(self) -> float:
        return self.fully_isolated / self.edges_total if self.edges_total else 0.0

    def as_log_dict(self) -> dict:
        """Flat dict for structured logging (one key per field)."""
        out = asdict(self)
        # Expand the bbox tuple so JSON consumers don't have to parse it
        out["bbox_min_lon"], out["bbox_min_lat"], out["bbox_max_lon"], out["bbox_max_lat"] = self.bbox
        out["grid_fallback_ratio"] = round(self.grid_fallback_ratio, 4)
        out["dangling_ratio"] = round(self.dangling_ratio, 4)
        out["isolated_ratio"] = round(self.isolated_ratio, 4)
        # bbox tuple isn't JSON-serializable as-is for some loggers
        del out["bbox"]
        return out


# Alert thresholds — see module docstring
ALERT_GRID_FALLBACK_RATIO = 0.20
ALERT_ISOLATED_RATIO = 0.05


# O(n) shape: materialize the edge set once, aggregate endpoint degrees
# once, keep only degree-1 endpoints (`lone`), then hash-LEFT-JOIN each
# edge's two endpoints against that small set. The previous version used
# correlated subqueries against a non-materialized `deg` CTE — the
# planner re-ran the full degree aggregation per edge (O(n²), >29 min
# for one region at 2.3M heat_edges).
_METRIC_SQL = """
WITH bbox AS (
    SELECT ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326) AS g
),
e AS MATERIALIZED (
    SELECT
        h.edge_key,
        h.osm_way_id,
        ROUND(ST_X(ST_StartPoint(h.geometry))::numeric, 5) AS sx,
        ROUND(ST_Y(ST_StartPoint(h.geometry))::numeric, 5) AS sy,
        ROUND(ST_X(ST_EndPoint(h.geometry))::numeric, 5)   AS ex,
        ROUND(ST_Y(ST_EndPoint(h.geometry))::numeric, 5)   AS ey
    FROM heat_edges h, bbox
    WHERE h.user_count >= 1
      AND ST_Intersects(h.geometry, bbox.g)
      AND (:sport IS NULL OR h.sport = :sport)
),
lone AS MATERIALIZED (
    SELECT x, y
    FROM (
        SELECT sx AS x, sy AS y FROM e
        UNION ALL
        SELECT ex, ey FROM e
    ) p
    GROUP BY x, y
    HAVING COUNT(*) = 1
),
flags AS (
    SELECT
        e.osm_way_id,
        (ls.x IS NOT NULL) AS s_lone,
        (le.x IS NOT NULL) AS e_lone
    FROM e
    LEFT JOIN lone ls ON ls.x = e.sx AND ls.y = e.sy
    LEFT JOIN lone le ON le.x = e.ex AND le.y = e.ey
)
SELECT
    COUNT(*) AS edges_total,
    COUNT(*) FILTER (WHERE osm_way_id IS NULL) AS grid_fallback,
    COUNT(*) FILTER (WHERE s_lone OR e_lone) AS dangling_endpoint,
    COUNT(*) FILTER (WHERE s_lone AND e_lone) AS fully_isolated
FROM flags
"""


def compute_disconnection_metrics(
    db: Session,
    bbox: tuple[float, float, float, float],
    sport: str | None = None,
) -> DisconnectionMetrics:
    """Count spaghetti / disconnected heat_edges in a bbox.

    `bbox` is (min_lon, min_lat, max_lon, max_lat) in WGS84.
    `sport` filters by partition — pass None to count across all sports.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    row = db.execute(sa_text(_METRIC_SQL), {
        "min_lon": min_lon, "min_lat": min_lat,
        "max_lon": max_lon, "max_lat": max_lat,
        "sport": sport,
    }).one()
    return DisconnectionMetrics(
        bbox=bbox,
        sport=sport,
        edges_total=row.edges_total or 0,
        grid_fallback=row.grid_fallback or 0,
        dangling_endpoint=row.dangling_endpoint or 0,
        fully_isolated=row.fully_isolated or 0,
    )


# Regions worth monitoring on every build_pmtiles run. Each entry is
# (label, bbox). Pick a populated area where we EXPECT good OSM
# coverage — a high grid_fallback_ratio there is genuinely alarming
# (not just "no PBF imported for Iceland").
MONITORED_REGIONS: list[tuple[str, tuple[float, float, float, float]]] = [
    ("montpellier_core", (3.85, 43.58, 3.95, 43.65)),
    ("anduze_corridor",  (3.95, 43.95, 4.10, 44.10)),
    ("lyon_core",        (4.75, 45.70, 4.95, 45.85)),
    ("marseille_core",   (5.30, 43.25, 5.50, 43.40)),
]
