/**
 * PMTiles protocol registration for MapLibre GL JS.
 *
 * Call registerPMTiles() once before creating the map.
 * Then use 'pmtiles://URL' as the source URL.
 *
 * Example:
 *   map.addSource('heatmap', {
 *     type: 'vector',
 *     url: 'pmtiles://https://cdn.example.com/heatmap.pmtiles',
 *   });
 */

let registered = false;

export async function registerPMTiles(): Promise<void> {
  if (registered) return;
  const [{ Protocol }, maplibregl] = await Promise.all([
    import('pmtiles'),
    import('maplibre-gl'),
  ]);
  const protocol = new Protocol();
  maplibregl.default.addProtocol('pmtiles', protocol.tile);
  registered = true;
}

export function isPMTilesAvailable(): boolean {
  return registered;
}
