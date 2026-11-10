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
 *
 * A source that needs custom fetching (credentials, a private proxy) is added
 * with addPMTilesSource(): the protocol then serves `pmtiles://<source key>`
 * from that Source instead of a plain public fetch.
 */
import type { Source } from 'pmtiles';

let registered = false;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let protocolInstance: any = null;

export async function registerPMTiles(): Promise<void> {
  if (registered) return;
  const [{ Protocol }, maplibregl] = await Promise.all([
    import('pmtiles'),
    import('maplibre-gl'),
  ]);
  const protocol = new Protocol();
  maplibregl.default.addProtocol('pmtiles', protocol.tile);
  protocolInstance = protocol;
  registered = true;
}

export function isPMTilesAvailable(): boolean {
  return registered;
}

/**
 * Register a custom-fetching PMTiles archive with the protocol. Returns the
 * MapLibre source URL to use (`pmtiles://<source.getKey()>`).
 */
export async function addPMTilesSource(source: Source): Promise<string> {
  await registerPMTiles();
  const { PMTiles } = await import('pmtiles');
  protocolInstance.add(new PMTiles(source));
  return `pmtiles://${source.getKey()}`;
}
