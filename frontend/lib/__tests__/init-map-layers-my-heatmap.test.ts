/**
 * "Ma heatmap" vs "Heatmap commune" (beta request, Thierry Crouzet): a rider
 * must be able to superpose HIS traces on the community heatmap and tell the
 * paths he rode from the paths others rode.
 *
 * Two map-side invariants, driven through the REAL addMapSourcesAndLayers:
 *   1. the user's traces are painted in ONE colour (MY_TRACES_COLOR, cyan) —
 *      not the per-sport colours that clashed with the plum→orange ramp;
 *   2. every user-trace layer is added AFTER every community layer, i.e. drawn
 *      ON TOP when both are visible.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { addMapSourcesAndLayers } from '@/lib/init-map-layers';
import { MY_TRACES_COLOR, SPORT_COLORS } from '@/lib/constants';
import { HEAT_RAMP_STOPS } from '@/lib/community-heatmap-layers';

interface LayerSpec { id: string; source?: string; paint?: Record<string, unknown> }

function makeMap() {
  const layers: LayerSpec[] = [];
  const m = {
    layers,
    addSource: () => {},
    addLayer: (spec: LayerSpec) => { layers.push(spec); },
    addImage: () => {},
    hasImage: () => false,
    getSource: () => undefined,
    on: () => {},
  };
  return m;
}

function installBrowserGlobals() {
  const g = globalThis as Record<string, unknown>;
  const ctx = {
    scale: () => {}, beginPath: () => {}, moveTo: () => {}, lineTo: () => {}, stroke: () => {},
    getImageData: () => ({ data: new Uint8ClampedArray(0) }),
  };
  g.document = { createElement: () => ({ width: 0, height: 0, getContext: () => ctx }) };
  g.window = { devicePixelRatio: 1, location: { origin: 'http://localhost:3787' } };
  g.fetch = () => Promise.reject(new Error('offline in tests'));
}

const USER_TRACE_LAYERS = ['user-activities-glow', 'user-activities-line', 'user-activities-hit'];

describe('/map — "Ma heatmap" layers vs the community heatmap', () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    installBrowserGlobals();
  });
  afterEach(() => {
    warnSpy.mockRestore();
    const g = globalThis as Record<string, unknown>;
    delete g.document;
    delete g.window;
    delete g.fetch;
  });

  it('paints the user glow + line in the single cyan MY_TRACES_COLOR (not per sport)', () => {
    const m = makeMap();
    addMapSourcesAndLayers(m);

    for (const id of ['user-activities-glow', 'user-activities-line']) {
      const spec = m.layers.find((l) => l.id === id);
      expect(spec, `${id} must be added`).toBeDefined();
      // A literal colour, not a ['get', 'color'] per-feature (per-sport) expression.
      expect(spec!.paint!['line-color']).toBe(MY_TRACES_COLOR);
    }
    expect(MY_TRACES_COLOR).toBe('#06b6d4');
  });

  it('the cyan is distinct from every community ramp stop and every sport colour', () => {
    const ramp = HEAT_RAMP_STOPS.map(([, c]) => c.toLowerCase());
    expect(ramp).not.toContain(MY_TRACES_COLOR.toLowerCase());
    expect(Object.values(SPORT_COLORS).map((c) => c.toLowerCase())).not.toContain(MY_TRACES_COLOR.toLowerCase());
  });

  it('adds every user-trace layer AFTER every community layer (drawn on top)', () => {
    const m = makeMap();
    addMapSourcesAndLayers(m);
    const ids = m.layers.map((l) => l.id);

    const communityIdx = m.layers
      .map((l, i) => (l.source === 'community-trails' ? i : -1))
      .filter((i) => i >= 0);
    // Guard against a vacuous pass: the community block must have run.
    expect(communityIdx.length).toBeGreaterThan(0);
    const lastCommunity = Math.max(...communityIdx);

    for (const id of USER_TRACE_LAYERS) {
      const idx = ids.indexOf(id);
      expect(idx, `${id} must be added`).toBeGreaterThanOrEqual(0);
      expect(idx, `${id} must be above the community heatmap`).toBeGreaterThan(lastCommunity);
    }
  });
});
