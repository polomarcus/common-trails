/**
 * Unit tests for the toast display-text resolver.
 *
 * Drives the REAL `notificationDisplay` + the REAL fr/en dictionaries via a
 * `t` shim that mirrors i18n.tsx's `{var}` interpolation + fr-fallback, so a
 * missing/renamed key or a broken interpolation fails here (not in prod).
 */
import { describe, it, expect } from 'vitest';

import { notificationDisplay, type NotificationLike } from '../notification-display';
import fr from '../translations/fr';
import en from '../translations/en';

type Dict = Record<string, string>;

function makeT(dict: Dict) {
  return (key: string, vars?: Record<string, string | number>): string => {
    let str = dict[key] ?? fr[key] ?? key;
    if (vars) for (const [k, v] of Object.entries(vars)) str = str.replaceAll(`{${k}}`, String(v));
    return str;
  };
}

const base = (over: Partial<NotificationLike>): NotificationLike => ({
  kind: 'activity_synced',
  title: 'DB title',
  body: 'DB body',
  meta: null,
  ...over,
});

describe('notificationDisplay', () => {
  it('renders the backend-provided title/body verbatim', () => {
    const n = base({ kind: 'strava_resync_complete', title: 'Resync', body: '3 nouvelles activités' });
    const out = notificationDisplay(n, makeT(fr));
    expect(out.title).toBe('Resync');
    expect(out.body).toBe('3 nouvelles activités');
  });

  it('renders a legacy activity_synced row (retired Strava live sync) from its stored text, in both locales', () => {
    const n = base({ meta: { name: 'Sortie matinale', sport: 'gravel', provider_activity_id: '42' } });
    for (const dict of [fr, en]) {
      const out = notificationDisplay(n, makeT(dict));
      expect(out).toEqual({ title: 'DB title', body: 'DB body' });
    }
  });

  it('has no leftover activity_synced translation keys', () => {
    for (const dict of [fr, en] as Dict[]) {
      expect(Object.keys(dict).filter((k) => k.startsWith('notif.activitySynced'))).toEqual([]);
    }
  });
});
