'use client';

import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { SPORT_COLORS, SPORT_ICONS } from '@/lib/constants';
import { EDITABLE_SPORTS, updateActivitySport, type EditableSport } from '@/lib/activity-sport';
import { useT } from '@/lib/i18n';
import { formatDist, fmtElev, fmtDate } from '@/lib/format';
import type { ActivityItem } from '@/lib/map-utils';
import ElevationProfile from '@/components/ElevationProfile';
import ActivityPhotoStrip from '@/components/map/ActivityPhotoStrip';

interface SelectedActivityCardProps {
  activity: ActivityItem;
  dateLocale: string;
  onClose: () => void;
  /**
   * Called with the NEW sport optimistically (before the PATCH resolves) and
   * again with the OLD sport if it fails (rollback), so the page can keep its
   * activity list + the "Ma heatmap" source in sync.
   */
  onSportChange?: (activityId: string, sport: string) => void;
}

function SelectedActivityCard({ activity, dateLocale, onClose, onSportChange }: SelectedActivityCardProps) {
  const t = useT();
  // Local copy for the optimistic update; re-synced when another activity is
  // selected or the parent pushes a new sport.
  const [sport, setSport] = useState(activity.sport);
  const [saving, setSaving] = useState(false);
  const [sportError, setSportError] = useState(false);
  // Guards against a slow PATCH resolving after the user selected another trace.
  const currentIdRef = useRef(activity.id);
  useEffect(() => {
    currentIdRef.current = activity.id;
    setSport(activity.sport);
    setSportError(false);
  }, [activity.id, activity.sport]);

  const sportColor = SPORT_COLORS[sport] ?? '#2d6a4f';
  // A legacy/aliased stored sport (e.g. "offroad") stays selectable as the
  // current value, but the user can only pick a concrete sport.
  const sportOptions: string[] = (EDITABLE_SPORTS as readonly string[]).includes(sport)
    ? [...EDITABLE_SPORTS]
    : [sport, ...EDITABLE_SPORTS];

  const handleSportChange = async (next: EditableSport) => {
    const id = activity.id;
    const prev = sport;
    if (next === prev || saving) return;
    setSport(next);
    setSportError(false);
    setSaving(true);
    onSportChange?.(id, next);
    try {
      await updateActivitySport(id, next);
    } catch {
      onSportChange?.(id, prev);
      if (currentIdRef.current === id) {
        setSport(prev);
        setSportError(true);
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      style={{
        position: 'absolute', bottom: 'calc(var(--beta-banner-h, 0px) + 16px)', left: 16,
        background: '#fff', borderRadius: 12, padding: '14px 18px',
        boxShadow: '0 4px 24px rgba(0,0,0,0.15)', zIndex: 10,
        maxWidth: 360, width: 'calc(100vw - 32px)',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ fontSize: 24 }}>{SPORT_ICONS[sport] ?? '🗺️'}</span>
            <h3 style={{ fontSize: 16, fontWeight: 800, color: '#1a1a1a', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {activity.name}
            </h3>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <label
              title={t('map.activity.sportHint')}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 600, color: '#666' }}
            >
              {t('map.activity.sport')}&nbsp;:
              <select
                data-testid="activity-sport-select"
                aria-label={t('map.activity.sportChange')}
                value={sport}
                disabled={saving}
                onChange={(e) => { void handleSportChange(e.target.value as EditableSport); }}
                style={{
                  padding: '2px 6px', borderRadius: 10, fontSize: 11, fontWeight: 700,
                  background: sportColor, color: '#fff', border: 'none', cursor: saving ? 'wait' : 'pointer',
                  opacity: saving ? 0.7 : 1,
                }}
              >
                {sportOptions.map((s) => (
                  <option key={s} value={s} disabled={!(EDITABLE_SPORTS as readonly string[]).includes(s)}>
                    {t(`sport.${s}`)}
                  </option>
                ))}
              </select>
            </label>
            {activity.provider === 'strava' && activity.provider_activity_id ? (
              <a
                href={`https://www.strava.com/activities/${activity.provider_activity_id}`}
                target="_blank" rel="noreferrer"
                style={{ padding: '2px 8px', borderRadius: 10, fontSize: 11, fontWeight: 700, background: '#fc4c02', color: '#fff', textDecoration: 'none' }}
              >
                {t('strava.viewOn')}
              </a>
            ) : (
              <span style={{ padding: '2px 8px', borderRadius: 10, fontSize: 11, fontWeight: 600, background: '#f0f0f0', color: '#888' }}>GPX</span>
            )}
          </div>
        </div>
        <button
          onClick={onClose}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#aaa', fontSize: 18, padding: '0 0 0 8px', lineHeight: 1 }}
        >✕</button>
      </div>

      {sportError && (
        <div role="alert" data-testid="activity-sport-error" style={{ fontSize: 12, color: '#c0392b', marginBottom: 8 }}>
          {t('map.activity.sportError')}
        </div>
      )}

      {/* Stats */}
      <div style={{ display: 'flex', gap: 20, fontSize: 13, color: '#333', marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 800, color: sportColor }}>{formatDist(activity.distance_m)}</div>
          <div style={{ fontSize: 11, color: '#aaa' }}>{t('map.activity.distance')}</div>
        </div>
        {activity.elevation_gain_m ? (
          <div>
            <div style={{ fontSize: 18, fontWeight: 800, color: '#c0392b' }}>{fmtElev(activity.elevation_gain_m)}</div>
            <div style={{ fontSize: 11, color: '#aaa' }}>{t('map.activity.elevGain')}</div>
          </div>
        ) : null}
        {!activity.undated && (activity.activity_date || activity.created_at) && (
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#333' }}>{fmtDate(activity.activity_date || activity.created_at || undefined, dateLocale)}</div>
            <div style={{ fontSize: 11, color: '#aaa' }}>{t('map.activity.date')}</div>
          </div>
        )}
      </div>

      {/* Undated trace (in practice a planned itinerary): kept in "Ma heatmap"
          but never public (backend provenance.community_eligible_conditions).
          The API's activity_date then falls back to the import date, which is
          NOT shown as if it were the ride date. */}
      {activity.undated && (
        <div data-testid="activity-undated-note" style={{ fontSize: 12, color: '#8a6d3b', background: '#fcf8e3', borderRadius: 6, padding: '4px 8px', marginBottom: 8 }}>
          {t('map.activity.undated')}
        </div>
      )}

      {/* Photo thumbnails (Strava import phase 4) */}
      {activity.total_photo_count && activity.total_photo_count > 0 ? (
        <ActivityPhotoStrip
          activityId={activity.id}
          totalPhotoCount={activity.total_photo_count}
        />
      ) : null}

      {/* Elevation profile */}
      {activity.coords.some((c) => c.length > 2 && c[2] !== 0) && (
        <div style={{ marginBottom: 8 }}>
          <ElevationProfile coords={activity.coords} width={320} height={72} />
        </div>
      )}

      {/* Actions — the standalone /activities detail page was retired post-pivot
          (frozen social + a dead "create route → editor" path; the editor was
          decommissioned in #518). It now redirects to /map, so the old "View
          details" button would just bounce back here. The selected trace is
          already shown full-size on the map with its elevation profile above,
          so we keep only the "explore this area" action. */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Link
          href={(() => {
            // `coords` is empty until fetched on the per-user PMTiles path
            // (the list ships no geometry) → the list's precomputed centre.
            const mid = activity.coords[Math.floor(activity.coords.length / 2)] ?? activity.center;
            // /discover is disabled — land on the map centred on the activity area.
            return `/map?lat=${mid[1].toFixed(5)}&lon=${mid[0].toFixed(5)}&zoom=12`;
          })()}
          style={{ padding: '7px 14px', background: '#f0f0f0', color: '#555', borderRadius: 8, fontSize: 12, fontWeight: 600, textDecoration: 'none' }}
        >
          {t('map.activity.similar')}
        </Link>
      </div>
    </div>
  );
}

export default React.memo(SelectedActivityCard);
