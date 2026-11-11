import { describe, expect, it } from 'vitest';
import { homeViewCenter, HOME_VIEW_ZOOM } from '../home-view';

/**
 * Non-régression (Paul, 2026-11-11) : /map s'ouvrait sur le départ de la
 * DERNIÈRE sortie — une sortie de vacances à Annecy et la carte s'ouvrait à
 * 300 km de Montpellier, où se trouvent presque toutes les sorties.
 */
describe('homeViewCenter', () => {
  const montpellier: [number, number][] = Array.from({ length: 40 }, (_, i) => [3.86 + (i % 5) * 0.004, 43.61 + (i % 4) * 0.004]);
  const annecy: [number, number] = [6.1286, 45.8992];

  it('opens where most rides start, even when the latest ride is elsewhere', () => {
    const c = homeViewCenter([...montpellier, annecy])!; // Annecy = most recent (last)
    expect(c[0]).toBeGreaterThan(3.8);
    expect(c[0]).toBeLessThan(3.95);
    expect(c[1]).toBeGreaterThan(43.55);
    expect(c[1]).toBeLessThan(43.7);
  });

  it('a single ride → that ride', () => {
    expect(homeViewCenter([annecy])).toEqual(annecy);
  });

  it('nothing usable → null (the map keeps its default view)', () => {
    expect(homeViewCenter([])).toBeNull();
    expect(homeViewCenter([null, undefined, [NaN, 1]])).toBeNull();
  });

  it('opens at an area-level zoom, not street level', () => {
    expect(HOME_VIEW_ZOOM).toBeLessThanOrEqual(12);
  });
});
