/**
 * "Default sport" activities: bike rides a Strava export only calls "Ride".
 * The importer files them under Route for lack of a better signal and flags
 * them (`sport_is_default`) so the rider can classify them — many are gravel
 * or MTB rides that would otherwise pollute the road layer.
 *
 *   GET   /me/activities/default-sport        → { count, activities (≤ limit) }
 *   PATCH /me/activities/sport                → re-classify ids, or every
 *         default-sport activity at once ({ from_default: true }); the same
 *         sport as the current one just CONFIRMS it (clears the flag).
 */
import { apiFetch } from '@/lib/api-client';

/** What a generic bike ride can be re-classified into. */
export const DEFAULT_SPORT_CHOICES = ['road', 'gravel', 'mtb'] as const;
export type DefaultSportChoice = (typeof DEFAULT_SPORT_CHOICES)[number];

/** Activities listed in the panel; the rest is "et N autres". */
export const DEFAULT_SPORT_LIST_LIMIT = 50;
/** Backend cap of an explicit id list (BULK_SPORT_MAX_IDS). */
export const BULK_SPORT_MAX_IDS = 500;

export interface DefaultSportActivity {
  id: string;
  name: string;
  sport: string;
  distance_m: number | null;
  activity_date: string | null;
}

export interface DefaultSportStatus {
  count: number;
  activities: DefaultSportActivity[];
}

export interface BulkSportResult {
  sport: string;
  matched: number;
  updated: number;
  updated_ids: string[];
  default_sport_count: number;
}

/** `null` when the status could not be read (network error, older backend,
 *  malformed body) — distinct from "zero left to classify". */
export async function tryFetchDefaultSport(limit = DEFAULT_SPORT_LIST_LIMIT): Promise<DefaultSportStatus | null> {
  try {
    const data = await apiFetch<DefaultSportStatus>(`/me/activities/default-sport?limit=${limit}`);
    if (typeof data?.count !== 'number' || !Array.isArray(data.activities)) return null;
    return data;
  } catch {
    return null;
  }
}

/** Never throws: an older backend / a network error reads as "nothing to classify". */
export async function fetchDefaultSport(limit = DEFAULT_SPORT_LIST_LIMIT): Promise<DefaultSportStatus> {
  return (await tryFetchDefaultSport(limit)) ?? { count: 0, activities: [] };
}

/** `'all'` = every default-sport activity of the rider (server-side set). */
export function reclassifyDefaultSport(target: string[] | 'all', sport: DefaultSportChoice): Promise<BulkSportResult> {
  const body = target === 'all' ? { from_default: true, sport } : { activity_ids: target, sport };
  return apiFetch<BulkSportResult>('/me/activities/sport', { method: 'PATCH', body: JSON.stringify(body) });
}

/** What the page needs to keep its list / map source in sync after a bulk change. */
export interface DefaultSportChange {
  sport: string;
  /** Activities whose sport actually changed. */
  updatedIds: string[];
  /** Activities whose "default" flag was cleared (`'all'` = every flagged one). */
  cleared: string[] | 'all';
}

/**
 * Apply a bulk change to a list of items carrying `id` / `sport` /
 * `sport_is_default`. Pure; returns the input when nothing changes.
 */
export function applyDefaultSportChange<T extends { id: string; sport: string; sport_is_default?: boolean }>(
  items: T[], change: DefaultSportChange,
): T[] {
  const updated = new Set(change.updatedIds);
  const cleared = change.cleared === 'all' ? null : new Set(change.cleared);
  let hit = false;
  const next = items.map((a) => {
    const newSport = updated.has(a.id) ? change.sport : a.sport;
    const clear = a.sport_is_default === true && (cleared === null || cleared.has(a.id) || updated.has(a.id));
    if (newSport === a.sport && !clear) return a;
    hit = true;
    return { ...a, sport: newSport, sport_is_default: clear ? false : a.sport_is_default };
  });
  return hit ? next : items;
}
