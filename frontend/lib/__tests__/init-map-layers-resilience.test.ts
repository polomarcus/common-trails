/**
 * Non-regression: ONE failing setup block must never blank the whole map.
 *
 * Prod bug (2026-10-12, Montpellier): `/map` rendered the basemap and NOTHING
 * else — no community heatmap, no DFCI, no user traces — while the home hero
 * (which reads the same PMTiles file) was fine. Cause: `addMapSourcesAndLayers`
 * was straight-line code whose FIRST statement, `registerDirectionArrow()`,
 * touches the canvas (`getImageData`) and calls `map.addImage()`. Either can
 * throw in the wild: `addImage` raises `RangeError: mismatched image size` when
 * the declared size disagrees with the bitmap (verified in maplibre-gl 4.3.2,
 * which wraps the bytes in `new RGBAImage({width, height}, …)`), and
 * `getImageData` raises SecurityError where canvas reads are blocked. The throw
 * aborted every block after it,
 * and `layersReady` was never set — so the Calques checkbox and the `H`
 * shortcut were inert too, with nothing in the UI to explain it.
 *
 * These tests fail on the old straight-line code and pass with `safeBlock`.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { addMapSourcesAndLayers } from '@/lib/init-map-layers';

const COMMUNITY_LAYERS = [
  'community-trails-heat',
  'community-trails-line',
  'community-trails-arrows',
  'community-trails-hit',
];

interface FakeMap {
  sources: string[];
  layers: string[];
  images: string[];
  lastImage?: { width: number; height: number; data: ArrayLike<number> };
  addSource: (id: string, spec: unknown) => void;
  addLayer: (spec: { id: string }) => void;
  addImage: (id: string, img: { width: number; height: number; data: ArrayLike<number> }, opts?: { pixelRatio?: number }) => void;
  hasImage: (id: string) => boolean;
  getSource: (id: string) => unknown;
  on: (ev: string, a?: unknown, b?: unknown) => void;
}

/** A map stub that records what was added. `failures` makes a given call throw. */
function makeMap(failures: {
  addImageThrows?: boolean;
  sourceId?: string;
} = {}): FakeMap {
  const m: FakeMap = {
    sources: [],
    layers: [],
    images: [],
    addSource(id) {
      if (failures.sourceId === id) throw new Error(`boom: addSource(${id})`);
      m.sources.push(id);
    },
    addLayer(spec) {
      m.layers.push(spec.id);
    },
    addImage(id, img) {
      // A sprite registration can fail for reasons other than the size check
      // (a future maplibre validation, a patched/instrumented map…). The message
      // mirrors the real RangeError so the test can't drift into fiction.
      if (failures.addImageThrows) throw new RangeError('mismatched image size');
      // MapLibre validates the declared size against the byte length and throws
      // on a mismatch — the exact check that a fractional devicePixelRatio broke.
      if (img.width * img.height * 4 !== img.data.length) {
        throw new Error(
          `RGBAImage size mismatch: declared ${img.width}x${img.height} ` +
          `(${img.width * img.height * 4} bytes) but got ${img.data.length}`,
        );
      }
      m.images.push(id);
      m.lastImage = img;
    },
    hasImage: (id) => m.images.includes(id),
    getSource: () => undefined,
    on: () => {},
  };
  return m;
}

/** Canvas 2d context stub; `throwOnGetImageData` simulates a blocked canvas read. */
function makeCanvas(throwOnGetImageData: boolean) {
  // A real canvas TRUNCATES fractional width/height to an integer bitmap, and
  // getImageData clamps to it — reproduce both, or the dpr bug can't be caught.
  const canvas = { width: 0, height: 0, getContext: () => ctx };
  const ctx = {
    scale: () => {},
    beginPath: () => {},
    moveTo: () => {},
    lineTo: () => {},
    stroke: () => {},
    lineCap: '',
    lineJoin: '',
    strokeStyle: '',
    lineWidth: 0,
    getImageData: () => {
      if (throwOnGetImageData) {
        const err = new Error('The operation is insecure.');
        err.name = 'SecurityError';
        throw err;
      }
      const w = Math.trunc(canvas.width);
      const h = Math.trunc(canvas.height);
      return { data: new Uint8ClampedArray(w * h * 4) };
    },
  };
  return canvas;
}

function installBrowserGlobals(canvasThrows: boolean, devicePixelRatio = 1) {
  const g = globalThis as Record<string, unknown>;
  g.document = { createElement: () => makeCanvas(canvasThrows) };
  g.window = {
    devicePixelRatio,
    location: { origin: 'http://localhost:3787' },
  };
  // The freshness-pointer upgrade is fire-and-forget; keep it off the network.
  g.fetch = () => Promise.reject(new Error('offline in tests'));
}

describe('addMapSourcesAndLayers — block isolation', () => {
  let errorSpy: ReturnType<typeof vi.spyOn>;
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    errorSpy.mockRestore();
    warnSpy.mockRestore();
    const g = globalThis as Record<string, unknown>;
    delete g.document;
    delete g.window;
    delete g.fetch;
  });

  it('adds the community heatmap on the happy path (guard against a vacuous test)', () => {
    installBrowserGlobals(false);
    const m = makeMap();

    addMapSourcesAndLayers(m);

    expect(m.images).toContain('direction-arrow');
    expect(m.sources).toContain('community-trails');
    for (const id of COMMUNITY_LAYERS) expect(m.layers).toContain(id);
    // …and the neighbouring blocks, whose z-order sandwiches the heatmap.
    expect(m.sources).toContain('terrain-dem');
    expect(m.sources).toContain('dfci-trails');
    expect(m.sources).toContain('user-activities');
  });

  it('still adds the community heatmap when the canvas read is blocked', () => {
    installBrowserGlobals(true); // getImageData throws → the prod symptom
    const m = makeMap();

    expect(() => addMapSourcesAndLayers(m)).not.toThrow();

    expect(m.images).not.toContain('direction-arrow'); // sprite lost…
    expect(m.sources).toContain('community-trails');   // …map intact
    for (const id of COMMUNITY_LAYERS) expect(m.layers).toContain(id);
    expect(m.sources).toContain('user-activities');
  });

  it('still adds the community heatmap when addImage itself throws', () => {
    installBrowserGlobals(false);
    const m = makeMap({ addImageThrows: true });

    expect(() => addMapSourcesAndLayers(m)).not.toThrow();

    expect(m.sources).toContain('community-trails');
    for (const id of COMMUNITY_LAYERS) expect(m.layers).toContain(id);
  });

  it('keeps the community heatmap when an UNRELATED block fails, and says so loudly', () => {
    installBrowserGlobals(false);
    const m = makeMap({ sourceId: 'terrain-dem' }); // hillshade block blows up

    expect(() => addMapSourcesAndLayers(m)).not.toThrow();

    expect(m.sources).not.toContain('terrain-dem');
    expect(m.sources).toContain('community-trails');
    for (const id of COMMUNITY_LAYERS) expect(m.layers).toContain(id);
    // A swallowed failure that says nothing is how this bug stayed invisible.
    expect(errorSpy).toHaveBeenCalled();
    // `.some`, not `calls[0]`: any other console.error (env-url's fail-loud path
    // under a different NODE_ENV, a log added to an earlier block…) would shift
    // the index and quietly make this assertion inspect the wrong call.
    expect(errorSpy.mock.calls.some((c: unknown[]) => String(c[0]).includes('terrain + hillshade'))).toBe(true);
  });

  it('a failing block does not take down the ones after it (DFCI → user layers)', () => {
    installBrowserGlobals(false);
    const m = makeMap({ sourceId: 'dfci-trails' });

    addMapSourcesAndLayers(m);

    expect(m.sources).not.toContain('dfci-trails');
    expect(m.sources).toContain('user-activities');
    expect(m.layers).toContain('user-activities-line');
  });
});

describe('registerDirectionArrow — fractional devicePixelRatio', () => {
  afterEach(() => {
    const g = globalThis as Record<string, unknown>;
    delete g.document;
    delete g.window;
    delete g.fetch;
  });

  // THE root cause of the blank /map (prod 2026-10-12, reproduced headless at
  // deviceScaleFactor 1.1): Firefox reports a fractional dpr at browser zoom, the
  // canvas truncated `16 * 1.1 = 17.6` to a 17 px bitmap, `addImage` was told
  // 17.6 px, MapLibre threw "RGBAImage size mismatch" — and the throw took the
  // whole layer setup with it.
  for (const dpr of [1, 1.1, 1.25, 1.5, 2, 2.2, 2.4, 3]) {
    it(`registers a consistently-sized sprite at dpr ${dpr}`, async () => {
      installBrowserGlobals(false, dpr);
      const { registerDirectionArrow } = await import('@/lib/map-arrows');
      const m = makeMap(); // its addImage enforces width*height*4 === data.length

      expect(registerDirectionArrow(m)).toBe(true);
      expect(m.images).toContain('direction-arrow');
      const img = m.lastImage!;
      expect(Number.isInteger(img.width)).toBe(true);
      expect(Number.isInteger(img.height)).toBe(true);
      expect(img.width).toBe(Math.max(1, Math.round(16 * dpr)));
    });
  }

  it('builds the whole /map at a fractional dpr (end-to-end of the prod bug)', () => {
    installBrowserGlobals(false, 1.1);
    const m = makeMap();

    addMapSourcesAndLayers(m);

    expect(m.images).toContain('direction-arrow'); // sprite works, not just survives
    expect(m.sources).toContain('community-trails');
    for (const id of COMMUNITY_LAYERS) expect(m.layers).toContain(id);
    expect(m.sources).toContain('user-activities');
  });
});

describe('registerDirectionArrow', () => {
  afterEach(() => {
    const g = globalThis as Record<string, unknown>;
    delete g.document;
    delete g.window;
  });

  it('returns false instead of throwing when the canvas is blocked', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    installBrowserGlobals(true);
    const { registerDirectionArrow } = await import('@/lib/map-arrows');

    expect(registerDirectionArrow(makeMap())).toBe(false);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('is idempotent: a second call does not re-add the image', async () => {
    installBrowserGlobals(false);
    const { registerDirectionArrow } = await import('@/lib/map-arrows');
    const m = makeMap();

    expect(registerDirectionArrow(m)).toBe(true);
    expect(registerDirectionArrow(m)).toBe(true);
    expect(m.images).toEqual(['direction-arrow']);
  });
});
