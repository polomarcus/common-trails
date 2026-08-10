# Heatmap quality roadmap: gap to Komoot

A focused list of what we have, what Komoot does better, and the concrete steps to close the gap. Written after the May 2026 continuous-heatmap audit (commits `375304a` → `ba9604c`). Prioritised by visible impact.

The Komoot reference screenshot the discussion was anchored to: pink-to-orange gradient on roads, **one smooth line per road**, **no off-road fragments**, paths styled differently from roads, popular highways tinted brighter than residential lanes.

## Where we are after `ba9604c`

| Property | State | Code |
|---|---|---|
| One feature per `(osm_way_id, sport)` | ✅ | `build_pmtiles.py`, `refresh_matview.py`, `heatmap.py` fallback all share the CTE pattern |
| Smooth curve uses OSM `way_geometry` | ✅ | `LATERAL JOIN osm_road_edges` + `COALESCE(way_geometry, geometry, heat_geom)` |
| Grid-fallback dropped from display | ✅ | `drop_grid_fallback=True` default in `build_pmtiles.export_geojson` |
| Sharp detail at street zoom | ✅ish | tippecanoe max-zoom 15 (was 14); over-zoom at z17+ still loses some detail |
| Heatmap layer feels integrated | ⚠️ | purple-to-lavender ramp + 3 layers (glow / line / hit). Komoot uses pink→orange, layers feel warmer |
| Popular roads visually emphasised | ⚠️ | `heat_score` driven by `user_count` only; doesn't account for `highway_type` |
| Paths styled differently from roads | ❌ | single layer set; paths render the same as roads |
| Time-decay for recency | ❌ | a 5-year-old trace counts the same as one from yesterday |
| Lossless off-road coverage | ❌ | grid-fallback dropped → genuine off-road traces with no OSM match disappear |
| Reliable PMTiles deploy workflow | ✅ | `make pmtiles` writes host file in place (preserves bind-mount inode); never use `docker compose cp` |

## Gap and how to close it, ranked by impact

### 1. Pink/orange palette (frontend styling, ~1 hour)

Komoot's pink-to-orange ramp reads as "warmer = more travelled" intuitively. Ours is purple-to-lavender → looks like one solid wash.

**Action**: edit the three `community-trails-*` layers in `frontend/app/page.tsx` (search `community-trails-line`). Replace the line-color interpolate stops:

```js
// Current (purple-to-lavender):
['interpolate', ['linear'], ['get', 'heat_score'],
  0,    '#3b1e6e',
  0.4,  '#7c4dff',
  0.7,  '#c8a2ff',
  1.0,  '#f3e9ff']

// Komoot-style (pink-to-orange):
['interpolate', ['linear'], ['get', 'heat_score'],
  0,    '#7a2058',  // dark plum (rare)
  0.4,  '#d63384',  // hot pink (occasional)
  0.7,  '#f06595',  // bright pink (popular)
  1.0,  '#ff8c42']  // orange (very popular)
```

Drop the `community-trails-glow` layer's blur down (8px → 4px) so popular roads don't get washed out. Quick win, no backend change.

### 2. Highway-type emphasis in `heat_score` (~2 hours)

Komoot's bright orange on D-series roads is no accident — they boost popular *and* important roads. We have `highway_type` per heat_edge but ignore it.

**Action**: change the `heat_score` computation in the three SQL CTEs to factor in `highway_type`:

```sql
ROUND(LEAST(1.0,
    LN(1 + user_count) / (8.0 * LN(2))
    + CASE WHEN highway_type IN ('primary', 'secondary', 'trunk') THEN 0.15
           WHEN highway_type IN ('tertiary', 'unclassified') THEN 0.05
           ELSE 0 END
)::numeric, 3) AS heat_score
```

Caveat: `osm_road_edges.highway` is at the segment level, not the way. Need to lift it via the same JOIN we use for `way_geometry`. Adjust all three paths consistently.

### 3. Differentiated path/track styling (~3 hours)

Komoot dashes paths and tracks. We can do the same — split `community-trails-line` into:

- `community-roads-line` (where `highway_type IN ('primary', 'secondary', 'tertiary', 'residential')`)
- `community-paths-line` (where `highway_type IN ('path', 'track', 'cycleway', 'footway')`) — dashed via `line-dasharray`

This requires emitting `highway_type` as a feature property in the PMTiles export (we already collect it in the CTE; just include it in `prop_json`).

### 4. Lossless off-road via map-matching at ingest (~1–2 days)

Today, grid-fallback edges get created when the densified GPS doesn't snap to an OSM way within 25 m. They're then dropped from PMTiles → off-road traces vanish.

**Better approach**: introduce a real map-matcher (Valhalla or OSRM map-match endpoint) at ingest time. For each trace, get back a *route* through the OSM graph. Store `osm_way_id` per matched edge with confidence. Lose nothing legit, drop only the truly unmatchable.

This is a real engineering project — Valhalla container, schema add for confidence, ingest call site changes, fallback if matcher is down. Worth it before going public, can wait for the soft-launch.

### 5. Time-decay (~3 hours)

A trace from 2018 that hasn't been ridden since shouldn't count the same as one from this week.

**Action**: add `decay_factor = exp(-age_days / 365)` (or similar) to `heat_edge_contributors.activity_date`, sum the decayed factors per edge instead of `COUNT(*)`. The `?days=N` filter in `/heatmap/tiles` already JOINs `heat_edge_contributors`; this would replace it with a continuous decay.

Tradeoff: changes the meaning of `user_count`. K-anonymity logic depends on `user_count >= K`; need to think through whether decay weights work for K-anonymity (probably keep `user_count` as-is for K, add a separate `weighted_score` for display).

### 6. Sharper at high zoom (~30 min)

We bumped tippecanoe max-zoom 14 → 15. Could go to 16 if file size budget allows (currently 16 MB → likely 30 MB at z16). Worth measuring.

**Action**: try `--max-zoom=16` in `app/jobs/build_pmtiles.py:228`, rebuild via `make pmtiles`, eyeball the result, watch the file size. If still under 100 MB, ship it.

## Anti-goals (things that look like Komoot but aren't worth chasing)

- **Per-pixel heat density (raster heatmap)**. Their vector approach gives sharp edges + smooth panning. Don't refactor to raster.
- **3D/perspective rendering at z18+**. Cosmetic; expensive to render on mobile.
- **Real-time updates**. We auto-refresh the matview after each ingest already; PMTiles is rebuilt on demand. No need for live streaming.

## How to verify "we look like Komoot" after each change

1. `make pmtiles` (or rebuild via the job) — never `docker compose cp`.
2. Hard-reload the map at `http://localhost:3787/map?lat=43.62128&lon=3.87842&zoom=17.2`.
3. Open Komoot in a side window at the same coords/zoom for direct comparison.
4. Specifically check: (a) one line per road, (b) no off-road fragments, (c) curve smoothness on Rue du Colonel Marchand or any road with a bend, (d) clearly more colour on D-series than on residential lanes.

If any of those four is off, we're not at Komoot quality yet.
