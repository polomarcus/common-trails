'use client';

import Link from 'next/link';
import { useI18n } from '@/lib/i18n';

// ── Import Modal (thin launcher) ─────────────────────────────────────────────
// De-clutter (2026-07): there is ONE canonical import place — /strava — with a
// single smart dropzone (GPX / FIT / Strava-Garmin-Komoot `.zip`) and one ODbL
// consent. The map no longer carries a second, competing upload form (file
// picker + sport select + consent + per-platform export links). This modal is a
// thin signpost: a sentence + a link that sends the user to /strava.

interface ImportModalProps {
  onClose: () => void;
}

export function ImportModal({ onClose }: ImportModalProps) {
  const { t } = useI18n();
  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
      }}
      onClick={onClose}
    >
      <div
        data-testid="import-launcher"
        style={{ background: '#fff', borderRadius: 12, padding: 24, width: 360, maxWidth: '90%', boxShadow: '0 8px 24px rgba(0,0,0,0.15)', textAlign: 'center' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ fontSize: 30, marginBottom: 8 }} aria-hidden="true">📥</div>
        <h3 style={{ margin: '0 0 8px', color: '#1a4731' }}>{t('map.importTitle')}</h3>
        <p style={{ fontSize: 13, color: '#555', lineHeight: 1.55, margin: '0 0 18px' }}>
          {t('map.import.launcherBody')}
        </p>

        <Link
          href="/strava"
          data-testid="import-goto-strava"
          onClick={onClose}
          style={{
            display: 'inline-block', width: '100%', boxSizing: 'border-box',
            padding: '11px 16px', background: '#2d6a4f', color: '#fff',
            borderRadius: 8, fontWeight: 700, fontSize: 14, textDecoration: 'none',
          }}
        >
          {t('map.import.launcherCta')} →
        </Link>

        <button
          type="button"
          onClick={onClose}
          style={{ display: 'block', width: '100%', marginTop: 10, padding: '9px', background: 'none', border: 'none', color: '#888', cursor: 'pointer', fontSize: 13 }}
        >
          {t('map.close')}
        </button>
      </div>
    </div>
  );
}

// ── Routing Methodology Modal ───────────────────────────────────────────────

interface RoutingInfoModalProps {
  onClose: () => void;
}

export function RoutingInfoModal({ onClose }: RoutingInfoModalProps) {
  const { t } = useI18n();
  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100 }}
      onClick={onClose}
    >
      <div
        style={{ background: '#fff', borderRadius: 16, padding: 28, maxWidth: 460, width: '90%', maxHeight: '90vh', overflowY: 'auto', position: 'relative' }}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          style={{ position: 'absolute', top: 12, right: 16, background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: '#999', lineHeight: 1 }}
        >&times;</button>

        <div style={{ fontWeight: 800, fontSize: 17, color: '#1a4731', marginBottom: 14 }}>
          {t('map.howRouting.title')}
        </div>

        <p style={{ fontSize: 13, color: '#555', lineHeight: 1.6, margin: '0 0 14px' }}>
          {t('map.howRouting.intro')}
        </p>

        <p style={{ fontSize: 12, color: '#777', margin: '0 0 10px', fontWeight: 600 }}>
          {t('map.howRouting.signalsIntro')}
        </p>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 14 }}>
          {[
            { icon: '📊', title: t('map.howRouting.frequency'), desc: t('map.howRouting.frequencyDesc') },
            { icon: '🪨', title: t('map.howRouting.surface'), desc: t('map.howRouting.surfaceDesc') },
            { icon: '⛰️', title: t('map.howRouting.elevation'), desc: t('map.howRouting.elevationDesc') },
            { icon: '🥾', title: t('map.howRouting.trails'), desc: t('map.howRouting.trailsDesc') },
            { icon: '🚫', title: t('map.howRouting.detour'), desc: t('map.howRouting.detourDesc') },
          ].map(({ icon, title, desc }) => (
            <div key={title} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <span style={{ fontSize: 15, flexShrink: 0, marginTop: 1 }}>{icon}</span>
              <span style={{ fontSize: 12, color: '#444', lineHeight: 1.5 }}>
                <strong>{title}</strong> — {desc}
              </span>
            </div>
          ))}
        </div>

        <p style={{ fontSize: 12, color: '#555', lineHeight: 1.6, margin: '0 0 16px', fontStyle: 'italic' }}>
          {t('map.howRouting.profileNote')}
        </p>

        <a
          href="/methode"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 13, fontWeight: 600, color: '#2d6a4f', textDecoration: 'none' }}
        >
          {t('map.howRouting.learnMore')}
        </a>
      </div>
    </div>
  );
}

// ── Photo Lightbox ──────────────────────────────────────────────────────────

interface LightboxPhoto {
  url_medium: string;
  caption?: string;
  activity_name?: string;
  index: number;
}

interface PhotoLightboxProps {
  photo: LightboxPhoto;
  photos: any[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  onClose: () => void;
  onNavigate: (photo: LightboxPhoto) => void;
}

export function PhotoLightbox({ photo, photos, onClose, onNavigate }: PhotoLightboxProps) {
  const { t } = useI18n();
  const goPrev = () => {
    if (photo.index > 0) {
      const prev = photos[photo.index - 1];
      if (prev) onNavigate({ url_medium: prev.properties.url_medium, caption: prev.properties.caption, activity_name: prev.properties.activity_name, index: photo.index - 1 });
    }
  };
  const goNext = () => {
    if (photo.index < photos.length - 1) {
      const next = photos[photo.index + 1];
      if (next) onNavigate({ url_medium: next.properties.url_medium, caption: next.properties.caption, activity_name: next.properties.activity_name, index: photo.index + 1 });
    }
  };

  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)',
        zIndex: 9999, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      }}
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose();
        if (e.key === 'ArrowLeft') goPrev();
        if (e.key === 'ArrowRight') goNext();
      }}
      tabIndex={0}
      ref={(el) => el?.focus()}
    >
      <div style={{ position: 'absolute', top: 16, left: 0, right: 0, display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 24px' }}>
        <span style={{ color: '#fff', fontSize: 15, fontWeight: 600 }}>
          {photo.activity_name || t('map.lightbox.photo')}
          <span style={{ marginLeft: 12, fontSize: 12, color: '#aaa' }}>
            {photo.index + 1} / {photos.length}
          </span>
        </span>
        <button
          onClick={(e) => { e.stopPropagation(); onClose(); }}
          style={{ background: 'none', border: 'none', color: '#fff', fontSize: 28, cursor: 'pointer', padding: '4px 12px' }}
        >&times;</button>
      </div>
      {photo.index > 0 && (
        <button
          onClick={(e) => { e.stopPropagation(); goPrev(); }}
          style={{ position: 'absolute', left: 16, top: '50%', transform: 'translateY(-50%)', background: 'rgba(0,0,0,0.5)', border: 'none', color: '#fff', fontSize: 32, cursor: 'pointer', padding: '8px 14px', borderRadius: 8 }}
        >&#8249;</button>
      )}
      {photo.index < photos.length - 1 && (
        <button
          onClick={(e) => { e.stopPropagation(); goNext(); }}
          style={{ position: 'absolute', right: 16, top: '50%', transform: 'translateY(-50%)', background: 'rgba(0,0,0,0.5)', border: 'none', color: '#fff', fontSize: 32, cursor: 'pointer', padding: '8px 14px', borderRadius: 8 }}
        >&#8250;</button>
      )}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={photo.url_medium}
        alt={photo.caption || t('map.lightbox.photo')}
        style={{ maxWidth: '90vw', maxHeight: '80vh', objectFit: 'contain', borderRadius: 8 }}
        onClick={(e) => e.stopPropagation()}
      />
      {photo.caption && (
        <div style={{ color: '#ddd', marginTop: 12, fontSize: 14, textAlign: 'center', maxWidth: '80vw' }}>
          {photo.caption}
        </div>
      )}
    </div>
  );
}
