'use client';

import { useState } from 'react';
import Link from 'next/link';
import { API_URL } from '@/lib/api-client';
import { useI18n } from '@/lib/i18n';
import { getCurrentUserId } from '@/lib/auth';
import EmailLoginForm from '@/components/EmailLoginForm';

export type Bbox = {
  minLon: number;
  minLat: number;
  maxLon: number;
  maxLat: number;
};

interface ExportHeatmapModalProps {
  onClose: () => void;
  /** Retained for call-site compatibility — the pre-computed export has no
   *  bbox editor, so this is ignored. */
  initialBbox?: Bbox | null;
}

/**
 * Heatmap export modal — PRE-COMPUTED artifacts ONLY.
 *
 * The community heatmap export is served entirely from static artifacts the
 * `build_pmtiles` job publishes to the public (ODbL) GCS bucket. There is NO
 * on-demand tile computation, NO async build, NO mbtiles/geojson/gpx/kml
 * per-bbox builds anymore. The modal offers exactly three things:
 *
 * - **Calque** — the raster XYZ overlay (`raster/tiles.json`) to drop into
 *   gpx.studio / VisuGPX as a custom map layer (plan a route ON TOP of the
 *   heatmap). One public URL, no download.
 * - **PMTiles** — the single full-region vector-tile archive
 *   (`/export/heatmap.pmtiles` → 302 to the canonical GCS URL).
 * - **GeoJSONL** — the full-region newline-delimited GeoJSON (gzipped)
 *   (`/export/heatmap.geojsonl` → 302 to the static GCS object).
 *
 * The bulk export is the ONE members-only affordance (the MAP itself stays
 * public); anonymous visitors get a login CTA instead of the download links.
 */
export default function ExportHeatmapModal({ onClose }: ExportHeatmapModalProps) {
  const { t } = useI18n();
  // ── Members-only EXPORT gate (2026-07 posture) ─────────────────────────────
  // The community MAP is PUBLIC — anyone can view the heatmap. The bulk ODbL
  // community-data EXPORT is the ONE members-only affordance: it's the growth
  // conversion point (view freely → log in to take the data → contribute back).
  // Lazy init is safe: this modal is only ever mounted client-side on a user
  // click (never during SSG / first hydration), so localStorage is available.
  const [authed] = useState<boolean>(
    () => typeof window !== 'undefined' && !!getCurrentUserId(),
  );
  const [acceptedOdbl, setAcceptedOdbl] = useState(false);

  // "Use as a calque" — the community heatmap is also a public raster XYZ tile
  // pyramid, so it can be added as an OVERLAY layer in gpx.studio / VisuGPX via
  // one URL (no download). The XYZ TEMPLATE (…/{z}/{x}/{y}.png) is the correct
  // URL — NOT tiles.json (gpx.studio would read that as a vector source and show
  // nothing). Full tutorial + per-sport calques live at /calque.
  // Per-sport calque (road as the default example; other sports + the tutorial
  // at /calque). The all-sports "Tous" calque was dropped — a blended overlay
  // isn't useful for planning; the all-sports view lives on the landing hero.
  const calqueTileUrl =
    'https://tiles.chemins-communs.fr/raster-road/{z}/{x}/{y}.png';
  const [calqueCopied, setCalqueCopied] = useState(false);
  const copyCalqueUrl = () => {
    navigator.clipboard?.writeText(calqueTileUrl).then(
      () => { setCalqueCopied(true); setTimeout(() => setCalqueCopied(false), 2000); },
      () => {/* clipboard blocked — the URL is visible to select manually */},
    );
  };

  const pmtilesUrl = `${API_URL}/export/heatmap.pmtiles`;
  const geojsonlUrl = `${API_URL}/export/heatmap.geojsonl`;

  // Anonymous visitor → the community EXPORT is members-only. Show a login CTA
  // instead of the export form (the MAP itself stays public — this gates only
  // the bulk ODbL data download). Reuses the passwordless EmailLoginForm.
  if (!authed) {
    return (
      <div
        data-testid="export-heatmap-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-heatmap-gate-title"
        style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
        }}
        onClick={onClose}
      >
        <div
          data-testid="export-login-gate"
          style={{
            background: '#0d1420', borderRadius: 10, padding: 24,
            width: 440, maxWidth: '92vw', maxHeight: '92vh', overflowY: 'auto',
            boxShadow: '0 8px 24px rgba(0,0,0,0.35)',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <h3
            id="export-heatmap-gate-title"
            style={{ marginTop: 0, marginBottom: 8, fontSize: 18, color: '#fff' }}
          >
            {t('exportGate.title')}
          </h3>
          <p style={{ fontSize: 13.5, color: 'rgba(255,255,255,0.6)', lineHeight: 1.55, margin: '0 0 18px' }}>
            {t('exportGate.subtitle')}
          </p>
          <div
            style={{
              background: 'rgba(46,204,113,0.06)',
              border: '1px solid rgba(46,204,113,0.2)',
              borderRadius: 12, padding: 16, textAlign: 'left',
            }}
          >
            <p style={{ color: '#fff', fontSize: 14.5, fontWeight: 700, margin: '0 0 4px' }}>
              {t('exportGate.cta')}
            </p>
            <p style={{ color: 'rgba(255,255,255,0.5)', fontSize: 12.5, margin: '0 0 14px', lineHeight: 1.5 }}>
              {t('exportGate.sub')}
            </p>
            <EmailLoginForm variant="dark" />
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
            <button
              type="button"
              onClick={onClose}
              data-testid="export-gate-close"
              style={{
                padding: '6px 14px', borderRadius: 5,
                border: '1px solid rgba(255,255,255,0.2)', background: 'transparent',
                color: 'rgba(255,255,255,0.7)', cursor: 'pointer', fontSize: 13,
              }}
            >
              {t('export.cancel')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const linkStyle = (enabled: boolean) => ({
    padding: '8px 14px', borderRadius: 5, border: 'none',
    background: enabled ? '#2d6a4f' : '#cfd8d3',
    color: '#fff', fontWeight: 600, textDecoration: 'none', fontSize: 13,
    cursor: enabled ? 'pointer' : 'not-allowed',
    opacity: enabled ? 1 : 0.7,
    pointerEvents: enabled ? ('auto' as const) : ('none' as const),
    display: 'inline-block',
  });

  return (
    <div
      data-testid="export-heatmap-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="export-heatmap-title"
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: '#fff', borderRadius: 10, padding: 24,
          width: 480, maxWidth: '92vw', maxHeight: '92vh', overflowY: 'auto',
          boxShadow: '0 8px 24px rgba(0,0,0,0.15)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="export-heatmap-title" style={{ marginTop: 0, marginBottom: 12, fontSize: 18 }}>
          {t('export.title')}
        </h3>
        <p style={{ fontSize: 13, color: '#555', lineHeight: 1.5, margin: '0 0 16px' }}>
          {t('export.intro')}
        </p>

        {/* Use-as-a-calque (overlay) in gpx.studio / VisuGPX — the "plan a route
            ON TOP of the heatmap" path. One public URL, no download. */}
        <div style={{ border: '1px solid #d8e6df', background: '#f4faf7', borderRadius: 8, padding: '12px 14px', margin: '0 0 16px' }}>
          <div style={{ fontSize: 13, fontWeight: 600, margin: '0 0 4px' }}>
            {t('export.calque.title')}
          </div>
          <p style={{ fontSize: 12.5, color: '#555', lineHeight: 1.5, margin: '0 0 8px' }}>
            {t('export.calque.bodyBefore')}
            <strong>{t('export.calque.bodyStrong')}</strong>
            {t('export.calque.bodyAfter')}
          </p>
          <div style={{ display: 'flex', gap: 6, alignItems: 'stretch' }}>
            <code style={{ flex: 1, fontSize: 11, background: '#fff', border: '1px solid #cfe0d8', borderRadius: 6, padding: '7px 9px', wordBreak: 'break-all', color: '#2d6a4f' }}>
              {calqueTileUrl}
            </code>
            <button
              type="button"
              onClick={copyCalqueUrl}
              data-testid="calque-copy-url"
              style={{ flexShrink: 0, fontSize: 12, fontWeight: 600, padding: '0 12px', borderRadius: 6, border: 'none', background: '#2d6a4f', color: '#fff', cursor: 'pointer' }}
            >
              {calqueCopied ? t('export.calque.copied') : t('export.calque.copy')}
            </button>
          </div>
          {/* Prominent entry to the full step-by-step tutorial (with per-sport
              calques) — Paul: "il faut bien indiquer dans l'UI comment exporter
              vers gpx.studio". */}
          <Link
            href="/calque"
            data-testid="calque-tutorial-link"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 10, fontSize: 12.5, fontWeight: 700, color: '#1a4731', textDecoration: 'none' }}
          >
            <span aria-hidden="true">🗺️</span> {t('export.calque.tutorialCta')} →
          </Link>
        </div>

        {/* Static, pre-computed downloads. */}
        <fieldset style={{ border: '1px solid #e5e5e5', borderRadius: 6, padding: '10px 12px', margin: '0 0 16px' }}>
          <legend style={{ fontSize: 11, color: '#888', padding: '0 4px' }}>{t('export.downloads.legend')}</legend>

          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '6px 0' }}>
            <div style={{ flex: 1, fontSize: 13 }}>
              <strong>PMTiles</strong>{' '}
              <span style={{ color: '#888' }}>{t('export.pmtiles.desc')}</span>
            </div>
            <a
              href={acceptedOdbl ? pmtilesUrl : undefined}
              data-testid="export-download-pmtiles"
              aria-disabled={!acceptedOdbl}
              onClick={(e) => { if (!acceptedOdbl) e.preventDefault(); }}
              style={linkStyle(acceptedOdbl)}
              {...(acceptedOdbl ? { download: 'heatmap-display.pmtiles' } : {})}
            >
              {t('export.download')}
            </a>
          </div>

          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '6px 0' }}>
            <div style={{ flex: 1, fontSize: 13 }}>
              <strong>GeoJSONL</strong>{' '}
              <span style={{ color: '#888' }}>{t('export.geojsonl.desc')}</span>
            </div>
            <a
              href={acceptedOdbl ? geojsonlUrl : undefined}
              data-testid="export-download-geojsonl"
              aria-disabled={!acceptedOdbl}
              onClick={(e) => { if (!acceptedOdbl) e.preventDefault(); }}
              style={linkStyle(acceptedOdbl)}
              {...(acceptedOdbl ? { download: 'heatmap-display.geojsonl.gz' } : {})}
            >
              {t('export.download')}
            </a>
          </div>
        </fieldset>

        <label
          style={{
            display: 'flex', alignItems: 'flex-start', gap: 8,
            margin: '0 0 16px', cursor: 'pointer', fontSize: 13, lineHeight: 1.4,
          }}
        >
          <input
            type="checkbox"
            checked={acceptedOdbl}
            onChange={(e) => setAcceptedOdbl(e.target.checked)}
            data-testid="export-odbl-checkbox"
            style={{ marginTop: 2, accentColor: '#2d6a4f', flexShrink: 0 }}
          />
          <span>
            {t('export.odbl.before')}
            <a
              href="https://opendatacommons.org/licenses/odbl/1-0/"
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: '#2d6a4f', textDecoration: 'underline' }}
            >
              ODbL-1.0
            </a>
            {t('export.odbl.after')}
          </span>
        </label>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: '6px 14px', borderRadius: 5,
              border: '1px solid #ccc', background: '#fff', color: '#555',
              cursor: 'pointer', fontSize: 13,
            }}
          >
            {t('export.cancel')}
          </button>
        </div>
      </div>
    </div>
  );
}
