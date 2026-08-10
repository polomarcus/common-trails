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

describe('notificationDisplay — activity_synced', () => {
  it('renders the localised FR message with the activity name from meta', () => {
    const out = notificationDisplay(
      base({ meta: { name: 'Sortie matinale', sport: 'gravel', provider_activity_id: '42' } }),
      makeT(fr),
    );
    expect(out.title).toBe('Sortie synchronisée');
    expect(out.body).toBe('Ta sortie « Sortie matinale » est synchronisée dans ta vue perso ✓');
  });

  it('renders the localised EN message with the activity name from meta', () => {
    const out = notificationDisplay(
      base({ meta: { name: 'Morning ride' } }),
      makeT(en),
    );
    expect(out.title).toBe('Ride synced');
    expect(out.body).toBe('Your ride « Morning ride » is synced to your personal view ✓');
  });

  it('falls back to the no-name variant when meta.name is missing/blank', () => {
    for (const meta of [null, {}, { name: '' }, { name: '   ' }] as NotificationLike['meta'][]) {
      const out = notificationDisplay(base({ meta }), makeT(fr));
      expect(out.body).toBe('Ta sortie est synchronisée dans ta vue perso ✓');
    }
  });

  it('never leaks the raw i18n key (keys resolve in both dictionaries)', () => {
    for (const dict of [fr, en]) {
      const out = notificationDisplay(base({ meta: { name: 'X' } }), makeT(dict));
      expect(out.title).not.toContain('notif.activitySynced');
      expect(out.body).not.toContain('notif.activitySynced');
    }
  });
});

describe('notificationDisplay — other kinds', () => {
  it('renders the backend-provided title/body verbatim for non-activity_synced kinds', () => {
    const n = base({ kind: 'strava_resync_complete', title: 'Resync', body: '3 nouvelles activités' });
    const out = notificationDisplay(n, makeT(fr));
    expect(out.title).toBe('Resync');
    expect(out.body).toBe('3 nouvelles activités');
  });
});
