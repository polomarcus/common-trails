'use client';

import React, { useEffect, useState } from 'react';
import { apiFetchSafe } from '@/lib/api-client';
import { useI18n } from '@/lib/i18n';

export interface ActivityPhoto {
  photo_id: string;
  strava_photo_id: string | null;
  thumbnail_url: string;
  full_url: string;
  lat: number | null;
  lon: number | null;
  caption: string | null;
}

interface ActivityPhotoStripProps {
  activityId: string;
  totalPhotoCount: number;
  /** Max thumbnails to render (Strava import phase 4 caps at 3 per activity). */
  maxThumbs?: number;
}

/**
 * Renders a small horizontal strip of photo thumbnails for an activity.
 *
 * Lazy-fetches `GET /me/activities/{activityId}/photos` on mount when
 * `totalPhotoCount > 0`. Clicking a thumbnail opens a simple lightbox.
 *
 * Photo URLs come straight from Strava CDN (signed, time-limited); if a
 * thumbnail fails to load we silently hide it.
 */
function ActivityPhotoStrip({ activityId, totalPhotoCount, maxThumbs = 3 }: ActivityPhotoStripProps) {
  const { t } = useI18n();
  const [photos, setPhotos] = useState<ActivityPhoto[] | null>(null);
  const [lightbox, setLightbox] = useState<ActivityPhoto | null>(null);
  const [errored, setErrored] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!activityId || totalPhotoCount <= 0) {
      setPhotos(null);
      return;
    }
    let cancelled = false;
    (async () => {
      const data = await apiFetchSafe<ActivityPhoto[]>(`/me/activities/${activityId}/photos`);
      if (!cancelled) setPhotos(data ?? []);
    })();
    return () => { cancelled = true; };
  }, [activityId, totalPhotoCount]);

  if (totalPhotoCount <= 0) return null;
  if (photos === null) {
    // loading skeleton — keep height stable to avoid layout jump
    return (
      <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
        {Array.from({ length: Math.min(maxThumbs, totalPhotoCount) }).map((_, i) => (
          <div
            key={i}
            style={{ width: 64, height: 64, borderRadius: 6, background: '#eee' }}
          />
        ))}
      </div>
    );
  }

  const visible = photos.filter((p) => !errored.has(p.photo_id)).slice(0, maxThumbs);
  if (visible.length === 0) return null;

  return (
    <>
      <div
        style={{ display: 'flex', gap: 6, marginBottom: 8, flexWrap: 'wrap' }}
        aria-label={t('map.photos.stripLabel', { count: visible.length })}
      >
        {visible.map((p) => (
          <button
            key={p.photo_id}
            type="button"
            onClick={() => setLightbox(p)}
            title={p.caption || t('map.photos.stravaPhoto')}
            style={{
              width: 64, height: 64, padding: 0, border: '1px solid #ddd',
              borderRadius: 6, overflow: 'hidden', cursor: 'pointer', background: '#f5f5f5',
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={p.thumbnail_url}
              alt={p.caption || ''}
              loading="lazy"
              referrerPolicy="no-referrer"
              onError={() => setErrored((prev) => {
                const next = new Set(prev);
                next.add(p.photo_id);
                return next;
              })}
              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
            />
          </button>
        ))}
      </div>
      {lightbox && (
        <div
          role="dialog"
          aria-modal="true"
          onClick={() => setLightbox(null)}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)',
            zIndex: 10000, display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: 16, cursor: 'zoom-out',
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={lightbox.full_url}
            alt={lightbox.caption || t('map.photos.altFallback')}
            referrerPolicy="no-referrer"
            style={{ maxWidth: '95%', maxHeight: '95%', borderRadius: 8, boxShadow: '0 8px 32px rgba(0,0,0,0.6)' }}
            onClick={(e) => e.stopPropagation()}
          />
          {lightbox.caption && (
            <div style={{
              position: 'absolute', bottom: 24, left: '50%', transform: 'translateX(-50%)',
              background: 'rgba(0,0,0,0.65)', color: '#fff', padding: '6px 14px',
              borderRadius: 6, fontSize: 13, maxWidth: '80%',
            }}>
              {lightbox.caption}
            </div>
          )}
          <button
            type="button"
            onClick={() => setLightbox(null)}
            aria-label={t('common.close')}
            style={{
              position: 'absolute', top: 16, right: 16, background: 'rgba(0,0,0,0.6)',
              color: '#fff', border: 'none', borderRadius: '50%', width: 36, height: 36,
              fontSize: 18, cursor: 'pointer',
            }}
          >✕</button>
        </div>
      )}
    </>
  );
}

export default React.memo(ActivityPhotoStrip);
