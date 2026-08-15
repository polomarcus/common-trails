/**
 * community-heatmap-layers.ts — shared source/layer specs for the community
 * heatmap rendered from the STATIC `heatmap-display.pmtiles` binary.
 *
 * Display doctrine (CLAUDE.md): the static PMTiles is the PRIMARY display
 * artefact; the live `/heatmap/tiles/...` MVT endpoint is a DB-backed
 * FALLBACK only. Every page that draws the community heatmap must go through
 * these specs (fed by `getPmtilesUrl()`), never through the live endpoint.
 *
 * Consumers:
 *   - /map  → lib/init-map-layers.ts (layers start hidden, toggled by hooks)
 *   - /     → app/page.tsx hero background (layers visible immediately,
 *             slightly translucent, filtered per sport by the hero chips)
 *
 * The paint/layout expressions are the single source of truth extracted from
 * init-map-layers.ts (Komoot-style warm ramp). NOTE: useMapLayerSync /
 * useHeatmapControl re-set the opacity ramps on /map — if you change the
 * base ramps here, keep those hooks in sync.
 */

import { directionArrowLayout } from '@/lib/map-arrows';

export const COMMUNITY_TRAILS_SOURCE = 'community-trails';
export const COMMUNITY_TRAILS_GLOW_LAYER = 'community-trails-glow';
export const COMMUNITY_TRAILS_LINE_LAYER = 'community-trails-line';
export const COMMUNITY_TRAILS_ARROW_LAYER = 'community-trails-arrows';
/** Raster-style DENSITY heatmap (maplibre `heatmap` type) over the PMTiles
 *  `heat_points` point layer — the PRIMARY density visual (the Strava look).
 *  Replaces the diffuse vector-line "pâté": overlapping traces accumulate into
 *  a clean purple→orange density FIELD with the basemap showing through. */
export const COMMUNITY_TRAILS_HEAT_LAYER = 'community-trails-heat';

/** Source-layer name of the weighted density points the backend build emits
 *  (raw_trace_display._emit_run_points → build_pmtiles multi-layer tippecanoe). */
export const HEAT_POINTS_SOURCE_LAYER = 'heat_points';

/** Zoom floor for the THIN crisp line overlay. The raster heatmap carries the
 *  density look, but at regional zoom (the default hero view ~z10-11) its
 *  tippecanoe-thinned points render as scattered DOTS in sparse areas rather
 *  than continuous corridors (Paul, 2026-08-07: "on voit des points plutôt que
 *  des lignes au zoom par défaut"). The `trails` layer is continuous LineStrings,
 *  so showing it from z9 draws clean lines instead of dots. Verified on real prod
 *  pmtiles at z10.5: z14 = corridors+dots, z9 = a clean line network. Below z9
 *  (national overview) the raster alone carries the density (individual lines
 *  would just be noise there). */
export const LINE_CRISP_MINZOOM = 9;

/** oneway_score >= this ⇒ a trail is ridden predominantly ONE way (backend
 *  circular-concentration R; mirror of raw_trace_display.ONEWAY_SCORE_THRESHOLD).
 *  NOTE: this is NO LONGER the arrow gate — the arrow gate is the directional
 *  DOMINANCE RATIO below (a weakly-directional R≈0.6 trail is noise, not signal).
 *  oneway_score is retained only to drive the intensity ramps. */
export const ONEWAY_SCORE_THRESHOLD = 0.6;

/** ⭐ Directional-dominance gate for the arrows (Paul, 2026-08: "arrows should be
 *  useful mainly when ~95% of rides go the SAME direction").
 *
 *  dominance = |forward_count − backward_count| / (forward_count + backward_count)
 *
 *  Ratio → one-way-percentage mapping: for a majority fraction p of passes in the
 *  dominant direction, dominance = |p − (1−p)| = 2p − 1, i.e.  p = (1 + dominance) / 2.
 *    dominance 0.6 → 80% one-way
 *    dominance 0.8 → 90% one-way   ← the gate (clearly one-way)
 *    dominance 0.9 → 95% one-way   (Paul's stated "~95%" ideal)
 *    dominance 1.0 → 100% one-way
 *
 *  Gated at 0.8 (≥90% one-way) so a rendered arrow MEANS "this trail is clearly
 *  one-way"; a 60/40 (dominance 0.2) or 50/50 (0.0) trail draws NO arrow. */
export const DOMINANCE_THRESHOLD = 0.8;

/** Minimum directional passes (forward_count + backward_count) before a trail is
 *  arrowed. A lone GPS traversal is trivially one-way, so require multi-pass
 *  confirmation. Doubles as the divide-by-zero guard for the dominance ratio
 *  (fwd+bwd >= 3 > 0). */
export const ARROW_MIN_PASS_COUNT = 3;

/** Intensity ramps key on oneway_score, but only strongly-dominant trails now
 *  survive the dominance gate, so re-center the ramp domain to start at this
 *  higher floor (was ONEWAY_SCORE_THRESHOLD=0.6) — otherwise every shown arrow
 *  sat in the ramp's upper region with no visible contrast. A shown trail below
 *  this floor simply clamps to the minimum (faintest/smallest) intensity. */
export const ARROW_RAMP_FLOOR = 0.8;

/** Arrows only ever render for the off-road singletrack family (mtb/gravel) —
 *  the sports whose descents have a meaningful dominant direction. Road/running
 *  are commuter/loop networks: never arrowed. Mirrors the backend
 *  raw_trace_display._DIRECTIONAL_SPORTS. */
const ARROW_SPORTS = ['mtb', 'gravel'];

/** Zoom floor for arrows: below z13 tippecanoe's low-zoom coalescing merges
 *  features across cells and mixes oneway_scores, so only arrow at street zoom. */
export const ARROW_MINZOOM = 13;

/** Vector source spec for the PMTiles heatmap display file.
 *  `url` must be a `pmtiles://…` URL (see lib/cdn-cache.ts::getPmtilesUrl and
 *  lib/pmtiles-protocol.ts — the protocol is registered in components/Map.tsx). */
export function communityTrailsSourceSpec(url: string) {
  return {
    type: 'vector',
    url,
    minzoom: 6,
    maxzoom: 14,
  };
}

export interface CommunityLayerOptions {
  /** Initial visibility. /map starts hidden (hooks toggle it); the hero starts visible. */
  visible?: boolean;
  /** Layer minzoom (default 10, matching /map). The hero lowers it so the
   *  background never looks empty when panned/zoomed out slightly. */
  minzoom?: number;
  /** Multiplies the data-driven opacity ramps (hero background dims slightly).
   *  Default 1 → emits the plain ramps, byte-identical to the historical /map specs. */
  opacityMult?: number;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Expr = any;

// Exported SSOT: the /map hooks (useHeatmapControl) that re-set opacity at
// runtime MUST route through this instead of hand-writing `['*', mult, ramp]`,
// or they reintroduce the invalid-zoom-wrapper bug for the raster heatmap-opacity
// ramp (which is zoom-driven). See useHeatmapControl.ts.
export function withMult(ramp: Expr, mult: number): Expr {
  if (mult === 1) return ramp;
  // maplibre forbids a `zoom` expression ANYWHERE except as the direct input to
  // a top-level step/interpolate. So `['*', mult, <zoom-interpolate>]` is an
  // INVALID paint value — the layer fails style validation and is SILENTLY
  // dropped (no throw; an `error` event fires). That is exactly how the hero's
  // raster `heatmap` layer stopped rendering: heatmap-opacity is a ZOOM ramp and
  // the hero passes opacityMult 0.9, so `withMult` produced the illegal wrapper.
  // For a zoom-driven ramp, fold the multiplier into the interpolate OUTPUT stops
  // instead (numerically identical, and legal). Data-driven ramps (`['get', …]`,
  // e.g. line/glow opacity) keep the simple `['*', …]` wrapper — legal there.
  if (
    Array.isArray(ramp) && ramp[0] === 'interpolate' &&
    Array.isArray(ramp[2]) && ramp[2][0] === 'zoom'
  ) {
    const folded: Expr = ramp.slice(0, 3); // 'interpolate', interpolation, ['zoom']
    for (let i = 3; i < ramp.length; i += 2) {
      folded.push(ramp[i]); // stop input (zoom level) — unchanged
      const out = ramp[i + 1];
      folded.push(typeof out === 'number' ? mult * out : ['*', mult, out]);
    }
    return folded;
  }
  return ['*', mult, ramp];
}

// Glow opacity — kept LOW so many overlapping glows in a dense city centre
// accumulate GRADUALLY (Strava-style density gradient) instead of instantly
// saturating into a solid orange blob. Retuned down (was up to 0.4) as part of
// fix/heatmap-oversaturation: at ~0.16 top, three stacked glows still only reach
// ~1-(1-0.16)^3 ≈ 0.41 alpha, so popular corridors bloom without filling in.
export const GLOW_OPACITY_RAMP: Expr = ['interpolate', ['linear'], ['get', 'heat_score'],
  0, 0.04, 0.5, 0.07, 1.0, 0.1,
];

// Capped at 0.6 (was 1.0): where a rider repeats a route 100+×, the raw traces
// overlap; full opacity turned that into a solid orange "pâté" (blob). A cap lets
// overlaps ACCUMULATE into intensity (Strava-raster feel) instead of a solid mass.
export const LINE_OPACITY_RAMP: Expr = ['interpolate', ['linear'], ['get', 'heat_score'],
  0, 0.4, 0.3, 0.45, 0.7, 0.52, 1.0, 0.6,
];

// ⭐ SSOT width ramps — the /map hooks (useMapLayerSync, useHeatmapControl) IMPORT
// these instead of re-declaring them, so a paint retune here can't silently fail
// to reach /map (the 2026-08-05 pâté-fix bug: the spec was thinned but the hooks
// still re-set the OLD fat ramps on heatmap-toggle). Thinned ~half at the
// high-heat end (z16 core 8→3.6) so dense repeated traces don't merge into a blob.
export const LINE_WIDTH_RAMP: Expr = ['interpolate', ['exponential', 1.5], ['zoom'],
  10, ['interpolate', ['linear'], ['get', 'heat_score'], 0, 0.4, 0.3, 0.7, 0.7, 1.3, 1, 2],
  13, ['interpolate', ['linear'], ['get', 'heat_score'], 0, 0.7, 0.3, 1.2, 0.7, 2, 1, 3],
  16, ['interpolate', ['linear'], ['get', 'heat_score'], 0, 1, 0.3, 1.6, 0.7, 2.6, 1, 3.6],
];
export const GLOW_WIDTH_RAMP: Expr = ['interpolate', ['exponential', 1.5], ['zoom'],
  10, ['interpolate', ['linear'], ['get', 'heat_score'], 0, 1, 0.3, 1.6, 0.7, 2.6, 1, 3.6],
  13, ['interpolate', ['linear'], ['get', 'heat_score'], 0, 1.4, 0.3, 2.2, 0.7, 3.4, 1, 4.6],
  16, ['interpolate', ['linear'], ['get', 'heat_score'], 0, 1.8, 0.3, 2.8, 0.7, 4, 1, 5.5],
];

/** Soft bloom around popular trails — Garmin-style diffused edges. */
export function communityTrailsGlowLayerSpec(opts: CommunityLayerOptions = {}) {
  const { visible = false, minzoom = 10, opacityMult = 1 } = opts;
  return {
    id: COMMUNITY_TRAILS_GLOW_LAYER,
    type: 'line',
    source: COMMUNITY_TRAILS_SOURCE,
    'source-layer': 'trails',
    minzoom,
    layout: {
      visibility: visible ? 'visible' : 'none',
      'line-cap': 'round',
      'line-join': 'round',
      'line-sort-key': ['get', 'user_count'],
    },
    paint: {
      // Glow ramp: warm pink → orange so popular roads bloom warmer.
      'line-color': ['interpolate', ['linear'], ['get', 'heat_score'],
        0, '#a83275', 0.4, '#d63384', 0.7, '#f06595', 1.0, '#ff8c42',
      ],
      // Glow WIDTH — capped hard (was up to 16-22 px). In prod most urban ways
      // sit at heat_score ~0.25-0.5 (heat_score = log2(1+user_count)/8 + highway
      // boost, and the beta is mostly user_count=1), so the 0.3 stop is the one
      // that fills the dense centre — it drops the most (z13 8→3.5). A gentle
      // curve keeps low/mid density thin (reads as corridor structure) and lets
      // only genuinely popular roads bloom to the ~6-10 px cap.
      // Slimmed glow to match the thinner core (pâté fix, 2026-08-05). SSOT const.
      'line-width': GLOW_WIDTH_RAMP,
      // Tighter blur so a glow stays close to its own line and doesn't wash into
      // neighbouring streets (a wide+blurred glow was the other blob driver).
      'line-blur': ['interpolate', ['linear'], ['get', 'heat_score'],
        0, 1, 0.3, 1.5, 0.7, 2.5, 1.0, 3,
      ],
      'line-opacity': withMult(GLOW_OPACITY_RAMP, opacityMult),
      // Smooth fade when H key toggles heatmap (Crouzet discovery technique)
      'line-opacity-transition': { duration: 300, delay: 0 },
    },
  };
}

/** Crisp core trail lines — thickness driven by popularity (Garmin-style). */
export function communityTrailsLineLayerSpec(opts: CommunityLayerOptions = {}) {
  const { visible = false, minzoom = 10, opacityMult = 1 } = opts;
  return {
    id: COMMUNITY_TRAILS_LINE_LAYER,
    type: 'line',
    source: COMMUNITY_TRAILS_SOURCE,
    'source-layer': 'trails',
    minzoom,
    layout: {
      visibility: visible ? 'visible' : 'none',
      'line-cap': 'round',
      'line-join': 'round',
      'line-sort-key': ['get', 'user_count'],
    },
    paint: {
      // Core line ramp: dark plum (rare) → hot pink (occasional) →
      // bright pink (popular) → orange (very popular). Komoot-style.
      'line-color': ['interpolate', ['linear'], ['get', 'heat_score'],
        0, '#7a2058', 0.25, '#a83275', 0.5, '#d63384',
        0.75, '#f06595', 0.9, '#ff7e3a', 1.0, '#ff8c42',
      ],
      // Thinned (~half at the high-heat end): 8px cores made dense repeated
      // traces merge into a blob. See the pâté fix (2026-08-05). SSOT const.
      'line-width': LINE_WIDTH_RAMP,
      'line-blur': 0,
      'line-opacity': withMult(LINE_OPACITY_RAMP, opacityMult),
      'line-opacity-transition': { duration: 300, delay: 0 },
    },
  };
}

// ── Raster-style density heatmap (SSOT paint) ────────────────────────────────
// The prototype-validated paint for the maplibre `heatmap` layer over the
// `heat_points` source-layer. Exported so any hook that re-sets these on toggle
// reads the SAME expressions (the 2026-08-05 divergence lesson: retuning a spec
// while the hooks re-set the old values silently fails to reach /map).

// Values below are the prototype-validated paint (Paul: "looks great" on the
// real Le Lez prod data) extended down to z6 for the hero. Low-zoom intensity
// is kept LOW (0.4–0.6) so a whole-city view reads as a density field, not a
// solid blob; it climbs at street zoom where points spread out.

/** heatmap-weight: point `w` (backend log-normalised corridor popularity, the
 *  same density scale as the line heat_score) → contribution. Floor 0.4 so a
 *  solo desire line still registers; overlaps accumulate further via density. */
export const HEAT_WEIGHT_RAMP: Expr = ['interpolate', ['linear'], ['get', 'w'],
  0, 0.4, 1, 1,
];

/** heatmap-intensity PEAKS at mid zoom then DECAYS at street zoom. The old ramp
 *  climbed to 2.0@z18 on the assumption points spread out; but on the most-ridden
 *  corridors (the Lez, 100+ passes) even spread points accumulate density >>1 →
 *  the color ramp clamps to solid orange = the "pâté" blob. Decaying intensity
 *  (0.9@z13 → 0.5@z16 → 0.35@z18) keeps the densest area in the mid-range so
 *  individual traces stay legible; the thin crisp line (z14+) carries the
 *  street-level "this is a road" read. Validated on real prod Le Lez data
 *  (2026-08-06): z16.5 solid-blob+dark-beads → readable corridors, overview
 *  (z12) unchanged (only z13+ diverges). */
export const HEAT_INTENSITY_RAMP: Expr = ['interpolate', ['linear'], ['zoom'],
  6, 0.4, 10, 0.6, 13, 0.9, 14, 0.8, 16, 0.5, 18, 0.35,
];

/** heatmap-radius grows to a MODEST cap (~12px@z18, was 26px). A 26px kernel at
 *  street zoom made overlapping traces merge into a filled mass (and its
 *  low-density tail drew ugly dark "beads"). ~10-12px lets corridors read as
 *  corridors, not a field. */
export const HEAT_RADIUS_RAMP: Expr = ['interpolate', ['linear'], ['zoom'],
  6, 2, 10, 4, 13, 8, 16, 10, 18, 12,
];

/** heatmap-opacity ~0.85 through mid zoom, then fades HARDER from z15 (was z16)
 *  so the THIN crisp line (z14+) takes over the street-level read and the raster
 *  stops fighting it into a blob. */
export const HEAT_OPACITY_RAMP: Expr = ['interpolate', ['linear'], ['zoom'],
  6, 0.85, 13, 0.8, 15, 0.55, 16.5, 0.35, 18, 0.25,
];

/** Brand density gradient: transparent → dark plum → hot pink → orange. The
 *  0-density stop MUST be fully transparent so the basemap shows through. */
export const HEAT_COLOR_RAMP: Expr = ['interpolate', ['linear'], ['heatmap-density'],
  0, 'rgba(122,32,88,0)',
  0.15, 'rgba(122,32,88,0.55)',
  0.4, '#a83275',
  0.6, '#d63384',
  0.8, '#f06595',
  0.95, '#ff7e3a',
  1.0, '#ff8c42',
];

/**
 * Raster-style density heatmap layer over the PMTiles `heat_points` point
 * layer — the PRIMARY community density visual (fixes the vector-line pâté).
 * `opacityMult` dims it for the hero background (matches the line/glow specs).
 */
export function communityTrailsHeatLayerSpec(opts: CommunityLayerOptions = {}) {
  const { visible = false, minzoom = 6, opacityMult = 1 } = opts;
  return {
    id: COMMUNITY_TRAILS_HEAT_LAYER,
    type: 'heatmap',
    source: COMMUNITY_TRAILS_SOURCE,
    'source-layer': HEAT_POINTS_SOURCE_LAYER,
    minzoom,
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'heatmap-weight': HEAT_WEIGHT_RAMP,
      'heatmap-intensity': HEAT_INTENSITY_RAMP,
      'heatmap-radius': HEAT_RADIUS_RAMP,
      'heatmap-color': HEAT_COLOR_RAMP,
      'heatmap-opacity': withMult(HEAT_OPACITY_RAMP, opacityMult),
      'heatmap-opacity-transition': { duration: 300, delay: 0 },
    },
  };
}

/**
 * MapLibre filter for a sport chip on the PMTiles `trails` layer (features
 * carry a `sport` property, one feature per `(osm_way_id, sport)`).
 *
 * - `'all'` → `null` (setFilter(null) clears the filter).
 * - `'offroad'` → expands to mtb + offroad + gravel, mirroring the backend
 *   SSOT `app/config.py::expand_sport` (offroad has no dedicated heat edges
 *   for most data — see reference_offroad_no_heat_edges).
 */
export function communityHeatSportFilter(sport: string): Expr | null {
  if (sport === 'all') return null;
  const sports = sport === 'offroad' ? ['mtb', 'offroad', 'gravel'] : [sport];
  return ['in', ['get', 'sport'], ['literal', sports]];
}

/** Layer ids the sport chip filters: the density (`heat_points`) + crisp line +
 *  invisible click-hit layer all carry `sport`. */
export const COMMUNITY_TRAILS_SPORT_FILTERED_LAYERS = [
  COMMUNITY_TRAILS_HEAT_LAYER,
  COMMUNITY_TRAILS_LINE_LAYER,
  'community-trails-hit',
] as const;

/**
 * Apply a sport chip to the community heatmap on a live map — SSOT so the home
 * hero AND `/map` filter identically. `/map` never called this (its chips only
 * updated React state, never `setFilter`) so its sport filter did nothing until
 * 2026-08-15; home worked because `app/page.tsx` filtered inline. Extracting the
 * one helper both call closes that drift. Safe on partial maps (the community
 * layers are skipped when `NEXT_PUBLIC_HEATMAP_URL` is unset) — each layer is
 * `getLayer`-guarded.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function applyCommunityHeatSportFilter(map: any, sport: string): void {
  const filter = communityHeatSportFilter(sport);
  for (const id of COMMUNITY_TRAILS_SPORT_FILTERED_LAYERS) {
    if (map?.getLayer?.(id)) map.setFilter(id, filter);
  }
}

/**
 * Filter for the direction-arrow layer: STRONGLY one-way, multi-pass, mtb/gravel
 * trails only. The PRIMARY gate is the directional DOMINANCE RATIO — a weakly
 * one-way (~60%) trail is noise, so an arrow only renders when the dominant
 * direction is ≥90% of directional passes (DOMINANCE_THRESHOLD, see there for the
 * ratio→% mapping). All three clauses are load-bearing —
 *   - forward_count + backward_count >= min: enough directional data (a lone pass
 *     is trivially one-way and would spray arrows over every single trace) AND the
 *     divide-by-zero guard for the ratio;
 *   - dominance ratio >= threshold: |fwd − bwd| / (fwd + bwd) >= DOMINANCE_THRESHOLD,
 *     expressed in the algebraically-equivalent NO-DIVISION form
 *     |fwd − bwd| >= T·(fwd + bwd) so no float division runs inside the filter
 *     expression (also sidesteps a 0/0 for degenerate features);
 *   - sport ∈ mtb/gravel: only off-road singletrack has a meaningful direction.
 */
export function communityDirectionArrowFilter(): Expr {
  const fwd: Expr = ['get', 'forward_count'];
  const bwd: Expr = ['get', 'backward_count'];
  const total: Expr = ['+', fwd, bwd];
  const absDiff: Expr = ['abs', ['-', fwd, bwd]];
  return ['all',
    // enough directional passes (also guards divide-by-zero for the ratio)
    ['>=', total, ARROW_MIN_PASS_COUNT],
    // dominance: |fwd-bwd|/(fwd+bwd) >= T  ⇔  |fwd-bwd| >= T*(fwd+bwd) (no float div)
    ['>=', absDiff, ['*', DOMINANCE_THRESHOLD, total]],
    ['in', ['get', 'sport'], ['literal', ARROW_SPORTS]],
  ];
}

/** ⭐ DOMINANCE ramps — make the DOMINANT direction unmistakable, not just
 *  present. The backend orients every directional feature's geometry to the
 *  more-popular travel direction (raw_trace_display._orient_directional_run),
 *  so `symbol-placement:'line'` already auto-rotates all of a trail's chevrons
 *  the SAME dominant way (no more overlapping opposite arrows = the "both ways"
 *  star). On top of that we scale the arrow by `oneway_score` (the circular
 *  concentration R∈[0,1]): a just-past-the-floor trail draws faint + small, a
 *  strongly one-way descent (R→1) draws bold + large — so STRENGTH of dominance
 *  reads at a glance. Both key off a plain `['get','oneway_score']` (data-driven,
 *  legal as icon-opacity paint / icon-size layout). Since only strongly-dominant
 *  trails now survive the ratio gate, the ramp domain is re-centered to start at
 *  ARROW_RAMP_FLOOR (was ONEWAY_SCORE_THRESHOLD) so there's still visible
 *  contrast among the shown arrows; anything below the floor clamps to the min. */
export const ARROW_OPACITY_RAMP: Expr = ['interpolate', ['linear'], ['get', 'oneway_score'],
  ARROW_RAMP_FLOOR, 0.55, 0.9, 0.8, 1.0, 1.0,
];
export const ARROW_SIZE_RAMP: Expr = ['interpolate', ['linear'], ['get', 'oneway_score'],
  ARROW_RAMP_FLOOR, 0.8, 1.0, 1.2,
];

/**
 * Direction arrows on predominantly one-way MTB/gravel trails, pointing the
 * DOMINANT (more-ridden) way.
 *
 * ⭐ `symbol-placement:'line'` makes maplibre AUTO-rotate each chevron to
 * follow the feature's coordinate order, so we emit NO `mean_bearing` and set
 * NO `icon-rotate` (either would double-rotate). The KEY is that the backend
 * orients each directional feature's coordinate order to the more-popular
 * travel direction (`_orient_directional_run`), so every arrow on a trail
 * points the same dominant way — you can read which direction wins. The chevron
 * image (`direction-arrow`) is registered once by `registerDirectionArrow`
 * (map-arrows.ts) — the SAME sprite the activity arrows use. Gated by
 * `communityDirectionArrowFilter` + `ARROW_MINZOOM`; `oneway_score` drives the
 * opacity/size so dominance STRENGTH is visible (see the ramps above).
 */
export function communityTrailsArrowLayerSpec(opts: CommunityLayerOptions = {}) {
  const { visible = false } = opts;
  return {
    id: COMMUNITY_TRAILS_ARROW_LAYER,
    type: 'symbol',
    source: COMMUNITY_TRAILS_SOURCE,
    'source-layer': 'trails',
    minzoom: ARROW_MINZOOM,
    filter: communityDirectionArrowFilter(),
    layout: {
      ...directionArrowLayout({ 'icon-size': ARROW_SIZE_RAMP }),
      visibility: visible ? 'visible' : 'none',
    },
    paint: {
      // Fainter for a barely-one-way trail, bold for a strongly one-way one.
      'icon-opacity': ARROW_OPACITY_RAMP,
    },
  };
}
