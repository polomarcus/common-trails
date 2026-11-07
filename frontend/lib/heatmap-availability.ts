/**
 * Detect that the community heatmap source cannot load (tiles host down,
 * PMTiles header unreachable…) so /map can say so instead of silently showing
 * the basemap alone (frontend audit 2026-11-07, P0 #4).
 *
 * MapLibre tags every source-related `error` event with its `sourceId`, both
 * the PMTiles header/TileJSON load and each tile fetch, and fires `sourcedata`
 * with a `tile` when a tile actually loaded. Rule: an error on the source
 * BEFORE any tile loaded ⇒ unavailable; a tile that loads afterwards ⇒
 * available again (transient outage, the notice goes away by itself).
 */

export const COMMUNITY_HEATMAP_SOURCE_ID = 'community-trails';

interface SourceEvent {
  sourceId?: string;
  tile?: unknown;
}

interface EventedMap {
  on: (type: string, listener: (e: SourceEvent) => void) => unknown;
  off: (type: string, listener: (e: SourceEvent) => void) => unknown;
}

/**
 * Calls `onChange(true)` when the heatmap source fails before any tile loaded,
 * `onChange(false)` when a tile loads after such a failure. Returns an
 * unsubscribe function.
 */
export function watchHeatmapAvailability(
  map: EventedMap,
  onChange: (unavailable: boolean) => void,
  sourceId: string = COMMUNITY_HEATMAP_SOURCE_ID,
): () => void {
  let tileLoaded = false;
  let unavailable = false;

  const onError = (e: SourceEvent) => {
    if (e?.sourceId !== sourceId || tileLoaded || unavailable) return;
    unavailable = true;
    onChange(true);
  };
  const onSourceData = (e: SourceEvent) => {
    if (e?.sourceId !== sourceId || !e.tile) return;
    tileLoaded = true;
    if (unavailable) {
      unavailable = false;
      onChange(false);
    }
  };

  map.on('error', onError);
  map.on('sourcedata', onSourceData);
  return () => {
    map.off('error', onError);
    map.off('sourcedata', onSourceData);
  };
}
