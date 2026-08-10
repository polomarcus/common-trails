"""Single source of truth for the by-OSM-way heatmap aggregation SQL.

WHY this module exists: the "GROUP BY (osm_way_id, sport) + LATERAL-join
osm_road_edges for the smooth ``way_geometry`` + K-anon + grid-fallback
filtering + bucket/heat_score" aggregation used to be DUPLICATED in three
places — ``app/jobs/refresh_matview.py`` (the now-deleted
``heat_edges_display`` matview), ``app/jobs/build_pmtiles.py`` (the static
PMTiles export), and ``app/api/heatmap.py`` (the live MVT tile endpoint,
z11+). Three copies drift; a fix in one silently leaves the others wrong.

This builder returns the shared **CTE chain** (``heat`` → ``osm_grouped`` →
``osm_matched`` → ``grid_fallback`` → ``combined``). Each caller wraps it
with its own outer SELECT:

* ``build_pmtiles.export_geojson`` → ``json_build_object(... ST_AsGeoJSON ...)``
  emitting newline-delimited GeoJSON, whole-world (no bbox).
* ``heatmap._generate_tile`` (z11+) → ``ST_AsMVT(ST_AsMVTGeom(...))`` with the
  request's bbox ``&&`` predicate spliced in.

The ``combined`` CTE always exposes the SAME superset of columns so both
callers can ``SELECT`` what they need:

    geometry, sport, user_count, pass_count, forward_count,
    backward_count, highway_type, bucket

Aggregation semantics reconciled from the three former copies:

* ``user_count`` → ``MAX`` (K-anonymity: distinct riders on the busiest
  sub-segment; all three copies already agreed).
* ``pass_count`` / ``forward_count`` / ``backward_count`` → ``MAX`` of the
  sub-edges of one OSM way. ``build_pmtiles`` used ``MAX`` (a SUM over the
  ~11 m sub-edges inflated the tooltip to nonsense like "412k passages",
  see ``tests/test_pmtiles_aggregation.py``). The old matview SUMmed
  forward/backward, but it was a rarely-hit fallback and the SUM had no
  meaningful tooltip interpretation; ``MAX`` is the correct, consistent
  semantic and is what now ships everywhere.

The K-anon, grid-fallback length cap, and grid-fallback confirmation
thresholds are all parameters so the two callers (and tests) can dial them.

SECURITY: ``bbox_predicate`` and ``extra_predicate`` are interpolated, NOT
bound — pass only trusted, code-generated strings (a parameter
placeholder like ``:lon_min`` or a fixed test sport). Numeric thresholds
are also interpolated; pass ints/floats, never user input.
"""
from __future__ import annotations

import os

# ── Desire-lines display policy (Paul, 2026-07-20) ──────────────────────────
# "j'ai pas envie de perdre les lignes de désir" — off-OSM traces
# (grid-fallback edges, ``osm_way_id IS NULL``) are REAL signal: MTB
# singletracks / DFCI paths that simply don't exist in OSM. The static
# PMTiles export used to DROP them by default and the live MVT required
# ``user_count >= 2``, which hid every single-user desire line in the K=1
# beta. Both display readers now resolve the policy through THIS helper so
# they cannot drift.
GRID_FALLBACK_KEEP_ENV = "HEATMAP_KEEP_GRID_FALLBACK"
GRID_FALLBACK_MIN_UC_ENV = "HEATMAP_GRID_FALLBACK_MIN_UC"


def resolve_grid_fallback_display(effective_min_uc: int) -> tuple[bool, int]:
    """SSOT resolution of the grid-fallback ("desire lines") display policy.

    Returns ``(drop_grid_fallback, grid_fallback_min_uc)`` for the two
    display readers (the static PMTiles export + the live MVT fallback).

    * ``HEATMAP_KEEP_GRID_FALLBACK`` (default **true**) — keep off-OSM
      desire lines on the heatmap. ``false``/``0``/``no``/``off`` drops
      them (the pre-2026-07-20 Komoot-quality behaviour).
    * ``HEATMAP_GRID_FALLBACK_MIN_UC`` (default: empty → follow
      ``effective_min_uc``, i.e. the K-anonymity floor — 1 in the K=1
      beta so solo singletracks SHOW; automatically 2 when K flips to 2).

    The 60 m grid-fallback length cap is NOT governed here — it stays a
    fixed anti-GPS-jump guard at the call sites. Accepted tradeoff:
    GPS-noise fragments ≤60 m may render (the Strava-like imperfection
    Paul explicitly accepts).
    """
    keep_raw = os.environ.get(GRID_FALLBACK_KEEP_ENV, "true").strip().lower()
    keep = keep_raw not in ("false", "0", "no", "off")
    min_uc_raw = os.environ.get(GRID_FALLBACK_MIN_UC_ENV, "").strip()
    grid_min_uc = int(min_uc_raw) if min_uc_raw else max(1, int(effective_min_uc))
    return (not keep), grid_min_uc


# Heat-score expression shared by every display path. Boosts
# primary/secondary/trunk roads so they read warmer-coloured (Komoot-style
# bright pink/orange on D-roads) than residential lanes at equal user_count.
# Operates on the ``user_count`` + ``highway_type`` columns of the
# ``combined`` CTE.
HEAT_SCORE_SQL = (
    "ROUND(LEAST(1.0, "
    "LN(1 + user_count) / (8.0 * LN(2)) "
    "+ CASE WHEN highway_type IN ('primary', 'secondary', 'trunk') THEN 0.15 "
    "WHEN highway_type IN ('tertiary', 'unclassified') THEN 0.05 "
    "ELSE 0 END"
    ")::numeric, 3)"
)

# Popularity bucket (1-5) by user_count — used by the legacy matview shape /
# its test. Operates on the ``user_count`` column of the ``combined`` CTE.
BUCKET_SQL = (
    "CASE WHEN user_count <= 2 THEN 1 "
    "WHEN user_count <= 5 THEN 2 "
    "WHEN user_count <= 15 THEN 3 "
    "WHEN user_count <= 50 THEN 4 "
    "ELSE 5 END"
)


def build_heat_aggregation_sql(
    *,
    min_uc: int = 1,
    bbox_predicate: str | None = None,
    extra_predicate: str = "TRUE",
    grid_fallback_min_uc: int = 2,
    max_grid_fallback_m: float | None = 60.0,
    drop_grid_fallback: bool = False,
    way_ids_param: str | None = None,
) -> str:
    """Build the shared by-OSM-way aggregation CTE chain.

    Returns SQL of the form ``WITH heat AS (...), osm_grouped AS (...),
    osm_matched AS (...), grid_fallback AS (...), combined AS (...)`` — NOT a
    complete statement. The caller appends a leading comma + its own CTE /
    outer ``SELECT ... FROM combined``.

    Args:
        min_uc: K-anonymity floor — only ``heat_edges`` with
            ``user_count >= min_uc`` enter the aggregation.
        bbox_predicate: optional spatial filter spliced into the ``heat``
            CTE WHERE (e.g. ``"he.geometry && ST_MakeEnvelope(:lon_min,
            :lat_min, :lon_max, :lat_max, 4326)"``). ``None`` = whole world
            (PMTiles).
        extra_predicate: AND-ed into the ``heat`` CTE WHERE. Defaults to
            ``TRUE``. Tests pass a fixed scoping clause like
            ``"he.sport = '_mvt_abc'"``. Interpolated — keep it trusted.
        grid_fallback_min_uc: grid-fallback edges (``osm_way_id IS NULL``)
            need ``user_count >= grid_fallback_min_uc`` to survive (drops
            single-trace GPS noise — the "purple discontinue" halo).
        max_grid_fallback_m: drop grid-fallback edges longer than this many
            metres (GPS-jump artefacts that render as field-crossing
            straight lines). ``None`` disables the length cap.
        drop_grid_fallback: when ``True`` the grid-fallback CTE returns
            nothing (Komoot-quality default for PMTiles whole-world build —
            only OSM-matched smooth ways survive).

    The ``combined`` CTE columns (same in every branch):
        ``osm_way_id, geometry, sport, user_count, pass_count, forward_count,
        backward_count, highway_type``.

    ``osm_way_id`` is exposed (``NULL`` for grid-fallback rows) so the
    ``heat_edges_agg`` write path (``app/jobs/rebuild_heat_agg.py`` +
    ``ingest._recompute_heat_agg_for_ways``) can key the upsert on it. The
    display readers ignore the extra column (they SELECT by name).

    Args (in addition to the ones above):
        way_ids_param: name of a BOUND parameter (e.g. ``"agg_way_ids"``)
            carrying a ``list[int]`` of ``osm_way_id``s. When set, the
            ``heat`` CTE is scoped to those ways (``AND he.osm_way_id =
            ANY(:<param>)``) — used by the per-way incremental recompute so
            the SAME aggregation definition produces both the whole-world
            build and the per-activity refresh (ONE definition = zero
            drift). ``None`` = whole world. Grid-fallback rows (NULL
            ``osm_way_id``) never match the filter, which is correct — the
            recompute only maintains OSM-matched agg rows.
    """
    bbox_clause = f"AND ({bbox_predicate})" if bbox_predicate else ""

    # Grid-fallback length cap. Spliced into the ``heat`` CTE so it applies
    # uniformly (an over-length grid edge is dropped before it can become a
    # feature). OSM-matched edges (osm_way_id NOT NULL) are never length-capped.
    if max_grid_fallback_m is not None:
        length_cap = (
            f"AND NOT (he.osm_way_id IS NULL "
            f"AND ST_Length(he.geometry::geography) > {max_grid_fallback_m})"
        )
    else:
        length_cap = ""

    way_ids_clause = (
        f"AND he.osm_way_id = ANY(:{way_ids_param})" if way_ids_param else ""
    )

    grid_fallback_keep = "FALSE" if drop_grid_fallback else "TRUE"

    return f"""
        WITH heat AS (
            SELECT he.osm_way_id, he.sport, he.geometry,
                he.user_count, he.pass_count,
                he.forward_count, he.backward_count
            FROM heat_edges he
            WHERE he.user_count >= {min_uc}
              {bbox_clause}
              {length_cap}
              {way_ids_clause}
              AND ({extra_predicate})
              AND NOT (he.osm_way_id IS NULL AND he.user_count < {grid_fallback_min_uc})
        ),
        osm_grouped AS (
            -- Cheap: GROUP BY the indexed bigint osm_way_id (+ sport, since
            -- the same OSM way can appear in multiple sport partitions).
            -- MAX (not SUM) for every count: pass/forward/backward are
            -- "how busy is this segment"; summing across the ~11 m sub-edges
            -- of one OSM way inflates the tooltip to nonsense (412k passages
            -- on a path whose busiest sub-segment saw 286). MAX = the busiest
            -- sub-segment, which is what the tooltip should show.
            SELECT osm_way_id, sport,
                MAX(user_count)::int AS user_count,
                MAX(pass_count)::int AS pass_count,
                MAX(forward_count)::int AS forward_count,
                MAX(backward_count)::int AS backward_count,
                ST_LineMerge(ST_Collect(geometry)) AS heat_geom
            FROM heat
            WHERE osm_way_id IS NOT NULL
            GROUP BY osm_way_id, sport
        ),
        osm_matched AS (
            -- The smooth multi-point line for the WHOLE OSM way lives in the
            -- `osm_ways` side-table since migration 0056 (stored once per
            -- way; pre-0056 it was a per-segment `way_geometry` column) —
            -- LEFT JOIN by PK. The LATERAL on osm_road_edges still resolves
            -- `highway` + the per-segment `geometry` fallback for ways that
            -- have segments but no osm_ways row (adhoc/test inserts).
            -- COALESCE falls back to the merged heat_edge geometry when the
            -- substrate has no row at all (local dev without imported PBF) —
            -- one feature per way instead of N criss-crossing 2-point
            -- segments (audit 2026-05-29 SP-S2-7).
            SELECT
                g.osm_way_id,
                COALESCE(w.way_geometry, o.geometry, g.heat_geom) AS geometry,
                g.sport, g.user_count, g.pass_count,
                g.forward_count, g.backward_count,
                COALESCE(o.highway, 'unknown') AS highway_type
            FROM osm_grouped g
            LEFT JOIN osm_ways w ON w.osm_way_id = g.osm_way_id
            LEFT JOIN LATERAL (
                SELECT o2.geometry, o2.highway
                FROM osm_road_edges o2
                WHERE o2.osm_way_id = g.osm_way_id
                ORDER BY o2.segment_idx
                LIMIT 1
            ) o ON true
        ),
        grid_fallback AS (
            -- Grid-fallback edges stay at heat_edge granularity; they have no
            -- OSM way to project onto. The length cap + confirmation filter
            -- already ran in the `heat` CTE. With drop_grid_fallback=True this
            -- returns nothing (Komoot-quality whole-world PMTiles).
            SELECT NULL::bigint AS osm_way_id, geometry, sport, user_count,
                pass_count, forward_count, backward_count,
                'unknown'::text AS highway_type
            FROM heat
            WHERE osm_way_id IS NULL
              AND {grid_fallback_keep}
        ),
        combined AS (
            SELECT osm_way_id, geometry, sport, user_count, pass_count,
                forward_count, backward_count, highway_type FROM osm_matched
            UNION ALL
            SELECT osm_way_id, geometry, sport, user_count, pass_count,
                forward_count, backward_count, highway_type FROM grid_fallback
        )
    """


def build_agg_read_sql(
    *,
    min_uc: int = 1,
    bbox_predicate: str | None = None,
    extra_predicate: str = "TRUE",
    grid_fallback_min_uc: int = 2,
    max_grid_fallback_m: float | None = 60.0,
    drop_grid_fallback: bool = False,
) -> str:
    """Build the DISPLAY-READ CTE chain that serves the SAME ``combined``
    shape as :func:`build_heat_aggregation_sql`, but reading the OSM-matched
    half from the pre-materialised ``heat_edges_agg`` table instead of
    re-running the heavy GROUP BY over ``heat_edges``.

    This is the OOM fix: the whole-world PMTiles build no longer executes the
    ~5 M-row aggregation at build time — it reads ``heat_edges_agg`` (one row
    per ``(osm_way_id, sport)``, ~45 k rows) with a plain indexed SELECT.

    The grid-fallback half is NOT materialised (see migration 0057 for the
    rationale). It is read LIVE from ``heat_edges`` here, mirroring the same
    grid-fallback semantics as the whole-world builder (length cap + the
    ``max(min_uc, grid_fallback_min_uc)`` confirmation floor). When
    ``drop_grid_fallback=True`` (the prod PMTiles path) the grid CTEs are
    omitted entirely → ZERO ``heat_edges`` scan → no OOM. The OSM-matched
    half — the part that actually needs the heavy aggregation — has a single
    source of truth: it is WRITTEN by ``build_heat_aggregation_sql`` and only
    READ here, so it cannot drift.

    ``min_uc`` is applied at READ time on the stored (raw, MAX) ``user_count``
    — see migration 0057. The agg table is aliased ``he`` so the caller's
    ``bbox_predicate`` (``he.geometry && ...``) and ``extra_predicate``
    (``he.sport = ANY(:sports)``) compose against it unchanged. NOTE: the
    agg table has no ``edge_key``/contributor columns, so a ``days``-window
    ``extra_predicate`` (which references ``he.edge_key``) is NOT valid here
    — callers keep the live :func:`build_heat_aggregation_sql` path for
    ``days``-filtered reads.

    SECURITY: same interpolation contract as the sibling builder — pass only
    trusted, code-generated predicate strings.
    """
    bbox_clause = f"AND ({bbox_predicate})" if bbox_predicate else ""

    agg_matched = f"""
        agg_matched AS (
            SELECT he.osm_way_id, he.geometry, he.sport, he.user_count,
                he.pass_count, he.forward_count, he.backward_count,
                he.highway_type
            FROM heat_edges_agg he
            WHERE he.user_count >= {min_uc}
              {bbox_clause}
              AND ({extra_predicate})
        )
    """

    if drop_grid_fallback:
        # Komoot-quality prod path: agg only, ZERO heat_edges scan.
        return f"""
        WITH {agg_matched}
        , combined AS (
            SELECT osm_way_id, geometry, sport, user_count, pass_count,
                forward_count, backward_count, highway_type FROM agg_matched
        )
        """

    # Keep grid-fallback: reuse the shared heat + grid_fallback CTE bodies.
    # ``he.osm_way_id IS NULL`` is added so the heat scan only materialises
    # grid rows (the OSM-matched half already lives in agg_matched).
    if max_grid_fallback_m is not None:
        length_cap = (
            f"AND NOT (he.osm_way_id IS NULL "
            f"AND ST_Length(he.geometry::geography) > {max_grid_fallback_m})"
        )
    else:
        length_cap = ""

    return f"""
        WITH {agg_matched}
        , heat AS (
            SELECT he.osm_way_id, he.sport, he.geometry,
                he.user_count, he.pass_count,
                he.forward_count, he.backward_count
            FROM heat_edges he
            WHERE he.osm_way_id IS NULL
              AND he.user_count >= {min_uc}
              AND he.user_count >= {grid_fallback_min_uc}
              {bbox_clause}
              {length_cap}
              AND ({extra_predicate})
        )
        , grid_fallback AS (
            SELECT NULL::bigint AS osm_way_id, geometry, sport, user_count,
                pass_count, forward_count, backward_count,
                'unknown'::text AS highway_type
            FROM heat
        )
        , combined AS (
            SELECT osm_way_id, geometry, sport, user_count, pass_count,
                forward_count, backward_count, highway_type FROM agg_matched
            UNION ALL
            SELECT osm_way_id, geometry, sport, user_count, pass_count,
                forward_count, backward_count, highway_type FROM grid_fallback
        )
    """
