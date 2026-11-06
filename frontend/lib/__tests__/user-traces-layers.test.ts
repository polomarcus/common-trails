/**
 * Non-regression: "Mes traces" must not turn into a solid blob at low zoom.
 *
 * Beta bug (Sète/Montpellier, 2026-11, ~z10-11): the user-activities glow was a
 * fixed 14 px / blur 6 / opacity 0.18 and the line a fixed 3 px / 0.92 at every
 * zoom — thousands of traces merged into one blue/purple blob with halos. These
 * tests drive the REAL layer setup (`addMapSourcesAndLayers`) and the REAL
 * runtime opacity path used by useMapLayerSync (`applyUserTraceOpacity`); they
 * fail on the old constants and pass with the zoom ramps.
 */
import { readFileSync } from 'fs';
import path from 'path';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { addMapSourcesAndLayers } from '@/lib/init-map-layers';
import { applyUserTraceOpacity } from '@/lib/user-traces-layers';
import { lineParts, activityDisplayGeometry } from '@/lib/map-utils';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Expr = any;

/** Evaluate a numeric paint value at `zoom` (constants, and the
 *  linear/exponential zoom interpolations maplibre supports). */
function evalAtZoom(expr: Expr, zoom: number): number {
  if (typeof expr === 'number') return expr;
  expect(Array.isArray(expr) && expr[0] === 'interpolate').toBe(true);
  expect(expr[2]).toEqual(['zoom']);
  const [kind, base = 1] = expr[1] as [string, number?];
  const stops: [number, Expr][] = [];
  for (let i = 3; i < expr.length; i += 2) stops.push([expr[i], expr[i + 1]]);
  const out = (v: Expr) => {
    // withMult folds a multiplier into the outputs: ['*', k, n] or plain n.
    if (Array.isArray(v) && v[0] === '*') return v[1] * v[2];
    return v as number;
  };
  if (zoom <= stops[0][0]) return out(stops[0][1]);
  for (let i = 0; i < stops.length - 1; i++) {
    const [z0, v0] = stops[i];
    const [z1, v1] = stops[i + 1];
    if (zoom <= z1) {
      let t = (zoom - z0) / (z1 - z0);
      if (kind === 'exponential' && base !== 1) {
        t = (Math.pow(base, zoom - z0) - 1) / (Math.pow(base, z1 - z0) - 1);
      }
      return out(v0) + (out(v1) - out(v0)) * t;
    }
  }
  return out(stops[stops.length - 1][1]);
}

function isZoomExpr(expr: Expr): boolean {
  return Array.isArray(expr) && expr[0] === 'interpolate' &&
    Array.isArray(expr[2]) && expr[2][0] === 'zoom';
}

function captureLayers(): Record<string, { paint: Record<string, Expr> }> {
  const layers: Record<string, { paint: Record<string, Expr> }> = {};
  const m = {
    addSource: () => {},
    addLayer: (spec: { id: string; paint?: Record<string, Expr> }) => {
      layers[spec.id] = { paint: spec.paint ?? {} };
    },
    addImage: () => {},
    hasImage: () => false,
    getSource: () => undefined,
    on: () => {},
  };
  addMapSourcesAndLayers(m);
  return layers;
}

describe('user-activities paint — zoom-dependent (no low-zoom blob)', () => {
  let errorSpy: ReturnType<typeof vi.spyOn>;
  let warnSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    // Unrelated blocks (sprite canvas, PMTiles pointer) fail without browser
    // globals; safeBlock logs and carries on — exactly what we rely on here.
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    (globalThis as Record<string, unknown>).fetch = () => Promise.reject(new Error('offline'));
  });
  afterEach(() => {
    errorSpy.mockRestore();
    warnSpy.mockRestore();
    delete (globalThis as Record<string, unknown>).fetch;
  });

  it('glow width / blur / opacity are zoom ramps: thin and invisible at z10, legacy at z15', () => {
    const glow = captureLayers()['user-activities-glow'];
    expect(glow).toBeDefined();
    for (const prop of ['line-width', 'line-blur', 'line-opacity']) {
      expect(isZoomExpr(glow.paint[prop]), prop).toBe(true);
    }
    expect(evalAtZoom(glow.paint['line-width'], 10)).toBeLessThanOrEqual(3);
    expect(evalAtZoom(glow.paint['line-blur'], 10)).toBeLessThanOrEqual(2);
    expect(evalAtZoom(glow.paint['line-opacity'], 9)).toBe(0);
    expect(evalAtZoom(glow.paint['line-opacity'], 10)).toBeLessThan(0.08);
    expect(evalAtZoom(glow.paint['line-width'], 15)).toBe(14);
    expect(evalAtZoom(glow.paint['line-blur'], 15)).toBe(6);
    expect(evalAtZoom(glow.paint['line-opacity'], 15)).toBeCloseTo(0.18);
  });

  it('main line width / opacity are zoom ramps: hairline at z10, legacy at z15', () => {
    const line = captureLayers()['user-activities-line'];
    expect(line).toBeDefined();
    expect(isZoomExpr(line.paint['line-width'])).toBe(true);
    expect(isZoomExpr(line.paint['line-opacity'])).toBe(true);
    expect(evalAtZoom(line.paint['line-width'], 10)).toBeLessThan(1.2);
    expect(evalAtZoom(line.paint['line-opacity'], 10)).toBeLessThan(0.7);
    expect(evalAtZoom(line.paint['line-width'], 15)).toBeGreaterThanOrEqual(3);
    expect(evalAtZoom(line.paint['line-opacity'], 15)).toBeCloseTo(0.92);
  });
});

describe('runtime opacity (selection dim/restore in useMapLayerSync) keeps the ramps', () => {
  function recordPaint(dimmed: boolean): Record<string, Expr> {
    const set: Record<string, Expr> = {};
    applyUserTraceOpacity({
      setPaintProperty: (layer: string, prop: string, value: Expr) => { set[`${layer}:${prop}`] = value; },
    }, dimmed);
    return set;
  }

  it('restore sets zoom ramps, not constants', () => {
    const set = recordPaint(false);
    const line = set['user-activities-line:line-opacity'];
    const glow = set['user-activities-glow:line-opacity'];
    expect(isZoomExpr(line)).toBe(true);
    expect(isZoomExpr(glow)).toBe(true);
    expect(evalAtZoom(line, 15)).toBeCloseTo(0.92);
    expect(evalAtZoom(glow, 9)).toBe(0);
  });

  it('dim scales the ramps (still zoom expressions, dimmer at every zoom)', () => {
    const dim = recordPaint(true);
    const full = recordPaint(false);
    for (const key of ['user-activities-line:line-opacity', 'user-activities-glow:line-opacity']) {
      expect(isZoomExpr(dim[key]), key).toBe(true);
      for (const z of [10, 12, 15]) {
        const d = evalAtZoom(dim[key], z);
        const f = evalAtZoom(full[key], z);
        expect(d).toBeLessThanOrEqual(f);
        if (f > 0) expect(d).toBeLessThan(f);
      }
    }
    expect(evalAtZoom(dim['user-activities-line:line-opacity'], 15)).toBeCloseTo(0.25, 1);
  });

  it('useMapLayerSync never writes a constant opacity on user-activities layers', () => {
    const src = readFileSync(path.resolve(__dirname, '../../hooks/useMapLayerSync.ts'), 'utf8');
    expect(src).toContain('applyUserTraceOpacity(m, true)');
    expect(src).toContain('applyUserTraceOpacity(m, false)');
    expect(src).not.toMatch(/setPaintProperty\(\s*'user-activities-(line|glow)'\s*,\s*'line-opacity'\s*,\s*[\d.]+\s*\)/);
  });
});

describe('MultiLineString traces (API breaks at GPS gaps)', () => {
  const a = [[3.69, 43.4], [3.691, 43.4]];
  const b = [[3.756, 43.4], [3.757, 43.4]];

  it('lineParts handles LineString and MultiLineString', () => {
    expect(lineParts({ type: 'LineString', coordinates: a })).toEqual([a]);
    expect(lineParts({ type: 'MultiLineString', coordinates: [a, b] })).toEqual([a, b]);
    expect(lineParts({ type: 'Point', coordinates: [3, 43] })).toEqual([]);
    expect(lineParts(null)).toEqual([]);
  });

  it('selected activity is drawn with its gaps, not bridged', () => {
    const coords = [...a, ...b];
    expect(activityDisplayGeometry({ coords, parts: [a, b] }))
      .toEqual({ type: 'MultiLineString', coordinates: [a, b] });
    expect(activityDisplayGeometry({ coords: a }))
      .toEqual({ type: 'LineString', coordinates: a });
  });
});
