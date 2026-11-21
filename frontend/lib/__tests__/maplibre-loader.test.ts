/**
 * lib/maplibre.ts pins the two maplibre-gl 6 traps that blanked every map on
 * the Dependabot bump (PR #105):
 *   - the package is ESM-only with NAMED exports: the loader hands back the
 *     namespace (`ml.Map`), there is no `.default` any more;
 *   - the worker URL must be set before the first map, and it must be a blob:
 *     module (page CSP) that imports the emitted asset — an http(s) worker URL
 *     would run under the backend's `default-src 'none'` and fetch nothing.
 */
import { resolveObjectURL } from 'node:buffer';
import { describe, it, expect, vi } from 'vitest';

const { setWorkerUrl, FakeMap } = vi.hoisted(() => ({ setWorkerUrl: vi.fn(), FakeMap: class FakeMap {} }));
vi.mock('maplibre-gl', () => ({ Map: FakeMap, setWorkerUrl }));

import { loadMaplibre } from '@/lib/maplibre';

describe('loadMaplibre', () => {
  it('returns the module namespace (named exports, no default)', async () => {
    const ml = await loadMaplibre();
    expect(ml.Map).toBe(FakeMap);
  });

  it('sets the worker URL once, to a blob: module importing the emitted worker asset', async () => {
    await loadMaplibre();
    await loadMaplibre();
    expect(setWorkerUrl).toHaveBeenCalledTimes(1);
    const url = setWorkerUrl.mock.calls[0][0] as string;
    expect(url.startsWith('blob:')).toBe(true);
    const src = await resolveObjectURL(url)!.text();
    expect(src).toMatch(/^import ".*maplibre-gl\/dist\/maplibre-gl-worker\.mjs";$/);
  });
});
