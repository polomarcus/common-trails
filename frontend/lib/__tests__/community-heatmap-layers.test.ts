/**
 * Non-regression pins for the shared community-heatmap layer specs
 * (lib/community-heatmap-layers.ts) — the SSOT consumed by BOTH
 * /map (init-map-layers.ts) and the home hero (app/page.tsx).
 *
 * Context (2026-07 home-hero fog bug): the home used to draw a
 * `type:'heatmap'` DENSITY layer over `heat_points` centroids fed by the
 * LIVE `/heatmap/tiles/...` MVT endpoint → purple fog over the whole hero
 * + per-visitor DB queries on the db-f1-micro.
 *
 * Raster-heatmap rework (2026-08, feat/raster-heatmap): the density visual is
 * back — but now a maplibre `heatmap` layer over the STATIC PMTiles
 * `heat_points` layer (built offline by build_pmtiles), NOT the live MVT
 * endpoint. That fixes both the fog (this is a real density gradient, not fed
 * by a broken source) AND the per-visitor DB load (static file). These tests
 * pin: line/arrow specs stay line/symbol over `trails`; the new heatmap spec is
 * a `heatmap` over `heat_points` with a transparent 0-density stop.
 */
import { describe, it, expect } from 'vitest';
import {
  communityTrailsSourceSpec,
  communityTrailsGlowLayerSpec,
  communityTrailsLineLayerSpec,
  communityTrailsArrowLayerSpec,
  communityTrailsHeatLayerSpec,
  communityHeatSportFilter,
  communityDirectionArrowFilter,
  COMMUNITY_TRAILS_SOURCE,
  COMMUNITY_TRAILS_GLOW_LAYER,
  COMMUNITY_TRAILS_LINE_LAYER,
  COMMUNITY_TRAILS_ARROW_LAYER,
  COMMUNITY_TRAILS_HEAT_LAYER,
  HEAT_POINTS_SOURCE_LAYER,
  DOMINANCE_THRESHOLD,
  ARROW_MIN_PASS_COUNT,
  ARROW_RAMP_FLOOR,
  ARROW_MINZOOM,
  ARROW_OPACITY_RAMP,
  ARROW_SIZE_RAMP,
  LINE_CRISP_MINZOOM,
  HEAT_OPACITY_RAMP,
  LINE_OPACITY_RAMP,
  withMult,
} from '../community-heatmap-layers';

describe('withMult (SSOT opacity scaler — used by the /map hooks at runtime)', () => {
  it('mult===1 returns the ramp unchanged (byte-identical /map default)', () => {
    expect(withMult(HEAT_OPACITY_RAMP, 1)).toBe(HEAT_OPACITY_RAMP);
    expect(withMult(LINE_OPACITY_RAMP, 1)).toBe(LINE_OPACITY_RAMP);
  });

  it('a ZOOM ramp × mult stays a top-level interpolate (NOT the invalid [*, mult, zoom-ramp])', () => {
    // HEAT_OPACITY_RAMP is ['interpolate',['linear'],['zoom'], …]. maplibre
    // REJECTS ['*', mult, <zoom-interpolate>] (silently drops the paint + fires
    // an error) — the exact bug that stopped the hero raster rendering AND made
    // useHeatmapControl's runtime heatmap-opacity set a no-op. withMult must fold
    // the multiplier into the OUTPUT stops instead.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const dim = withMult(HEAT_OPACITY_RAMP, 0.35) as any[];
    expect(dim[0]).toBe('interpolate');
    expect(dim[0]).not.toBe('*');
    expect(dim[2]).toEqual(['zoom']);
    // the z6 stop 0.85 is scaled to 0.85*0.35, folded into the output, not wrapped.
    expect(dim[4]).toBeCloseTo(0.85 * 0.35, 6);
  });

  it('a DATA-driven ramp × mult keeps the simple [*, mult, ramp] wrapper (legal there)', () => {
    // LINE_OPACITY_RAMP keys off ['get','heat_score'] — a ['*'] wrapper is valid.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const dim = withMult(LINE_OPACITY_RAMP, 0.35) as any[];
    expect(dim[0]).toBe('*');
    expect(dim[1]).toBe(0.35);
  });
});

describe('communityTrailsSourceSpec', () => {
  it('builds a vector source over the given pmtiles:// URL (z6-14)', () => {
    const spec = communityTrailsSourceSpec('pmtiles://https://example.org/heatmap-display.pmtiles');
    expect(spec).toEqual({
      type: 'vector',
      url: 'pmtiles://https://example.org/heatmap-display.pmtiles',
      minzoom: 6,
      maxzoom: 14,
    });
  });
});

describe('layer specs — line/glow are LINE over "trails"', () => {
  it('glow + line specs are LINE layers over the PMTiles "trails" source-layer (the density field is a SEPARATE heatmap spec over heat_points)', () => {
    for (const spec of [communityTrailsGlowLayerSpec(), communityTrailsLineLayerSpec()]) {
      expect(spec.type).toBe('line');
      expect(spec['source-layer']).toBe('trails');
      expect(spec.source).toBe(COMMUNITY_TRAILS_SOURCE);
    }
  });

  it('defaults match the historical /map specs: hidden, minzoom 10, plain (unmultiplied) opacity ramp', () => {
    const glow = communityTrailsGlowLayerSpec();
    const line = communityTrailsLineLayerSpec();
    expect(glow.id).toBe(COMMUNITY_TRAILS_GLOW_LAYER);
    expect(line.id).toBe(COMMUNITY_TRAILS_LINE_LAYER);
    for (const spec of [glow, line]) {
      expect(spec.layout.visibility).toBe('none');
      expect(spec.minzoom).toBe(10);
      // Plain data-driven ramp — must stay in sync with the copies in
      // useMapLayerSync / useHeatmapControl which re-set it on /map.
      expect(spec.paint['line-opacity'][0]).toBe('interpolate');
    }
    // Capped at 0.6 (was 1.0) so dense repeated traces accumulate into intensity
    // rather than a solid "pâté"; low-density floor stays 0.4 (desire lines visible).
    expect(line.paint['line-opacity']).toEqual([
      'interpolate', ['linear'], ['get', 'heat_score'],
      0, 0.4, 0.3, 0.45, 0.7, 0.52, 1.0, 0.6,
    ]);
  });

  it('hero options: visible, custom minzoom, opacity ramp multiplied', () => {
    const spec = communityTrailsLineLayerSpec({ visible: true, minzoom: 8, opacityMult: 0.9 });
    expect(spec.layout.visibility).toBe('visible');
    expect(spec.minzoom).toBe(8);
    expect(spec.paint['line-opacity'][0]).toBe('*');
    expect(spec.paint['line-opacity'][1]).toBe(0.9);
    expect(spec.paint['line-opacity'][2][0]).toBe('interpolate');
  });
});

describe('glow layer — anti-oversaturation invariants (fix/heatmap-oversaturation)', () => {
  // Regression guard for the "gros pâté orange" blob: at high trace density the
  // GLOW used to render very wide (up to 20-22 px), fairly opaque (up to 0.4)
  // blurred lines that additively saturated the dense centre into a solid mass.
  // These pins keep the glow narrow + faint so density reads as a gradient.
  const glow = communityTrailsGlowLayerSpec();

  it('glow line-width is capped small at every zoom stop (no 16-22 px blobs)', () => {
    // ['interpolate', ['exponential',1.5], ['zoom'], z0, ramp0, z1, ramp1, ...]
    // each rampN is ['interpolate',['linear'],['get','heat_score'], k,v, ...];
    // the max width is the value at the last (heat_score=1.0) stop.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const width = glow.paint['line-width'] as any[];
    for (let i = 4; i < width.length; i += 2) {
      const ramp = width[i];
      const maxWidth = ramp[ramp.length - 1];
      expect(typeof maxWidth).toBe('number');
      expect(maxWidth).toBeLessThanOrEqual(10);
    }
  });

  it('glow line-opacity stays low so overlapping glows accumulate gradually', () => {
    // ['interpolate',['linear'],['get','heat_score'], k0,v0, k1,v1, ...]
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const op = glow.paint['line-opacity'] as any[];
    const topOpacity = op[op.length - 1];
    expect(topOpacity).toBeLessThanOrEqual(0.2);
  });

  it('low-density desire lines stay visible (LINE layer opacity at heat_score 0)', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const op = communityTrailsLineLayerSpec().paint['line-opacity'] as any[];
    // first stop is heat_score 0 → its value must remain clearly visible.
    const zeroOpacity = op[4];
    expect(zeroOpacity).toBeGreaterThanOrEqual(0.4);
  });
});

describe('raster-style density heatmap layer (feat/raster-heatmap)', () => {
  it('is a `heatmap` layer over the STATIC PMTiles `heat_points` source-layer', () => {
    const spec = communityTrailsHeatLayerSpec();
    expect(spec.id).toBe(COMMUNITY_TRAILS_HEAT_LAYER);
    expect(spec.type).toBe('heatmap');
    expect(spec.source).toBe(COMMUNITY_TRAILS_SOURCE);
    expect(spec['source-layer']).toBe(HEAT_POINTS_SOURCE_LAYER);
    expect(HEAT_POINTS_SOURCE_LAYER).toBe('heat_points');
  });

  it('starts hidden by default at minzoom 6; opt-in visible', () => {
    expect(communityTrailsHeatLayerSpec().layout.visibility).toBe('none');
    expect(communityTrailsHeatLayerSpec().minzoom).toBe(6);
    expect(communityTrailsHeatLayerSpec({ visible: true }).layout.visibility).toBe('visible');
  });

  it('weight reads the point `w` property (backend density weight)', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const w = communityTrailsHeatLayerSpec().paint['heatmap-weight'] as any[];
    // ['interpolate', ['linear'], ['get','w'], 0, 0.15, ...]
    expect(w[0]).toBe('interpolate');
    expect(w[2]).toEqual(['get', 'w']);
  });

  it('color gradient starts FULLY TRANSPARENT so the basemap shows through (no fog)', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const color = communityTrailsHeatLayerSpec().paint['heatmap-color'] as any[];
    expect(color[0]).toBe('interpolate');
    // ['interpolate',['linear'],['heatmap-density'], 0, <transparent>, ...]
    expect(color[3]).toBe(0);
    expect(String(color[4]).replace(/\s/g, '')).toMatch(/,0\)$/); // rgba(...,0)
    // top of the ramp is the warm brand orange.
    expect(color[color.length - 1]).toBe('#ff8c42');
  });

  it('radius grows to a MODEST cap (~12px@z18) so dense corridors do not merge into a blob', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const r = communityTrailsHeatLayerSpec().paint['heatmap-radius'] as any[];
    // ['interpolate',['linear'],['zoom'], 6,v, 10,v, 13,v, 16,v, 18,v]
    const stops: Record<number, number> = {};
    for (let i = 3; i < r.length; i += 2) stops[r[i]] = r[i + 1];
    expect(stops[10]).toBe(4);
    // Capped well below the old 26px kernel that filled the Lez into a mass.
    expect(stops[18]).toBeLessThanOrEqual(14);
  });

  it('intensity DECAYS at street zoom (de-blob): peak mid, lower at z18 than its mid peak', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const intens = communityTrailsHeatLayerSpec().paint['heatmap-intensity'] as any[];
    const stops: Record<number, number> = {};
    for (let i = 3; i < intens.length; i += 2) stops[intens[i]] = intens[i + 1];
    // The old ramp climbed monotonically to 2.0@z18 → clamped the densest
    // corridors to solid orange. The de-blob design peaks by mid zoom and
    // decays after, so street zoom stays legible.
    const peak = Math.max(...Object.values(stops));
    expect(stops[18]).toBeLessThan(peak);
    expect(stops[18]).toBeLessThanOrEqual(0.5);
  });

  it('opacity ~0.85, opt-in dimmed via opacityMult (hero) — dim stays a VALID zoom ramp', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const base = communityTrailsHeatLayerSpec().paint['heatmap-opacity'] as any[];
    expect(base[0]).toBe('interpolate');
    expect(base[2]).toEqual(['zoom']);
    expect(base[4]).toBe(0.85); // z6 stop
    // Regression pin (hero raster heatmap silently not rendering): heatmap-opacity
    // is a ZOOM ramp, so the multiplier must NOT wrap it as `['*', mult, ramp]`
    // — maplibre forbids a `zoom` expression under `*` and drops the layer. It
    // must stay a top-level `interpolate` over `['zoom']`, with the multiplier
    // folded into the OUTPUT stops. This assertion FAILS on the old `['*', …]`
    // shape and PASSES on the fold-into-outputs fix.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const dim = communityTrailsHeatLayerSpec({ opacityMult: 0.9 }).paint['heatmap-opacity'] as any[];
    expect(dim[0]).toBe('interpolate'); // NOT '*'
    expect(dim[1]).toEqual(['linear']);
    expect(dim[2]).toEqual(['zoom']); // zoom is still the DIRECT top-level input
    // z6 output scaled by the multiplier: 0.9 * 0.85 = 0.765
    expect(dim[3]).toBe(6);
    expect(dim[4]).toBeCloseTo(0.765, 5);
    // Every output stop is a plain number (no illegal nested `zoom`/`*` wrapper).
    for (let i = 4; i < dim.length; i += 2) expect(typeof dim[i]).toBe('number');
  });

  it('the same sport chip filter applies (points carry `sport`)', () => {
    // The heat layer is filtered by communityHeatSportFilter just like the
    // lines — its point features carry the `sport` property.
    expect(communityHeatSportFilter('gravel')).toEqual(
      ['in', ['get', 'sport'], ['literal', ['gravel']]],
    );
  });

  it('LINE_CRISP_MINZOOM shows continuous lines from regional zoom (avoids the raster "dots" at the default view) but not at national overview', () => {
    // Lowered 14→9 (2026-08-07): the raster dotted out at the default hero zoom
    // (~z10-11); the continuous line layer from z9 fixes it. Kept >=6 so the
    // national overview stays raster-only (individual lines would be noise).
    expect(LINE_CRISP_MINZOOM).toBeGreaterThanOrEqual(6);
    expect(LINE_CRISP_MINZOOM).toBeLessThanOrEqual(11);
  });
});

describe('communityHeatSportFilter', () => {
  it("'all' clears the filter (null)", () => {
    expect(communityHeatSportFilter('all')).toBeNull();
  });

  it('plain sports filter on the sport property', () => {
    expect(communityHeatSportFilter('road')).toEqual(
      ['in', ['get', 'sport'], ['literal', ['road']]],
    );
    expect(communityHeatSportFilter('running')).toEqual(
      ['in', ['get', 'sport'], ['literal', ['running']]],
    );
  });

  it("'offroad' expands to mtb + offroad + gravel (mirrors backend expand_sport)", () => {
    expect(communityHeatSportFilter('offroad')).toEqual(
      ['in', ['get', 'sport'], ['literal', ['mtb', 'offroad', 'gravel']]],
    );
  });
});

/**
 * Minimal maplibre-expression evaluator — drives the REAL filter emitted by
 * communityDirectionArrowFilter() against a feature's properties (NOT an inline
 * mirror of the filter logic: it interprets whatever expression production emits,
 * so a gate change can't silently pass). Handles only the ops the filter uses.
 */
function evalFilterExpr(expr: unknown, props: Record<string, number | string>): unknown {
  if (!Array.isArray(expr)) return expr;
  const op = expr[0] as string;
  const a = (i: number) => evalFilterExpr(expr[i + 1], props);
  switch (op) {
    case 'get': return props[expr[1] as string];
    case 'literal': return expr[1];
    case 'all': return expr.slice(1).every((e) => evalFilterExpr(e, props) === true);
    case '>=': return (a(0) as number) >= (a(1) as number);
    case '+': return (a(0) as number) + (a(1) as number);
    case '-': return (a(0) as number) - (a(1) as number);
    case '*': return (a(0) as number) * (a(1) as number);
    case 'abs': return Math.abs(a(0) as number);
    case 'in': return (a(1) as unknown[]).includes(a(0));
    default: throw new Error(`evalFilterExpr: unhandled op "${op}"`);
  }
}

function arrowShows(props: Record<string, number | string>): boolean {
  return evalFilterExpr(communityDirectionArrowFilter(), props) === true;
}

describe('direction-arrow layer — one-way MTB/gravel singletrack', () => {
  // High oneway_score + high pass_count: under the OLD gate (oneway_score>=0.6 &&
  // pass_count>=3) EVERY feature below would show an arrow regardless of the
  // fwd/bwd split — so the 60/40 and 50/50 "no arrow" cases FAIL on the old gate
  // and PASS on the new dominance gate. That's the non-regression signal.
  const base = { sport: 'mtb', oneway_score: 0.95, pass_count: 100 };

  it('DOMINANCE_THRESHOLD gates ~90%-one-way (≥90%), documented ratio→% mapping', () => {
    // dominance = 2p-1 ⇒ p = (1+dominance)/2. Gate 0.8 ⇒ p ≥ 0.9 (90% one-way).
    expect(DOMINANCE_THRESHOLD).toBe(0.8);
    expect((1 + DOMINANCE_THRESHOLD) / 2).toBeCloseTo(0.9, 6);
  });

  it('a 95/5 trail (dominance 0.90) SHOWS an arrow', () => {
    expect(arrowShows({ ...base, forward_count: 95, backward_count: 5 })).toBe(true);
  });

  it('a 60/40 trail (dominance 0.20) shows NO arrow — weakly directional is noise', () => {
    // This FAILS on the old oneway_score>=0.6 gate (a 60/40 with high R passed);
    // the dominance ratio is the new primary gate.
    expect(arrowShows({ ...base, forward_count: 60, backward_count: 40 })).toBe(false);
  });

  it('a 50/50 trail (dominance 0.00) shows NO arrow (balanced suppression)', () => {
    expect(arrowShows({ ...base, forward_count: 50, backward_count: 50 })).toBe(false);
  });

  it('a 90/10 trail sits exactly at the gate (dominance 0.80) and SHOWS', () => {
    expect(arrowShows({ ...base, forward_count: 90, backward_count: 10 })).toBe(true);
  });

  it('too few directional passes shows NO arrow even when 100% one-way (guards div-by-zero)', () => {
    // fwd+bwd = 2 < ARROW_MIN_PASS_COUNT (3): not enough confirmation.
    expect(arrowShows({ ...base, forward_count: 2, backward_count: 0 })).toBe(false);
    // ...and a 0/0 degenerate feature is filtered out (no 0/0 division).
    expect(arrowShows({ ...base, forward_count: 0, backward_count: 0 })).toBe(false);
    // 3 passes, fully one-way → shows.
    expect(arrowShows({ ...base, forward_count: 3, backward_count: 0 })).toBe(true);
  });

  it('road/running never arrow, whatever the dominance', () => {
    expect(arrowShows({ sport: 'road', oneway_score: 1, forward_count: 100, backward_count: 0 })).toBe(false);
    expect(arrowShows({ sport: 'running', oneway_score: 1, forward_count: 100, backward_count: 0 })).toBe(false);
    // sport clause literal never contains road/running.
    const sportClause = communityDirectionArrowFilter()[3] as unknown[];
    const sports = (sportClause[2] as unknown[])[1] as string[];
    expect(sports).toEqual(['mtb', 'gravel']);
  });

  it('filter uses the NO-DIVISION dominance form |fwd-bwd| >= T*(fwd+bwd) (no float div in expr)', () => {
    const filter = communityDirectionArrowFilter() as unknown[];
    // clause 0: fwd+bwd >= ARROW_MIN_PASS_COUNT; clause 1: |fwd-bwd| >= T*(fwd+bwd)
    expect(filter[1]).toEqual(['>=', ['+', ['get', 'forward_count'], ['get', 'backward_count']], ARROW_MIN_PASS_COUNT]);
    expect(filter[2]).toEqual([
      '>=',
      ['abs', ['-', ['get', 'forward_count'], ['get', 'backward_count']]],
      ['*', DOMINANCE_THRESHOLD, ['+', ['get', 'forward_count'], ['get', 'backward_count']]],
    ]);
    // no '/' anywhere in the filter (float division avoided).
    expect(JSON.stringify(filter)).not.toContain('"/"');
  });

  it('is a line-placed symbol layer over the trails source-layer at street zoom', () => {
    const spec = communityTrailsArrowLayerSpec();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const layout = spec.layout as Record<string, any>;
    expect(spec.id).toBe(COMMUNITY_TRAILS_ARROW_LAYER);
    expect(spec.type).toBe('symbol');
    expect(spec.source).toBe(COMMUNITY_TRAILS_SOURCE);
    expect(spec['source-layer']).toBe('trails');
    expect(spec.minzoom).toBe(ARROW_MINZOOM);
    expect(ARROW_MINZOOM).toBeGreaterThanOrEqual(13);
    expect(layout['symbol-placement']).toBe('line');
    expect(layout['icon-image']).toBe('direction-arrow');
    // maplibre auto-rotates line-placed symbols to the coordinate order — we
    // must NOT emit our own rotation (would double-rotate).
    expect(layout['icon-rotate']).toBeUndefined();
    expect(spec.filter).toEqual(communityDirectionArrowFilter());
  });

  it('starts hidden by default; opt-in visible', () => {
    expect(communityTrailsArrowLayerSpec().layout.visibility).toBe('none');
    expect(communityTrailsArrowLayerSpec({ visible: true }).layout.visibility).toBe('visible');
  });
});

describe('direction-arrow dominance — the DOMINANT direction reads stronger', () => {
  // Minimal 1-D linear interpolate evaluator (clamped) mirroring maplibre's
  // ['interpolate',['linear'],['get',k], x0,y0, x1,y1, ...] over a scalar input.
  function evalInterp(expr: unknown[], input: number): number {
    const stops = expr.slice(3);
    if (input <= (stops[0] as number)) return stops[1] as number;
    for (let i = 0; i < stops.length - 2; i += 2) {
      const x0 = stops[i] as number, y0 = stops[i + 1] as number;
      const x1 = stops[i + 2] as number, y1 = stops[i + 3] as number;
      if (input <= x1) return y0 + ((y1 - y0) * (input - x0)) / (x1 - x0);
    }
    return stops[stops.length - 1] as number;
  }

  it('opacity + size ramps are data-driven on oneway_score', () => {
    expect(ARROW_OPACITY_RAMP[0]).toBe('interpolate');
    expect(ARROW_OPACITY_RAMP[2]).toEqual(['get', 'oneway_score']);
    expect(ARROW_SIZE_RAMP[0]).toBe('interpolate');
    expect(ARROW_SIZE_RAMP[2]).toEqual(['get', 'oneway_score']);
  });

  it('the ramp domain is re-centered to the higher ARROW_RAMP_FLOOR (still > the old 0.6 gate)', () => {
    // Since only strongly-dominant trails survive the ratio gate, the ramps start
    // at ARROW_RAMP_FLOOR (0.8) not the old 0.6, so shown arrows keep contrast.
    expect(ARROW_RAMP_FLOOR).toBe(0.8);
    expect(ARROW_OPACITY_RAMP[3]).toBe(ARROW_RAMP_FLOOR);
    expect(ARROW_SIZE_RAMP[3]).toBe(ARROW_RAMP_FLOOR);
  });

  it('a barely-past-floor trail draws FAINTER + SMALLER than a strongly one-way one', () => {
    // At the ramp floor (barely shown) vs a strongly one-way descent (R→1):
    const faint = evalInterp(ARROW_OPACITY_RAMP as unknown[], ARROW_RAMP_FLOOR);
    const bold = evalInterp(ARROW_OPACITY_RAMP as unknown[], 1.0);
    const small = evalInterp(ARROW_SIZE_RAMP as unknown[], ARROW_RAMP_FLOOR);
    const large = evalInterp(ARROW_SIZE_RAMP as unknown[], 1.0);
    expect(faint).toBeLessThan(bold);
    expect(small).toBeLessThan(large);
    // Full opacity + full-or-larger size at maximum dominance.
    expect(bold).toBeCloseTo(1.0, 6);
    expect(large).toBeGreaterThanOrEqual(1.0);
  });

  it('the spec wires the ramps: icon-opacity (paint) + icon-size (layout)', () => {
    const spec = communityTrailsArrowLayerSpec();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((spec.paint as Record<string, any>)['icon-opacity']).toBe(ARROW_OPACITY_RAMP);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((spec.layout as Record<string, any>)['icon-size']).toBe(ARROW_SIZE_RAMP);
  });

  it('near-balance is gated OUT (no arrow): dominance ratio is the OFF-switch', () => {
    // The dominance gate is the OFF-switch for balanced trails (the "✱ both ways"
    // artifact): a near-50/50 (55/45, dominance 0.10) is filtered out, while a
    // strongly one-way (95/5, dominance 0.90) is arrowed.
    const balanced = { sport: 'mtb', oneway_score: 0.62, pass_count: 100, forward_count: 55, backward_count: 45 };
    const strong = { sport: 'mtb', oneway_score: 0.98, pass_count: 100, forward_count: 95, backward_count: 5 };
    expect(evalFilterExpr(communityDirectionArrowFilter(), balanced)).toBe(false);
    expect(evalFilterExpr(communityDirectionArrowFilter(), strong)).toBe(true);
  });
});
