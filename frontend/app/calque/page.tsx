'use client';

import { useState } from 'react';
import Link from 'next/link';
import TopNav from '@/components/TopNav';
import { useT } from '@/lib/i18n';

// Raster XYZ templates — the combined ("all") calque + one per sport. NOT the
// .json (gpx.studio would read it as vector) and NOT the .pmtiles. The per-sport
// prefixes mirror the backend build (_CALQUE_SPORTS in build_pmtiles.py).
const BASE = 'https://tiles.chemins-communs.fr';
function tileUrlFor(sport: string): string {
  return sport === 'all'
    ? `${BASE}/raster/{z}/{x}/{y}.png`
    : `${BASE}/raster-${sport}/{z}/{x}/{y}.png`;
}

// The calque export is PER SPORT (no "Tous" — the all-sports blend is dominated
// by the majority sport and isn't a useful planning overlay; the all-sports view
// lives on the landing page's hero instead). Chips mirror the map/home sports.
const SPORTS: { key: string; emoji: string; label: string }[] = [
  { key: 'road', emoji: '🚴', label: 'Route' },
  { key: 'gravel', emoji: '🪨', label: 'Gravel' },
  { key: 'mtb', emoji: '⛰️', label: 'VTT' },
  { key: 'offroad', emoji: '🌿', label: 'Off-road' },
  { key: 'running', emoji: '🏃', label: 'Course' },
];

const GREEN = '#2d6a4f';
const DEEP = '#1a4731';
const INK = '#444';

export default function CalquePage() {
  const t = useT();
  const [sport, setSport] = useState('road');
  const tileUrl = tileUrlFor(sport);
  return (
    <div style={{ minHeight: '100vh', background: '#f5f5f0' }}>
      <TopNav breadcrumbs={[{ label: t('calque.breadcrumb') }]} />

      <div style={{ maxWidth: 720, margin: '32px auto', padding: '0 24px 60px' }}>
        <h1 style={{ fontSize: 26, fontWeight: 800, color: DEEP, margin: '0 0 8px', letterSpacing: '-0.01em' }}>
          {t('calque.title')}
        </h1>
        <p style={{ fontSize: 15, color: INK, lineHeight: 1.7, margin: '0 0 24px', maxWidth: '38em' }}>
          {t('calque.intro')}
        </p>

        <SportPicker sport={sport} setSport={setSport} />
        <CopyUrl t={t} url={tileUrl} />

        <StepsTitle>{t('calque.stepsTitle')}</StepsTitle>
        <Step n={1} title={t('calque.step1Title')}>
          <P>{t('calque.step1Text')}</P>
        </Step>
        <Step n={2} title={t('calque.step2Title')}>
          <P>{t('calque.step2Text')}</P>
          <SettingsPanel t={t} url={tileUrl} />
        </Step>
        <Step n={3} title={t('calque.step3Title')}>
          <P>{t('calque.step3Text')}</P>
        </Step>
        <Step n={4} title={t('calque.step4Title')}>
          <P>{t('calque.step4Text')}</P>
          <P>{t('calque.step4Text2')}</P>
        </Step>

        <Callout tone="danger" title={t('calque.trap1Title')}>
          <P>{t('calque.trap1Text')}</P>
          <UrlPicker
            ok={['…/raster-road/{z}/{x}/{y}.png']}
            ko={['…/raster-road/tiles.json', '….pmtiles']}
          />
        </Callout>
        <Callout tone="warn" title={t('calque.trap2Title')}>
          <P>{t('calque.trap2Text')}</P>
        </Callout>

        <div style={{ background: '#f0faf4', border: '1px solid #d4edda', borderRadius: 16, padding: '24px 28px', margin: '24px 0 8px' }}>
          <h2 style={{ fontSize: 17, fontWeight: 800, color: DEEP, margin: '0 0 8px' }}>{t('calque.whyTitle')}</h2>
          <P>{t('calque.whyText')}</P>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '18px 24px', borderTop: '1px solid #e5e5e0', margin: '28px 0 0', paddingTop: 22 }}>
          <Note title={t('calque.noteZoomT')}>{t('calque.noteZoom')}</Note>
          <Note title={t('calque.noteCoverageT')}>{t('calque.noteCoverage')}</Note>
          <Note title={t('calque.noteAppsT')}>{t('calque.noteApps')}</Note>
          <Note title={t('calque.noteLicenseT')}>
            {t('calque.noteLicense')}{' '}
            <a href="https://opendatacommons.org/licenses/odbl/1-0/" style={{ color: GREEN, fontWeight: 600 }}>ODbL 1.0</a>.
          </Note>
        </div>

        <div style={{ marginTop: 30, paddingTop: 20, borderTop: '1px solid #e5e5e0', textAlign: 'center' }}>
          <Link href="/map" style={{ color: GREEN, fontWeight: 700, fontSize: 14, textDecoration: 'none' }}>
            {t('support.backToMap')}
          </Link>
        </div>
      </div>
    </div>
  );
}

// ── Sport picker — swaps the calque URL between the combined + per-sport rasters ──

function SportPicker({ sport, setSport }: { sport: string; setSport: (s: string) => void }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
      {SPORTS.map((s) => {
        const on = s.key === sport;
        return (
          <button
            key={s.key}
            type="button"
            onClick={() => setSport(s.key)}
            aria-pressed={on}
            style={{
              cursor: 'pointer', border: `1px solid ${on ? DEEP : '#d8ddd8'}`, borderRadius: 999,
              padding: '7px 14px', fontSize: 13.5, fontWeight: on ? 700 : 500,
              background: on ? DEEP : '#fff', color: on ? '#fff' : '#4a5850',
              display: 'inline-flex', alignItems: 'center', gap: 6, transition: 'all .12s',
            }}
          >
            <span aria-hidden="true">{s.emoji}</span> {s.label}
          </button>
        );
      })}
    </div>
  );
}

// ── The copyable tile URL ────────────────────────────────────────────────────

function CopyUrl({ t, url }: { t: (k: string) => string; url: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(url);
      } else {
        const ta = document.createElement('textarea');
        ta.value = url; document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); ta.remove();
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1900);
    } catch { /* clipboard blocked — user can still select the text */ }
  };
  // Highlight the {z}/{x}/{y} template inside the (sport-dependent) URL.
  const [before, after] = url.split('{z}/{x}/{y}');
  return (
    <div style={{ background: '#10231a', borderRadius: 14, padding: '16px 16px 14px', boxShadow: '0 1px 4px rgba(0,0,0,0.12)' }}>
      <div style={{ fontSize: 11, letterSpacing: '0.12em', textTransform: 'uppercase', color: '#78a892', fontWeight: 700, margin: '0 0 10px' }}>
        {t('calque.urlLabel')}
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'stretch', flexWrap: 'wrap' }}>
        <code style={{
          flex: '1 1 320px', minWidth: 0, overflowX: 'auto', whiteSpace: 'nowrap',
          background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(111,227,168,0.22)',
          borderRadius: 9, padding: '12px 13px', fontSize: 14, color: '#cfe9db',
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        }}>
          {before}<span style={{ color: '#6fe3a8' }}>{'{z}/{x}/{y}'}</span>{after}
        </code>
        <button
          type="button"
          onClick={copy}
          aria-label={t('calque.copy')}
          style={{
            flex: '0 0 auto', cursor: 'pointer', border: 0, borderRadius: 9, padding: '0 18px',
            minHeight: 44, background: copied ? '#dff3e8' : '#23a866', color: copied ? DEEP : '#06130c',
            fontSize: 14, fontWeight: 700,
          }}
        >
          {copied ? t('calque.copied') : t('calque.copy')}
        </button>
      </div>
    </div>
  );
}

// ── Faux gpx.studio settings panel ───────────────────────────────────────────

function SettingsPanel({ t, url }: { t: (k: string) => string; url: string }) {
  return (
    <div style={{ marginTop: 14, background: '#f6f8f6', border: '1px solid #e0e6e0', borderRadius: 11, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 13px', background: DEEP, color: '#dff0e6', fontSize: 13.5, fontWeight: 700 }}>
        <span aria-hidden="true">🗺️</span> {t('calque.panelTitle')}
      </div>
      <div style={{ padding: '4px 13px 12px' }}>
        <Field label={t('calque.fieldName')} value="Chemins Communs" />
        <Field label={t('calque.fieldUrl')} mono value={url} />
        <Field label={t('calque.fieldZoom')} mono value="14" />
        <Field label={t('calque.fieldType')} selected value={t('calque.valType')} />
      </div>
    </div>
  );
}

function Field({ label, value, mono, selected }: { label: string; value: string; mono?: boolean; selected?: boolean }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: 10, alignItems: 'center', padding: '9px 0', borderTop: '1px solid #e6ece6' }}>
      <span style={{ fontSize: 13, color: '#5a6b60', fontWeight: 600 }}>{label}</span>
      {selected ? (
        <span style={{ justifySelf: 'start', display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 13, fontWeight: 700, color: DEEP, background: '#dff3e8', border: '1px solid #b7e2c9', borderRadius: 7, padding: '7px 11px' }}>
          <span aria-hidden="true">✓</span> {value}
        </span>
      ) : (
        <span style={{
          fontSize: 13, color: '#333', background: '#fff', border: '1px solid #e0e6e0', borderRadius: 7,
          padding: '8px 10px', overflowX: 'auto', whiteSpace: 'nowrap',
          fontFamily: mono ? 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' : undefined,
        }}>{value}</span>
      )}
    </div>
  );
}

// ── Correct-vs-wrong URL picker (inside the danger callout) ──────────────────

function UrlPicker({ ok, ko }: { ok: string[]; ko: string[] }) {
  const row = (mark: string, color: string, url: string) => (
    <div key={url} style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', fontSize: 12.5, color: '#333', overflowX: 'auto', whiteSpace: 'nowrap' }}>
      <span aria-hidden="true" style={{ color, fontWeight: 800 }}>{mark}</span> {url}
    </div>
  );
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 10, background: 'rgba(0,0,0,0.03)', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 8, padding: '10px 12px' }}>
      {ok.map((u) => row('✓', '#1a7a48', u))}
      {ko.map((u) => row('✕', '#c0392b', u))}
    </div>
  );
}

// ── Small building blocks (site idiom) ───────────────────────────────────────

function StepsTitle({ children }: { children: React.ReactNode }) {
  return <p style={{ fontSize: 12.5, letterSpacing: '0.12em', textTransform: 'uppercase', color: GREEN, fontWeight: 700, margin: '34px 0 16px' }}>{children}</p>;
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div style={{ position: 'relative', background: '#fff', border: '1px solid #eee', borderRadius: 14, padding: '20px 22px 20px 64px', marginBottom: 14, boxShadow: '0 1px 4px rgba(0,0,0,0.06)' }}>
      <span aria-hidden="true" style={{
        position: 'absolute', left: 18, top: 20, width: 32, height: 32, borderRadius: 9,
        display: 'grid', placeItems: 'center', background: '#eef4ef', color: GREEN,
        fontWeight: 800, fontSize: 15, border: '1px solid #dbe6de', fontVariantNumeric: 'tabular-nums',
      }}>{n}</span>
      <h3 style={{ margin: '2px 0 6px', fontSize: 17, fontWeight: 700, color: DEEP }}>{title}</h3>
      {children}
    </div>
  );
}

function P({ children }: { children: React.ReactNode }) {
  return <p style={{ fontSize: 14.5, color: INK, lineHeight: 1.65, margin: '0 0 6px' }}>{children}</p>;
}

function Callout({ tone, title, children }: { tone: 'danger' | 'warn'; title: string; children: React.ReactNode }) {
  const c = tone === 'danger'
    ? { bg: '#fbeae8', border: '#e9c3bd', ink: '#b23327', ic: '!' }
    : { bg: '#fbf0df', border: '#e6cd9d', ink: '#a8590a', ic: '△' };
  return (
    <div style={{ display: 'flex', gap: 13, background: c.bg, border: `1px solid ${c.border}`, borderRadius: 12, padding: '16px 18px', margin: '14px 0 0' }}>
      <span aria-hidden="true" style={{ flex: '0 0 auto', width: 24, height: 24, borderRadius: 7, display: 'grid', placeItems: 'center', background: c.ink, color: '#fff', fontWeight: 800, fontSize: 14 }}>{c.ic}</span>
      <div style={{ minWidth: 0 }}>
        <h4 style={{ margin: '1px 0 5px', fontSize: 15.5, fontWeight: 800, color: c.ink }}>{title}</h4>
        {children}
      </div>
    </div>
  );
}

function Note({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p style={{ fontSize: 11, letterSpacing: '0.1em', textTransform: 'uppercase', color: GREEN, fontWeight: 700, margin: '0 0 4px' }}>{title}</p>
      <p style={{ fontSize: 13.5, color: '#666', lineHeight: 1.5, margin: 0 }}>{children}</p>
    </div>
  );
}
