/**
 * Correcting an activity's sport after import (PATCH /me/activities/{id}).
 *
 * A wrong sport at import (MTB rides classified as road…) pollutes the wrong
 * community layer; the owner fixes it from the selected-activity card. The
 * backend validates against `config.EDITABLE_SPORTS` — mirror it here.
 * `offroad` is NOT editable: it is a filter alias, never a concrete practice.
 */
import { apiFetch } from '@/lib/api-client';

export const EDITABLE_SPORTS = ['road', 'gravel', 'mtb', 'running'] as const;
export type EditableSport = (typeof EDITABLE_SPORTS)[number];

export interface SportUpdateResult {
  id: string;
  sport: string;
  changed: boolean;
}

export function updateActivitySport(id: string, sport: EditableSport): Promise<SportUpdateResult> {
  return apiFetch<SportUpdateResult>(`/me/activities/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ sport }),
  });
}

/**
 * Return a copy of the "Ma heatmap" FeatureCollection with `sport` set on the
 * feature `id` (the map source must see the new sport for its hover popup).
 * Pure — unknown id / empty input returns the input unchanged.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function withActivitySport<T extends { features?: any[] } | null>(fc: T, id: string, sport: string): T {
  if (!fc || !Array.isArray(fc.features)) return fc;
  let hit = false;
  const features = fc.features.map((f) => {
    if (f?.properties?.id !== id) return f;
    hit = true;
    return { ...f, properties: { ...f.properties, sport } };
  });
  return hit ? { ...fc, features } : fc;
}
