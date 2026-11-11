/**
 * Paint SSOT for the user's OWN traces ("Mes traces", source `user-activities`).
 *
 * Beta bug (Sète/Montpellier, 2026-11): at z10-11 a user with thousands of
 * traces saw one solid blue/purple blob with blurry halos. The glow was a fixed
 * 14 px / blur 6 / opacity 0.18 and the line a fixed 3 px / 0.92 at EVERY zoom,
 * so at low zoom every trace became a fat halo and they all merged. Width, blur
 * and opacity are now zoom ramps: hairlines with no glow when zoomed out, the
 * legacy look (14 px glow, 3-4 px line) once zoomed in on a ride.
 *
 * Runtime opacity changes (dimming the others when one activity is selected)
 * MUST go through `applyUserTraceOpacity` — a `setPaintProperty(…, 0.92)`
 * constant would silently replace the zoom ramp and bring the blob back.
 */
import { withMult } from '@/lib/community-heatmap-layers';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Expr = any;

export const USER_TRACE_GLOW_LAYER = 'user-activities-glow';
export const USER_TRACE_LINE_LAYER = 'user-activities-line';

export const USER_TRACE_GLOW_WIDTH: Expr = ['interpolate', ['linear'], ['zoom'],
  8, 1.5, 11, 3, 13, 8, 15, 14,
];
export const USER_TRACE_GLOW_BLUR: Expr = ['interpolate', ['linear'], ['zoom'],
  8, 0.5, 11, 1.5, 15, 6,
];
// No halo at all below z9 (it is what turned thousands of traces into a blob);
// it fades in up to the legacy 0.18 at z12.
export const USER_TRACE_GLOW_OPACITY: Expr = ['interpolate', ['linear'], ['zoom'],
  9, 0, 12, 0.18,
];
export const USER_TRACE_LINE_WIDTH: Expr = ['interpolate', ['exponential', 1.5], ['zoom'],
  6, 0.9, 8, 1.1, 11, 1.8, 14, 3.2, 16, 4.2,
];
export const USER_TRACE_LINE_OPACITY: Expr = ['interpolate', ['linear'], ['zoom'],
  8, 0.75, 12, 0.85, 14, 0.92,
];

// Dimming applied to the other traces while one activity is selected — the
// ratios of the old constants (line 0.92 → 0.25, glow 0.18 → 0.06).
export const USER_TRACE_DIM_LINE_MULT = 0.27;
export const USER_TRACE_DIM_GLOW_MULT = 0.33;

/** Line/glow opacity expressions, optionally dimmed — always zoom ramps. */
export function userTraceOpacity(dimmed: boolean): { line: Expr; glow: Expr } {
  return {
    line: withMult(USER_TRACE_LINE_OPACITY, dimmed ? USER_TRACE_DIM_LINE_MULT : 1),
    glow: withMult(USER_TRACE_GLOW_OPACITY, dimmed ? USER_TRACE_DIM_GLOW_MULT : 1),
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function applyUserTraceOpacity(map: any, dimmed: boolean): void {
  const { line, glow } = userTraceOpacity(dimmed);
  map.setPaintProperty(USER_TRACE_LINE_LAYER, 'line-opacity', line);
  map.setPaintProperty(USER_TRACE_GLOW_LAYER, 'line-opacity', glow);
}

export const USER_TRACE_HIT_LAYER = 'user-activities-hit';
export const USER_TRACE_SOURCE = 'user-activities';
/** Layer name inside the per-user PMTiles (backend my_heatmap_files.TILE_LAYER). */
export const USER_TRACE_TILE_LAYER = 'activities';

/**
 * The three "Ma heatmap" layers (glow, line, wide transparent hit area), in
 * draw order. SSOT for both source flavours: the legacy `geojson` source fed by
 * GET /me/activities, and the per-user private PMTiles (`vector`, which needs a
 * `source-layer`). Same ids either way, so the hover/click handlers, the
 * visibility toggles and the dimming keep working across a source swap.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function userTraceLayerSpecs(color: string, sourceLayer?: string): any[] {
  const base = {
    type: 'line',
    source: USER_TRACE_SOURCE,
    ...(sourceLayer ? { 'source-layer': sourceLayer } : {}),
  };
  const layout = { visibility: 'visible', 'line-cap': 'round', 'line-join': 'round' };
  return [
    {
      ...base,
      id: USER_TRACE_GLOW_LAYER,
      layout,
      paint: {
        'line-color': color,
        'line-width': USER_TRACE_GLOW_WIDTH,
        'line-opacity': USER_TRACE_GLOW_OPACITY,
        'line-blur': USER_TRACE_GLOW_BLUR,
      },
    },
    {
      ...base,
      id: USER_TRACE_LINE_LAYER,
      layout,
      paint: {
        'line-color': color,
        'line-width': USER_TRACE_LINE_WIDTH,
        'line-opacity': USER_TRACE_LINE_OPACITY,
      },
    },
    {
      ...base,
      id: USER_TRACE_HIT_LAYER,
      layout,
      paint: { 'line-color': 'transparent', 'line-width': 20, 'line-opacity': 0 },
    },
  ];
}
