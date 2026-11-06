'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { getToken } from '@/lib/auth';
import { isArchivePending } from '@/lib/archive-pending';
import { useT } from '@/lib/i18n';

/** `/me/activities?limit=` used by /map — the API returns at most this many. */
export const MAP_ACTIVITIES_LIMIT = 5000;

interface MapActivitiesNoticeProps {
  /** Activities returned by /me/activities; null while not loaded yet. */
  count: number | null;
}

/**
 * Two small, discreet notices about the signed-in rider's own traces on /map:
 *   - EMPTY STATE: 0 activities → a dismissible card pointing to the import
 *     page (/strava hosts the dropzone), instead of a blank map with no clue;
 *   - CAP: exactly MAP_ACTIVITIES_LIMIT came back → "5000 most recent shown".
 *
 * Auth is read AFTER mount, never during render: /map is a static export built
 * logged out, so reading getToken() here would make a signed-in client's first
 * render differ from the HTML → hydration mismatch (React #418).
 */
export default function MapActivitiesNotice({ count }: MapActivitiesNoticeProps) {
  const t = useT();
  const [loggedIn, setLoggedIn] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  // An archive was accepted < 2 h ago and is still draining → "processing"
  // variant instead of the import CTA (read post-mount, like auth).
  const [processing, setProcessing] = useState(false);
  useEffect(() => {
    setLoggedIn(getToken() !== null);
    setProcessing(isArchivePending());
  }, []);

  if (!loggedIn || count === null) return null;

  if (count === 0 && !dismissed) {
    return (
      <div
        data-testid="map-empty-state"
        role="status"
        style={{
          // Top-left, clear of MapLibre's top-right controls.
          position: 'absolute', top: 12, left: 12,
          zIndex: 25, background: '#fff', borderRadius: 12,
          boxShadow: '0 4px 16px rgba(0,0,0,0.15)', border: '1px solid #e5e7eb',
          padding: '10px 12px 10px 14px', width: 'max-content', maxWidth: 'calc(100% - 68px)',
          display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
          fontSize: 13, color: '#1f2937',
        }}
      >
        {processing ? (
          <span data-testid="map-empty-state-processing" style={{ fontWeight: 600, flex: '1 1 180px' }}>
            ⏳ {t('map.empty.processing')}
          </span>
        ) : (
          <span style={{ fontWeight: 600, flex: '1 1 180px' }}>{t('map.empty.title')}</span>
        )}
        {!processing && <Link
          href="/strava"
          data-testid="map-empty-state-cta"
          style={{
            padding: '6px 12px', borderRadius: 8, background: '#2d6a4f', color: '#fff',
            fontWeight: 700, fontSize: 12, textDecoration: 'none', whiteSpace: 'nowrap',
          }}
        >
          {t('map.empty.cta')}
        </Link>}
        <button
          type="button"
          data-testid="map-empty-state-dismiss"
          onClick={() => setDismissed(true)}
          aria-label={t('map.empty.dismiss')}
          title={t('map.empty.dismiss')}
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            fontSize: 16, color: '#9ca3af', padding: '0 2px', lineHeight: 1,
          }}
        >
          ×
        </button>
      </div>
    );
  }

  if (count >= MAP_ACTIVITIES_LIMIT) {
    return (
      <div
        data-testid="map-activities-capped"
        style={{
          position: 'absolute', top: 10, right: 52, zIndex: 25,
          background: 'rgba(255,255,255,0.9)', borderRadius: 6,
          padding: '3px 8px', fontSize: 11, color: '#555',
          boxShadow: '0 1px 4px rgba(0,0,0,0.12)', pointerEvents: 'none',
        }}
      >
        {t('map.activitiesCapped', { count: MAP_ACTIVITIES_LIMIT })}
      </div>
    );
  }

  return null;
}
