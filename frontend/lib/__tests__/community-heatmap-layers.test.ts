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
 * by a broken source) AND the per-visitor DB load (static file).
 *
 * Thin lines only (2026-11-15, feat/heatmap-traits-fins): that density layer is
 * GONE again — up to z13 its blurred kernel drew a 10–16 px halo around every
 * trace and at z9 the halos merged into a solid pink mat. The community heatmap
 * is now the crisp `line` layer over `trails` alone, one hue, light = rare →
 * dark = frequent. These tests pin: no spec builds a `heatmap` layer; line/arrow
 * specs stay line/symbol over `trails`; the colour ramp is single-hue with
 * strictly decreasing luminance; widths/opacities grow with popularity.
 */
import { describe, it, expect } from 'vitest';
import * as communityLayers from '../community-heatmap-layers';
import {
  communityTrailsSourceSpec,
  communityTrailsGlowLayerSpec,
  communityTrailsLineLayerSpec,
  communityTrailsArrowLayerSpec,
  communityHeatSportFilter,
  applyCommunityHeatSportFilter,
  COMMUNITY_TRAILS_SPORT_FILTERED_LAYERS,
  communityDirectionArrowFilter,
  COMMUNITY_TRAILS_SOURCE,
  COMMUNITY_TRAILS_GLOW_LAYER,
  COMMUNITY_TRAILS_LINE_LAYER,
  COMMUNITY_TRAILS_ARROW_LAYER,
  DOMINANCE_THRESHOLD,
  ARROW_MIN_PASS_COUNT,
  ARROW_RAMP_FLOOR,
  ARROW_MINZOOM,
  ARROW_OPACITY_RAMP,
  ARROW_SIZE_RAMP,
  LINE_CRISP_MINZOOM,
  LINE_OPACITY_RAMP,
  GLOW_OPACITY_RAMP,
  LINE_WIDTH_RAMP,
  HEAT_RAMP_STOPS,
  HEAT_RAMP_LEGEND_STOPS,
  HEAT_RAMP_CSS,
  HERO_HEATMAP,
  withMult,
} from '../community-heatmap-layers';
import { IGN_PLANV2_STYLE, HYBRID_STYLE, BASEMAPS } from '../basemap-styles';

/** Output (a heat_score ramp) of a zoom-interpolate paint value at an exact zoom stop. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function lineOpacityAt(ramp: any[], zoom: number): any[] {
  for (let i = 3; i < ramp.length; i += 2) if (ramp[i] === zoom) return ramp[i + 1];
  throw new Error(`no zoom stop ${zoom}`);
}

/** True when `expr` contains a `['zoom']` expression anywhere. */
function containsZoom(expr: unknown): boolean {
  if (!Array.isArray(expr)) return false;
  if (expr.length === 1 && expr[0] === 'zoom') return true;
  return expr.some(containsZoom);
}

/** maplibre's rule: `['zoom']` may ONLY be the direct input of a TOP-LEVEL
 *  interpolate/step. Anything else (e.g. ['*', mult, <zoom-ramp>]) fails style
 *  validation and the layer is silently dropped. */
function zoomIsLegal(expr: unknown): boolean {
  if (!Array.isArray(expr)) return true;
  if ((expr[0] === 'interpolate' || expr[0] === 'step') && Array.isArray(expr[2]) && expr[2][0] === 'zoom') {
    return !containsZoom(expr.slice(3));
  }
  if (expr[0] === 'step' && Array.isArray(expr[1]) && expr[1][0] === 'zoom') {
    return !containsZoom(expr.slice(2));
  }
  return !containsZoom(expr);
}

describe('withMult (SSOT opacity scaler — used by the /map hooks at runtime)', () => {
  it('mult===1 returns the ramp unchanged (byte-identical /map default)', () => {
    expect(withMult(LINE_WIDTH_RAMP, 1)).toBe(LINE_WIDTH_RAMP);
    expect(withMult(LINE_OPACITY_RAMP, 1)).toBe(LINE_OPACITY_RAMP);
  });

  it('a ZOOM ramp × mult stays a top-level interpolate (NOT the invalid [*, mult, zoom-ramp])', () => {
    // maplibre REJECTS ['*', mult, <zoom-interpolate>] (silently drops the paint
    // + fires an error) — the bug that once stopped the hero's density layer
    // rendering. withMult must fold the multiplier into the OUTPUT stops instead.
    const zoomRamp = ['interpolate', ['linear'], ['zoom'], 6, 0.85, 18, 0.25];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const dim = withMult(zoomRamp, 0.35) as any[];
    expect(dim[0]).toBe('interpolate');
    expect(dim[2]).toEqual(['zoom']);
    // the z6 stop 0.85 is scaled to 0.85*0.35, folded into the output, not wrapped.
    expect(dim[4]).toBeCloseTo(0.85 * 0.35, 6);
    expect(zoomIsLegal(dim)).toBe(true);
    // The real zoom ramp in use (width × the hero's widthMult): each per-zoom
    // heat_score ramp gets wrapped, the zoom input stays top-level.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const w = withMult(LINE_WIDTH_RAMP, 0.6) as any[];
    expect(w.slice(0, 3)).toEqual(['interpolate', ['exponential', 1.5], ['zoom']]);
    for (let i = 4; i < w.length; i += 2) {
      expect(w[i].slice(0, 2)).toEqual(['*', 0.6]);
      expect(w[i][2][2]).toEqual(['get', 'heat_score']);
    }
    expect(zoomIsLegal(w)).toBe(true);
  });

  it('a DATA-driven ramp × mult keeps the simple [*, mult, ramp] wrapper (legal there)', () => {
    // GLOW_OPACITY_RAMP keys off ['get','heat_score'] — a ['*'] wrapper is valid.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const dim = withMult(GLOW_OPACITY_RAMP, 0.35) as any[];
    expect(dim[0]).toBe('*');
    expect(dim[1]).toBe(0.35);
  });

  it('the LINE opacity is data-driven only: × mult is [*, mult, ramp], with no zoom anywhere', () => {
    // No zoom dependence any more (the density layer it was fighting is gone),
    // so the "Apparence" / route-mode / hero multipliers wrap it directly.
    expect(containsZoom(LINE_OPACITY_RAMP)).toBe(false);
    expect(withMult(LINE_OPACITY_RAMP, 0.35)).toEqual(['*', 0.35, LINE_OPACITY_RAMP]);
    expect(zoomIsLegal(withMult(LINE_OPACITY_RAMP, 0.35))).toBe(true);
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
  it('glow + line specs are LINE layers over the PMTiles "trails" source-layer', () => {
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
    // The ramp validated live on prod (2026-11-15): data-driven only, stops over
    // the heat_score range the data actually spans (0.2–0.85).
    expect(line.paint['line-opacity']).toBe(LINE_OPACITY_RAMP);
    expect(line.paint['line-opacity']).toEqual(
      ['interpolate', ['linear'], ['get', 'heat_score'], 0.2, 0.5, 0.45, 0.75, 0.65, 0.9, 0.85, 1]);
  });

  it('hero options: visible, custom minzoom, opacity ramp multiplied', () => {
    const spec = communityTrailsLineLayerSpec({ visible: true, minzoom: 8, opacityMult: 0.9 });
    expect(spec.layout.visibility).toBe('visible');
    expect(spec.minzoom).toBe(8);
    // Data-driven ramp: a plain ['*', mult, ramp] wrapper (legal, no zoom inside).
    expect(spec.paint['line-opacity']).toEqual(['*', 0.9, LINE_OPACITY_RAMP]);
  });

  it('every line-layer paint value is legal for maplibre (zoom only as a top-level input)', () => {
    for (const opts of [{}, { opacityMult: HERO_HEATMAP.lineOpacity, widthMult: HERO_HEATMAP.lineWidth }]) {
      const paint = communityTrailsLineLayerSpec(opts).paint as Record<string, unknown>;
      for (const [prop, v] of Object.entries(paint)) {
        expect(zoomIsLegal(v), `${prop} ${JSON.stringify(opts)}`).toBe(true);
      }
    }
  });

  // Non-régression (Paul, 2026-11-11) : la retouche de lisibilité de /map avait
  // épaissi et opacifié la heatmap de l'accueil, qui noyait le titre et les CTA.
  it('accueil : la heatmap de fond est plus fine et plus transparente que sur /map', () => {
    const map = communityTrailsLineLayerSpec({ visible: true, minzoom: 9 });
    const hero = communityTrailsLineLayerSpec({
      visible: true, minzoom: 9,
      opacityMult: HERO_HEATMAP.lineOpacity, widthMult: HERO_HEATMAP.lineWidth,
    });
    expect(HERO_HEATMAP.lineOpacity).toBeLessThanOrEqual(0.6);
    expect(HERO_HEATMAP.lineWidth).toBeLessThanOrEqual(0.7);
    // The hero has no density layer any more, hence no density multiplier.
    expect(HERO_HEATMAP).not.toHaveProperty('heatOpacity');
    // Width stays a top-level ZOOM interpolate with the multiplier folded into
    // each stop (a wrapper around a zoom ramp is silently dropped by maplibre).
    const w = hero.paint['line-width'];
    expect(w[0]).toBe('interpolate');
    expect(w[2]).toEqual(['zoom']);
    for (let i = 4; i < w.length; i += 2) expect(w[i][0]).toBe('*');
    expect(hero.paint['line-opacity']).toEqual(['*', HERO_HEATMAP.lineOpacity, LINE_OPACITY_RAMP]);
    // /map is untouched: default multipliers → the SSOT ramps themselves.
    expect(map.paint['line-width']).toBe(LINE_WIDTH_RAMP);
    expect(map.paint['line-opacity']).toBe(LINE_OPACITY_RAMP);
    // At the hero's zoom the most popular corridor stays under 1 px and the
    // most opaque line under 0.6 — a background, not the subject.
    const top = (ramp: unknown[], z: number) => (lineOpacityAt(ramp as unknown[], z) as unknown[])[2] as unknown[];
    expect((top(hero.paint['line-width'], 10)[10] as number) * HERO_HEATMAP.lineWidth).toBeLessThanOrEqual(1);
    expect((LINE_OPACITY_RAMP[LINE_OPACITY_RAMP.length - 1] as number) * HERO_HEATMAP.lineOpacity)
      .toBeLessThanOrEqual(0.6);
  });

  it('traits fins : hairlines at regional zoom, widths grow with zoom AND popularity', () => {
    // Non-régression (2026-11-15) : à z8, ~55k traits de 0.8–2.6 px remplissaient
    // des zones entières. Les traits fins (0.4–1.2 px) gardent un réseau lisible.
    const line = communityTrailsLineLayerSpec();
    const w = line.paint['line-width'];
    expect(w.slice(0, 3)).toEqual(['interpolate', ['exponential', 1.5], ['zoom']]);
    const zooms: number[] = [];
    for (let i = 3; i < w.length; i += 2) zooms.push(w[i]);
    expect(zooms).toEqual([8, 10, 13, 16, 18]);
    const widths = (z: number) => {
      const r = lineOpacityAt(w, z) as unknown[];
      expect(r.slice(0, 3)).toEqual(['interpolate', ['linear'], ['get', 'heat_score']]);
      return [r[4], r[6], r[8], r[10]] as number[];
    };
    expect(widths(8)).toEqual([0.4, 0.55, 0.8, 1.2]);
    expect(widths(8)[3]).toBeLessThanOrEqual(1.2); // hairlines, even the busiest
    expect(widths(18)[3]).toBeLessThanOrEqual(7);   // still no fat blob at street zoom
    for (const z of zooms) {
      const ws = widths(z);
      for (let i = 1; i < ws.length; i++) expect(ws[i], `z${z} popularity`).toBeGreaterThan(ws[i - 1]);
    }
    for (let k = 1; k < zooms.length; k++) {
      const [lo, hi] = [widths(zooms[k - 1]), widths(zooms[k])];
      for (let i = 0; i < lo.length; i++) expect(hi[i], `z${zooms[k]} > z${zooms[k - 1]}`).toBeGreaterThan(lo[i]);
    }
  });

  it('opacity grows with popularity: rare traces half-transparent, frequent ones opaque', () => {
    const op = LINE_OPACITY_RAMP as unknown[];
    const outs = [op[4], op[6], op[8], op[10]] as number[];
    for (let i = 1; i < outs.length; i++) expect(outs[i]).toBeGreaterThan(outs[i - 1]);
    expect(outs[0]).toBeGreaterThanOrEqual(0.5);
    expect(outs[outs.length - 1]).toBe(1);
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

  it('low-density desire lines stay visible (LINE layer opacity at its lowest stop)', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const op = communityTrailsLineLayerSpec().paint['line-opacity'] as any[];
    // ['interpolate',['linear'],['get','heat_score'], 0.2, v, …]: the rarest
    // traces (and anything below, clamped) must remain clearly visible.
    expect(op[2]).toEqual(['get', 'heat_score']);
    expect(op[4]).toBeGreaterThanOrEqual(0.4);
  });
});

/** WCAG relative luminance of a #rrggbb colour (0 = black, 1 = white). */
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** HSL hue of a #rrggbb colour, in degrees [0, 360). */
function hue(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b);
  const d = max - Math.min(r, g, b);
  if (d === 0) return 0;
  const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return (h * 60 + 360) % 360;
}

/**
 * Lisibilité (2026-11-15, mesuré sur la prod, Montpellier) : la heatmap commune
 * n'est plus que des traits fins, d'un seul ton, clair = rare → foncé = fréquent.
 * Each test here FAILS on the previous code: a `heatmap` density layer spec
 * existed (its blurred halos merged into a pink mat up to z13), and the
 * plum → pink → orange ramp neither kept one hue nor darkened with frequency.
 */
describe('lisibilité — traits fins, un seul ton, clair = rare → foncé = fréquent', () => {
  it('no community layer spec builds a blurred `heatmap` density layer', () => {
    const specFactories = Object.entries(communityLayers)
      .filter(([name, v]) => name.endsWith('LayerSpec') && typeof v === 'function');
    // Guard against a vacuous pass: the line/glow/arrow factories are found.
    expect(specFactories.map(([name]) => name)).toEqual(
      expect.arrayContaining(['communityTrailsLineLayerSpec', 'communityTrailsArrowLayerSpec']));
    for (const [name, factory] of specFactories) {
      const spec = (factory as (o: object) => { type: string; 'source-layer'?: string })({ visible: true });
      expect(spec.type, name).not.toBe('heatmap');
      expect(spec['source-layer'], name).not.toBe('heat_points');
    }
    expect(Object.keys(communityLayers).some((k) => /^HEAT_(INTENSITY|RADIUS|WEIGHT|COLOR|OPACITY)_RAMP$/.test(k)))
      .toBe(false);
  });

  it('the line colour is exactly the ramp validated on prod (light pink → dark burgundy)', () => {
    expect(communityTrailsLineLayerSpec().paint['line-color']).toEqual(
      ['interpolate', ['linear'], ['get', 'heat_score'],
        0.2, '#f5b3cf', 0.4, '#e0619e', 0.6, '#c2185b', 0.75, '#8e0f4a', 0.9, '#5c0a33']);
  });

  it('luminance strictly DECREASES along the stops: light = rare, dark = frequent', () => {
    const lum = HEAT_RAMP_STOPS.map(([, c]) => luminance(c));
    for (let i = 1; i < lum.length; i++) {
      expect(lum[i], `${HEAT_RAMP_STOPS[i][1]} darker than ${HEAT_RAMP_STOPS[i - 1][1]}`).toBeLessThan(lum[i - 1]);
    }
    // A real light → dark spread, not five near-identical pinks.
    expect(lum[0] / lum[lum.length - 1]).toBeGreaterThan(10);
  });

  it('the ramp is ONE hue (every stop within 15° of the others)', () => {
    const hues = HEAT_RAMP_STOPS.map(([, c]) => hue(c));
    for (const a of hues) {
      for (const b of hues) {
        const d = Math.abs(a - b);
        expect(Math.min(d, 360 - d)).toBeLessThanOrEqual(15);
      }
    }
  });

  it('stops sit where the data is (heat_score ≈ 0.2–0.85 on prod), strictly increasing', () => {
    const pos = HEAT_RAMP_STOPS.map(([p]) => p);
    for (let i = 1; i < pos.length; i++) expect(pos[i]).toBeGreaterThan(pos[i - 1]);
    expect(pos[0]).toBeGreaterThanOrEqual(0.15);
    expect(pos[pos.length - 1]).toBeLessThanOrEqual(0.9);
  });

  it('the legend shows the whole ramp, left = rare (lightest) → right = frequent (darkest)', () => {
    expect(HEAT_RAMP_LEGEND_STOPS[0]).toEqual([0, HEAT_RAMP_STOPS[0][1]]);
    expect(HEAT_RAMP_LEGEND_STOPS[HEAT_RAMP_LEGEND_STOPS.length - 1])
      .toEqual([1, HEAT_RAMP_STOPS[HEAT_RAMP_STOPS.length - 1][1]]);
    expect(HEAT_RAMP_CSS.startsWith('linear-gradient(to right, #f5b3cf 0%, ')).toBe(true);
    expect(HEAT_RAMP_CSS.endsWith(', #5c0a33 100%)')).toBe(true);
  });

  it('the default IGN plan basemap is muted so the heatmap is the strongest colour on screen', () => {
    const ign = (IGN_PLANV2_STYLE.layers as Array<{ id: string; paint?: Record<string, number> }>)
      .find((l) => l.id === 'ign-planv2-layer');
    expect(ign?.paint).toEqual({
      'raster-saturation': -0.75, 'raster-contrast': -0.15, 'raster-brightness-min': 0.08,
    });
    expect(BASEMAPS.find((b) => b.key === 'ign')?.style).toBe(IGN_PLANV2_STYLE);
    // Only the plain IGN plan: the satellite + IGN hybrid keeps its own overlay.
    const hybridIgn = (HYBRID_STYLE.layers as Array<{ id: string; paint?: Record<string, number> }>)
      .find((l) => l.id === 'ign-planv2-layer');
    expect(hybridIgn?.paint).toEqual({ 'raster-opacity': 0.5 });
  });
});

describe('fusion des faisceaux — line-blur selon le nombre de traces superposées', () => {
  type Stops = Array<[number, number]>;
  // Evaluate the zoom → pass_count ramp at a given zoom stop (exact stops only).
  const blurAt = (zoom: number, passes: number): number => {
    const ramp = communityLayers.LINE_BLUR_RAMP as unknown[];
    expect(ramp[0]).toBe('interpolate');
    expect(ramp[2]).toEqual(['zoom']);
    for (let i = 3; i < ramp.length; i += 2) {
      if (ramp[i] !== zoom) continue;
      const out = ramp[i + 1];
      if (typeof out === 'number') return out;
      const inner = out as unknown[];
      expect(inner[2]).toEqual(['get', 'pass_count']);
      const stops: Stops = [];
      for (let j = 3; j < inner.length; j += 2) stops.push([inner[j] as number, inner[j + 1] as number]);
      if (passes <= stops[0][0]) return stops[0][1];
      for (let k = 1; k < stops.length; k++) {
        const [x0, y0] = stops[k - 1];
        const [x1, y1] = stops[k];
        if (passes <= x1) return y0 + ((y1 - y0) * (passes - x0)) / (x1 - x0);
      }
      return stops[stops.length - 1][1];
    }
    throw new Error(`no stop at z${zoom}`);
  };

  it('the line layer uses the SSOT blur ramp (was a flat 0: bundles drew as a hatched comb)', () => {
    const spec = communityLayers.communityTrailsLineLayerSpec({ visible: true }) as { paint: Record<string, unknown> };
    expect(spec.paint['line-blur']).toEqual(communityLayers.LINE_BLUR_RAMP);
  });

  it('no blur at regional zoom, whatever the bundle size', () => {
    expect(blurAt(13, 1)).toBe(0);
    expect(blurAt(13, 500)).toBe(0);
  });

  it('a lone trace stays crisp at street zoom, a busy bundle melts', () => {
    for (const z of [15, 17, 18]) {
      expect(blurAt(z, 1), `z${z} lone`).toBeLessThanOrEqual(0.3);
      expect(blurAt(z, 500), `z${z} bundle`).toBeGreaterThanOrEqual(1);
      expect(blurAt(z, 500)).toBeGreaterThan(blurAt(z, 15));
      expect(blurAt(z, 15)).toBeGreaterThan(blurAt(z, 3));
    }
  });

  it('never blurs wider than the thinnest line at that zoom (else the line fades instead of merging)', () => {
    // Thinnest width = LINE_WIDTH_RAMP's lowest heat stop, interpolated with its
    // exponential(1.5) zoom curve: z15 ≈ 1.27 px, z17 ≈ 1.84 px, z18 = 2.2 px.
    const thinnest: Record<number, number> = { 15: 1.27, 17: 1.84, 18: 2.2 };
    for (const [z, w] of Object.entries(thinnest)) {
      expect(blurAt(Number(z), 10_000), `z${z}`).toBeLessThanOrEqual(w);
    }
  });

  it('the hero scales the blur with its thinner lines (HERO_HEATMAP.lineWidth)', () => {
    const hero = communityLayers.communityTrailsLineLayerSpec({
      visible: true, widthMult: communityLayers.HERO_HEATMAP.lineWidth,
    }) as { paint: Record<string, unknown> };
    expect(hero.paint['line-blur']).toEqual(
      communityLayers.withMult(communityLayers.LINE_BLUR_RAMP, communityLayers.HERO_HEATMAP.lineWidth));
    expect(hero.paint['line-blur']).not.toEqual(communityLayers.LINE_BLUR_RAMP);
  });

  it('keys on the REAL overlap count (pass_count), not the capped popularity', () => {
    expect(JSON.stringify(communityLayers.LINE_BLUR_RAMP)).toContain('"pass_count"');
    expect(JSON.stringify(communityLayers.LINE_BLUR_RAMP)).not.toContain('"heat_score"');
  });
});

describe('line floor (LINE_CRISP_MINZOOM)', () => {
  it('LINE_CRISP_MINZOOM equals the SOURCE minzoom — lines draw wherever the tileset has data', () => {
    // 14→9 (2026-08-07), then 9→6 (2026-10-12). The z9 floor hid a dense network:
    // measured on the real prod tileset at z8.4 over Montpellier, the viewport
    // held 41 285 `trails` features and the map drew ZERO lines, leaving only the
    // ~3 px raster dots ("des petites bulles", Paul). The floor must therefore
    // never sit ABOVE the zoom at which the source starts serving features —
    // anything in between is data the tileset ships and the map silently hides.
    const source = communityTrailsSourceSpec('pmtiles://https://example.org/x.pmtiles');
    expect(LINE_CRISP_MINZOOM).toBe(source.minzoom);
  });

  it('the /map line layer is actually built with that floor (not a stale literal)', () => {
    // The constant is only worth pinning if the layer reads it: /map passes it
    // explicitly in lib/init-map-layers.ts and the home hero in app/page.tsx.
    expect(communityTrailsLineLayerSpec({ minzoom: LINE_CRISP_MINZOOM }).minzoom)
      .toBe(LINE_CRISP_MINZOOM);
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

/**
 * Non-regression (2026-08-15): the /map sport chips did NOTHING — `heatmapSport`
 * updated React state but nothing called `setFilter` on the map (home filtered
 * inline in app/page.tsx; /map's useMapLayerSync never did). The fix extracts the
 * SSOT helper `applyCommunityHeatSportFilter` that BOTH call. This drives the REAL
 * helper against a fake map and asserts it pushes the sport filter to the line +
 * hit layers — a filter that never reaches the map = the exact bug.
 */
describe('applyCommunityHeatSportFilter (SSOT — home + /map)', () => {
  function fakeMap() {
    const filters: Record<string, unknown> = {};
    const known = new Set(COMMUNITY_TRAILS_SPORT_FILTERED_LAYERS as readonly string[]);
    return {
      calls: filters,
      getLayer: (id: string) => (known.has(id) ? { id } : undefined),
      setFilter: (id: string, f: unknown) => { filters[id] = f; },
    };
  }

  it('filters exactly the line + hit layers (no density layer any more)', () => {
    expect([...COMMUNITY_TRAILS_SPORT_FILTERED_LAYERS]).toEqual([COMMUNITY_TRAILS_LINE_LAYER, 'community-trails-hit']);
  });

  it('pushes the sport filter to the line + hit layers', () => {
    const m = fakeMap();
    applyCommunityHeatSportFilter(m, 'gravel');
    for (const id of COMMUNITY_TRAILS_SPORT_FILTERED_LAYERS) {
      expect(m.calls[id], `layer ${id} must receive setFilter`).toEqual(
        ['in', ['get', 'sport'], ['literal', ['gravel']]],
      );
    }
  });

  it('offroad expands to mtb+offroad+gravel (matches communityHeatSportFilter)', () => {
    const m = fakeMap();
    applyCommunityHeatSportFilter(m, 'offroad');
    expect(m.calls[COMMUNITY_TRAILS_LINE_LAYER]).toEqual(communityHeatSportFilter('offroad'));
  });

  it("'all' clears the filter (setFilter(null)) on every layer", () => {
    const m = fakeMap();
    applyCommunityHeatSportFilter(m, 'all');
    for (const id of COMMUNITY_TRAILS_SPORT_FILTERED_LAYERS) {
      expect(m.calls[id]).toBeNull();
    }
  });

  it('is safe on a partial map (community layers skipped when URL unset)', () => {
    const m = { getLayer: () => undefined, setFilter: () => { throw new Error('should not be called'); } };
    expect(() => applyCommunityHeatSportFilter(m, 'road')).not.toThrow();
  });
});
