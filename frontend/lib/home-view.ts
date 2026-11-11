/**
 * Where /map opens for a signed-in rider with no explicit position in the URL.
 *
 * It used to jump to the START of the most recent activity — one holiday ride
 * and the map opened hundreds of km from where the rider actually rides (Paul,
 * 2026-11-11: sent to Annecy, rides around Montpellier). It now opens on the
 * rider's HOME AREA: the ~28 km grid cell holding the most activity starts,
 * centred on the mean of those starts.
 */
export const HOME_VIEW_ZOOM = 11;
const CELL_DEG = 0.25;

export function homeViewCenter(
  starts: ReadonlyArray<readonly [number, number] | null | undefined>,
): [number, number] | null {
  const cells = new Map<string, { n: number; lon: number; lat: number }>();
  let best: { n: number; lon: number; lat: number } | null = null;
  for (const s of starts) {
    if (!s || !Number.isFinite(s[0]) || !Number.isFinite(s[1])) continue;
    const key = `${Math.floor(s[0] / CELL_DEG)}:${Math.floor(s[1] / CELL_DEG)}`;
    let c = cells.get(key);
    if (!c) { c = { n: 0, lon: 0, lat: 0 }; cells.set(key, c); }
    c.n += 1; c.lon += s[0]; c.lat += s[1];
    // `>` keeps the FIRST cell on a tie → deterministic.
    if (!best || c.n > best.n) best = c;
  }
  return best ? [best.lon / best.n, best.lat / best.n] : null;
}
