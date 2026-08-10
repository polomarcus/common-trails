'use client';

/**
 * The ONE contribution card — TWO clearly-labeled intent blocks.
 *
 * Paul's UX call (2026-07): "un bloc dépôt archive strava (avec lien pour
 * avoir l'extrait) / et un bloc dépôt manuel de trace GPX, avec un système
 * pour laisser l'utilisateur dire que c'est VTT/Gravel etc".
 *
 *   - **Block A — Strava archive (.zip)**: signed-URL init → PUT straight to
 *     storage → complete (`uploadArchive`), GB-safe, never touches the
 *     ~32–50 MB Cloud Run cap. NO sport question — the archive's
 *     `activities.csv` is authoritative (the default sport is only a silent
 *     fallback). Carries a link that opens the collapsed "get your archive"
 *     how-to further down the page.
 *   - **Block B — loose GPX / FIT**: streamed through `/imports/files`. The
 *     sport chips live HERE — the user states what the traces are
 *     (VTT/Gravel/…) because loose files carry no reliable sport signal.
 *
 * Cross-routing guard: BOTH blocks dispatch through `chooseUploadPath`
 * (pure, unit-tested) — a `.zip` dropped on Block B still takes the archive
 * path (chips ignored), loose GPX dropped on Block A still streams through
 * `/imports/files`. Mis-drops route, they never error.
 *
 * ONE ODbL-consent checkbox (above the blocks) gates BOTH paths. Progress
 * reuses the shared plumbing (`lib/upload-progress`): a live bar for the
 * big-ZIP path (sending X% → finalizing → received ✓) and the simple
 * "Import de N fichier(s)…" line for GPX/FIT — shared area below the blocks.
 */
import { useState, type CSSProperties, type DragEvent } from 'react';

import { API_URL } from '@/lib/api-client';
import { getToken } from '@/lib/auth';
import { useI18n } from '@/lib/i18n';
import { buildUploadSummary } from '@/lib/strava-upload-summary';
import { chooseUploadPath } from '@/lib/upload-route';
import { uploadArchive } from '@/lib/strava-archive-upload';
import { buildFilesConsentFields, CONTRIBUTION_CONSENT_VERSION } from '@/lib/strava-archive-consent';
import { describeUpload, phaseForStage, type UploadPhase } from '@/lib/upload-progress';

const SPORTS = [
  { key: 'road', icon: '🚴' },
  { key: 'gravel', icon: '🪨' },
  { key: 'mtb', icon: '⛰️' },
  { key: 'offroad', icon: '🌿' },
  { key: 'running', icon: '🏃' },
] as const;

export default function ContributionDropzone() {
  const { t, locale } = useI18n();
  const [sport, setSport] = useState('mtb');
  const [dragOver, setDragOver] = useState<'archive' | 'files' | null>(null);

  // Archive (.zip → direct-to-storage) progress phase + byte fraction.
  const [phase, setPhase] = useState<UploadPhase>('idle');
  const [fraction, setFraction] = useState(0);
  // GPX/FIT (/imports/files) status line + in-flight flag.
  const [filesMsg, setFilesMsg] = useState<string | null>(null);
  const [filesBusy, setFilesBusy] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  const view = describeUpload(phase, fraction);
  const busy = view.busy || filesBusy;
  const done = phase === 'done';
  // No consent GATE any more (Paul 2026-08-07: "le consentement on l'oublie").
  // Uploading IS the affirmative ODbL act; the consent row is still recorded
  // server-side on every upload (the backend community gate needs it), and a
  // passive ODbL note stays visible — but nothing blocks the picker.
  const canPick = !busy;

  const runArchiveUpload = async (file: File, token: string) => {
    setFilesMsg(null);
    setErrMsg(null);
    setFraction(0);
    setPhase('uploading');
    try {
      await uploadArchive({
        apiUrl: API_URL,
        token,
        blob: file,
        payload: {
          sport, // fallback only; a Strava archive's activities.csv is authoritative
          consent: true,
          consent_version: CONTRIBUTION_CONSENT_VERSION,
          consent_text: t('strava.archive.consentLabel'),
          locale,
          filename: file.name || 'strava-archive.zip',
        },
        onProgress: (f) => { setPhase('uploading'); setFraction(f); },
        onStage: (s) => setPhase(phaseForStage(s)),
      });
      setPhase('done');
    } catch (err) {
      setErrMsg(err instanceof Error ? err.message : t('strava.archive.failed'));
      setPhase('error');
    }
  };

  const runFilesUpload = async (files: File[]) => {
    setErrMsg(null);
    setFilesBusy(true);
    setFilesMsg(t('strava.importingFiles', { count: files.length }));
    let imported = 0, skipped = 0, failed = 0;
    // ODbL consent audit trail (gap #7): the first request carries the exact
    // consent wording + version; the backend records ONE contribution_consents
    // row and returns its id, which every subsequent request reuses.
    let consentId: string | null = null;
    for (let i = 0; i < files.length; i++) {
      const formData = new FormData();
      formData.append('file', files[i]);
      formData.append('sport', sport);
      formData.append('contribute_heatmap', 'true');
      // Fire the PMTiles display rebuild ONCE per batch — on the last file only
      // — so a multi-file drop refreshes the community map a single time instead
      // of once per file. The backend triggers the build-pmtiles job on this
      // flag (best-effort). See imports.py::import_files.
      formData.append('rebuild_display', String(i === files.length - 1));
      const consentFields = buildFilesConsentFields(
        t('strava.archive.consentLabel'), locale, consentId,
      );
      for (const [key, value] of Object.entries(consentFields)) formData.append(key, value);
      try {
        const resp = await fetch(`${API_URL}/imports/files`, {
          method: 'POST', credentials: 'include', body: formData,
        });
        const data = await resp.json();
        if (resp.ok) {
          imported += data.imported ?? 0; skipped += data.skipped ?? 0;
          if (data.consent_id) consentId = data.consent_id;
        }
        else failed++;
      } catch { failed++; }
      setFilesMsg(t('strava.importProgress', { current: i + 1, total: files.length }));
    }
    setFilesMsg(buildUploadSummary({ imported, skipped, failed }, t));
    setFilesBusy(false);
  };

  // The single entry point BOTH blocks funnel through: dispatch by FILE TYPE
  // (not by which block was used) — the cross-routing guard. A .zip always
  // takes the archive path, loose GPX/FIT always stream. No consent gate:
  // consent is implicit (recorded server-side on upload).
  const dispatch = async (fileList: FileList | null) => {
    setErrMsg(null);
    const token = getToken();
    if (!token) { setErrMsg(t('strava.loginToImport')); return; }
    const route = chooseUploadPath(fileList);
    if (route.kind === 'empty') return;
    if (route.kind === 'archive') await runArchiveUpload(route.file, token);
    else await runFilesUpload(route.files);
  };

  // Reveal + scroll to the collapsed "get your Strava archive" how-to that
  // lives further down /strava (same mechanism as the page-subtitle link).
  const openStravaHelp = () => {
    const el = document.getElementById('strava-help') as HTMLDetailsElement | null;
    if (!el) return;
    el.open = true;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const reset = () => {
    setPhase('idle');
    setFraction(0);
    setFilesMsg(null);
    setErrMsg(null);
  };

  const blockStyle = (kind: 'archive' | 'files'): CSSProperties => ({
    flex: '1 1 230px', minWidth: 0,
    background: dragOver === kind ? 'rgba(46,204,113,0.16)' : 'rgba(255,255,255,0.03)',
    border: `2px dashed ${dragOver === kind ? '#2ecc71' : 'rgba(46,204,113,0.3)'}`,
    borderRadius: 12, padding: '16px 14px', textAlign: 'center',
    transition: 'all 0.2s ease',
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10,
  });

  const dropHandlers = (kind: 'archive' | 'files') => ({
    onDragOver: (e: DragEvent<HTMLDivElement>) => { if (canPick) { e.preventDefault(); setDragOver(kind); } },
    onDragLeave: () => setDragOver(null),
    onDrop: async (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragOver(null);
      await dispatch(e.dataTransfer.files);
    },
  });

  const pickButtonStyle: CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: 6,
    padding: '10px 18px', borderRadius: 10,
    cursor: canPick ? 'pointer' : 'not-allowed',
    background: 'linear-gradient(135deg, #27ae60, #2d6a4f)',
    color: '#fff', fontWeight: 700, fontSize: 13,
    boxShadow: '0 4px 16px rgba(39,174,96,0.22)',
    opacity: canPick ? 1 : 0.55,
  };

  // ── Archive "received ✓" card (the .zip path finished) ──
  if (done) {
    return (
      <div
        data-testid="strava-gpx-upload"
        style={{
          background: 'rgba(46,204,113,0.1)', border: '1px solid rgba(46,204,113,0.28)',
          borderRadius: 14, padding: '22px 18px', textAlign: 'center',
        }}
      >
        <div style={{
          width: 44, height: 44, borderRadius: '50%',
          background: 'linear-gradient(135deg, #27ae60, #2ecc71)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          margin: '0 auto 10px', boxShadow: '0 6px 20px rgba(39,174,96,0.3)',
        }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
        </div>
        <p data-testid="strava-gpx-msg" style={{ color: '#2ecc71', fontSize: 14, fontWeight: 700, margin: '0 0 8px' }}>
          {t('strava.archive.acceptedQueued')}
        </p>
        <button
          data-testid="hero-reset"
          onClick={reset}
          style={{
            background: 'rgba(255,255,255,0.06)', color: 'rgba(255,255,255,0.75)',
            border: '1px solid rgba(255,255,255,0.12)', borderRadius: 8,
            padding: '7px 16px', fontSize: 12, fontWeight: 600, cursor: 'pointer',
          }}
        >
          {t('strava.archive.importAnother')}
        </button>
      </div>
    );
  }

  return (
    <div
      data-testid="strava-gpx-upload"
      style={{
        background: 'rgba(46,204,113,0.05)',
        border: '1px solid rgba(46,204,113,0.22)',
        borderRadius: 14, padding: '18px 16px', textAlign: 'center',
      }}
    >
      <style>{`
        @keyframes ct-hero-indeterminate { 0% { transform: translateX(-120%); } 100% { transform: translateX(320%); } }
      `}</style>

      <div style={{ fontSize: 28, marginBottom: 4 }} aria-hidden="true">📥</div>
      <p style={{ color: '#fff', fontSize: 16, fontWeight: 700, margin: '0 0 2px' }}>
        {t('strava.depositTitle')}
      </p>
      <p style={{ color: 'rgba(255,255,255,0.45)', fontSize: 12, margin: '0 0 8px' }}>
        {t('strava.depositSub')}
      </p>

      {/* ODbL note — passive, no gate (Paul 2026-08-07: "le consentement on
          l'oublie"). Uploading is the affirmative act; consent is recorded
          server-side on every upload. Kept visible for transparency only. */}
      <p
        data-testid="hero-consent-note"
        style={{ color: 'rgba(255,255,255,0.5)', fontSize: 11.5, lineHeight: 1.5, textAlign: 'left', margin: '0 0 12px' }}
      >
        {t('strava.archive.consentLabel')}
      </p>

      {/* ── The two intent blocks — stacked on mobile, side-by-side when it fits ── */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'stretch' }}>

        {/* Block A — Strava archive (.zip). No sport question: activities.csv governs. */}
        <div data-testid="archive-block" {...dropHandlers('archive')} style={blockStyle('archive')}>
          <div style={{ fontSize: 22, lineHeight: 1 }} aria-hidden="true">📦</div>
          <p style={{ color: '#fff', fontSize: 13.5, fontWeight: 700, margin: 0 }}>
            {t('strava.blockArchiveTitle')}
          </p>
          <p style={{ color: 'rgba(255,255,255,0.45)', fontSize: 11, lineHeight: 1.45, margin: 0 }}>
            {t('strava.sportFromArchive')}
          </p>
          <label style={pickButtonStyle}>
            <input
              type="file"
              accept=".zip"
              disabled={!canPick}
              style={{ display: 'none' }}
              onChange={async (e) => { await dispatch(e.target.files); e.target.value = ''; }}
            />
            {t('strava.chooseArchive')}
          </label>
          <a
            role="button"
            tabIndex={0}
            data-testid="dropzone-strava-help-link"
            onClick={openStravaHelp}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openStravaHelp(); } }}
            style={{ color: '#fc8c4c', textDecoration: 'underline', cursor: 'pointer', fontSize: 11 }}
          >
            {t('strava.archiveHelpLink')}
          </a>
        </div>

        {/* Block B — loose GPX / FIT. The user states the sport HERE (chips). */}
        <div data-testid="files-block" {...dropHandlers('files')} style={blockStyle('files')}>
          <div style={{ fontSize: 22, lineHeight: 1 }} aria-hidden="true">🛰️</div>
          <p style={{ color: '#fff', fontSize: 13.5, fontWeight: 700, margin: 0 }}>
            {t('strava.blockFilesTitle')}
          </p>
          <p style={{ color: 'rgba(255,255,255,0.45)', fontSize: 11, lineHeight: 1.45, margin: 0 }}>
            {t('strava.sportConfirmTitle')}
          </p>
          <div style={{ display: 'flex', gap: 6, justifyContent: 'center', flexWrap: 'wrap' }}>
            {SPORTS.map((s) => (
              <button
                key={s.key}
                type="button"
                data-testid={`sport-chip-${s.key}`}
                aria-pressed={sport === s.key}
                onClick={() => setSport(s.key)}
                style={{
                  padding: '5px 10px', borderRadius: 16, fontSize: 11, cursor: 'pointer',
                  background: sport === s.key ? 'rgba(45,106,79,0.3)' : 'rgba(255,255,255,0.04)',
                  color: sport === s.key ? '#2ecc71' : 'rgba(255,255,255,0.4)',
                  border: sport === s.key ? '1px solid rgba(45,106,79,0.5)' : '1px solid rgba(255,255,255,0.08)',
                  fontWeight: sport === s.key ? 700 : 500,
                  transition: 'all 0.15s ease',
                }}
              >
                {s.icon} {t(`sport.${s.key}`)}
              </button>
            ))}
          </div>
          <label style={pickButtonStyle}>
            <input
              type="file"
              accept=".gpx,.fit"
              multiple
              disabled={!canPick}
              style={{ display: 'none' }}
              onChange={async (e) => { await dispatch(e.target.files); e.target.value = ''; }}
            />
            {t('strava.chooseFiles')}
          </label>
          <p style={{ color: 'rgba(255,255,255,0.25)', fontSize: 10, margin: 0 }}>
            {t('strava.compatible')}
          </p>
        </div>
      </div>

      {/* Live progress bar for the direct-to-storage .zip path (shared area). */}
      {view.showBar && (
        <div data-testid="archive-progress" style={{ margin: '14px 0 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
            <span style={{ color: 'rgba(255,255,255,0.7)', fontSize: 12, fontWeight: 600 }}>
              {view.labelKey ? t(view.labelKey, view.labelVars) : ''}
            </span>
            {!view.indeterminate && (
              <span style={{ color: 'rgba(255,255,255,0.5)', fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
                {view.pct}%
              </span>
            )}
          </div>
          <div style={{ background: 'rgba(255,255,255,0.08)', borderRadius: 6, height: 8, overflow: 'hidden', position: 'relative' }}>
            {view.indeterminate ? (
              <div style={{
                position: 'absolute', top: 0, bottom: 0, width: '35%',
                background: 'linear-gradient(90deg, transparent, #2ecc71, transparent)',
                animation: 'ct-hero-indeterminate 1.1s ease-in-out infinite',
              }} />
            ) : (
              <div
                data-testid="archive-progressbar"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={view.pct}
                style={{
                  height: '100%', borderRadius: 6,
                  background: 'linear-gradient(90deg, #27ae60, #2ecc71)',
                  width: `${view.pct}%`, transition: 'width 0.3s ease',
                }}
              />
            )}
          </div>
        </div>
      )}

      {/* Simple status line for the GPX/FIT path. */}
      {filesMsg && (
        <p data-testid="strava-gpx-msg" style={{ color: 'rgba(255,255,255,0.6)', fontSize: 12, margin: '10px 0 0', fontWeight: 500 }}>
          {filesMsg}
        </p>
      )}

      {errMsg && (
        <p data-testid="hero-upload-error" style={{ color: '#ff8a80', fontSize: 12, margin: '10px 0 0', fontWeight: 500 }}>
          ✗ {errMsg}
        </p>
      )}
    </div>
  );
}
