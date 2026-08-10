'use client';

import React from 'react';
import Link from 'next/link';
import { SPORT_COLORS, SPORT_ICONS, SPORT_LABELS } from '@/lib/constants';
import { useT } from '@/lib/i18n';
import { formatDist, fmtElev, fmtDate } from '@/lib/format';
import type { ActivityItem } from '@/lib/map-utils';
import ElevationProfile from '@/components/ElevationProfile';
import ActivityPhotoStrip from '@/components/map/ActivityPhotoStrip';

interface SelectedActivityCardProps {
  activity: ActivityItem;
  dateLocale: string;
  onClose: () => void;
}

function SelectedActivityCard({ activity, dateLocale, onClose }: SelectedActivityCardProps) {
  const t = useT();
  const sportColor = SPORT_COLORS[activity.sport] ?? '#2d6a4f';

  return (
    <div
      style={{
        position: 'absolute', bottom: 16, left: 16,
        background: '#fff', borderRadius: 12, padding: '14px 18px',
        boxShadow: '0 4px 24px rgba(0,0,0,0.15)', zIndex: 10,
        maxWidth: 360, width: 'calc(100vw - 32px)',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ fontSize: 24 }}>{SPORT_ICONS[activity.sport] ?? '🗺️'}</span>
            <h3 style={{ fontSize: 16, fontWeight: 800, color: '#1a1a1a', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {activity.name}
            </h3>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{
              padding: '2px 8px', borderRadius: 10, fontSize: 11, fontWeight: 700,
              background: sportColor, color: '#fff', textTransform: 'uppercase',
            }}>
              {SPORT_LABELS[activity.sport] ?? activity.sport}
            </span>
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
        {(activity.activity_date || activity.created_at) && (
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#333' }}>{fmtDate(activity.activity_date || activity.created_at || undefined, dateLocale)}</div>
            <div style={{ fontSize: 11, color: '#aaa' }}>{t('map.activity.date')}</div>
          </div>
        )}
      </div>

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
            const mid = activity.coords[Math.floor(activity.coords.length / 2)];
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
