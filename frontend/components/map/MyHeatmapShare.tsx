'use client';

/**
 * « Partager ma heatmap » — under the "Ma heatmap" toggle of the Calques menu
 * (docs/ma-heatmap-fichier-prive.md §10). Opt-in, off by default:
 *  - inactive → one button that creates a secret link;
 *  - just created / regenerated → the copyable URLs (gpx.studio style JSON,
 *    OsmAnd/Locus raster template) + how to add it in gpx.studio. The secret
 *    is shown THIS once (the server keeps only its hash);
 *  - active (later visit) → creation date; regenerate to see a link again.
 * "Regénérer le lien" kills the old one, "Désactiver le partage" kills it.
 * Renders nothing when the feature is off for the account.
 */
import { useEffect, useState } from 'react';
import { useI18n } from '@/lib/i18n';
import {
  createOrRegenerateShare, fetchShareStatus, formatShareDate, revokeShare,
  type MyHeatmapShareStatus, type MyHeatmapShareUrls,
} from '@/lib/my-heatmap-share';

const btn: React.CSSProperties = {
  padding: '3px 9px', borderRadius: 6, border: '1px solid #ccc', background: '#fafafa',
  color: '#444', cursor: 'pointer', fontSize: 11, fontWeight: 500,
};

const busyBtn: React.CSSProperties = { cursor: 'progress', opacity: 0.85 };

/** Creating the link can take ~30 s (the masked copy is built first): without
 *  feedback the button looked dead (Paul, 2026-11-11). */
function Spinner() {
  return (
    <>
      <style>{'@keyframes mhs-spin { to { transform: rotate(360deg); } }'}</style>
      <span
        aria-hidden="true"
        data-testid="my-heatmap-share-spinner"
        style={{
          display: 'inline-block', width: 10, height: 10, verticalAlign: '-1px',
          border: '2px solid #cbd5e1', borderTopColor: '#2d6a4f', borderRadius: '50%',
          animation: 'mhs-spin 0.8s linear infinite',
        }}
      />
    </>
  );
}

function CopyField({ label, value, testId }: { label: string; value: string; testId: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable: the field stays selectable */
    }
  };
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ fontSize: 11, color: '#555', fontWeight: 600, marginBottom: 2 }}>{label}</div>
      <div style={{ display: 'flex', gap: 4 }}>
        <input
          readOnly
          value={value}
          data-testid={testId}
          onFocus={(e) => e.currentTarget.select()}
          style={{ flex: 1, minWidth: 0, fontSize: 11, padding: '3px 5px', border: '1px solid #ddd', borderRadius: 4, fontFamily: 'monospace' }}
        />
        <button type="button" onClick={copy} style={btn} data-testid={`${testId}-copy`}>
          {copied ? t('map.share.copied') : t('map.share.copy')}
        </button>
      </div>
    </div>
  );
}

export default function MyHeatmapShare({ dateLocale }: { dateLocale: string }) {
  const { t } = useI18n();
  const [status, setStatus] = useState<MyHeatmapShareStatus | null>(null);
  const [urls, setUrls] = useState<MyHeatmapShareUrls | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  // Collapsed to one line by default: the "Ma heatmap" and "Heatmap commune"
  // toggles stay (nearly) side by side in the Calques menu.
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchShareStatus()
      .then((s) => { if (!cancelled) setStatus(s); })
      .catch(() => { if (!cancelled) setStatus(null); });
    return () => { cancelled = true; };
  }, []);

  if (!status?.available) return null;

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(false);
    try {
      await fn();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  const create = () => run(async () => {
    const res = await createOrRegenerateShare();
    setUrls(res.urls);
    setOpen(true);
    setStatus({ available: true, active: true, created_at: res.created_at, ready: status.ready ?? false });
  });

  const regenerate = () => {
    if (!window.confirm(t('map.share.confirmRegenerate'))) return;
    void create();
  };

  const revoke = () => {
    if (!window.confirm(t('map.share.confirmRevoke'))) return;
    void run(async () => {
      await revokeShare();
      setUrls(null);
      setStatus({ available: true, active: false });
    });
  };

  return (
    <div data-testid="my-heatmap-share" style={{ padding: '0 14px 6px 40px', fontSize: 11, color: '#666', lineHeight: 1.4 }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        data-testid="my-heatmap-share-toggle"
        style={{ border: 'none', background: 'transparent', padding: 0, cursor: 'pointer', fontSize: 11, color: '#2d6a4f', fontWeight: 600 }}
      >
        🔗 {status.active ? t('map.share.activeShort') : t('map.share.cta')} {open ? '\u25B4' : '\u25BE'}
      </button>
      {!open ? null : !status.active ? (
        <>
          <div style={{ marginTop: 4 }}>{t('map.share.intro')}</div>
          <button type="button" onClick={() => void create()} disabled={busy} aria-busy={busy} style={{ ...btn, marginTop: 6, ...(busy ? busyBtn : null) }} data-testid="my-heatmap-share-create">
            {busy ? <><Spinner /> {t('map.share.creating')}</> : t('map.share.create')}
          </button>
        </>
      ) : (
        <>
          <div data-testid="my-heatmap-share-active">
            {t('map.share.active', { date: formatShareDate(status.created_at, dateLocale) })}
            {status.ready && typeof status.activity_count === 'number' && (
              <> {t('map.share.count', { count: status.activity_count })}</>
            )}
          </div>
          {status.ready === false && <div style={{ fontStyle: 'italic' }}>{t('map.share.pending')}</div>}
          {urls ? (
            <>
              <CopyField label={t('map.share.styleLabel')} value={urls.style} testId="my-heatmap-share-style" />
              <div style={{ marginTop: 3 }}>{t('map.share.gpxStudioHint')}</div>
              <CopyField label={t('map.share.rasterLabel')} value={urls.raster} testId="my-heatmap-share-raster" />
              <div style={{ marginTop: 6 }}>{t('map.share.onceHint')}</div>
            </>
          ) : (
            <div style={{ marginTop: 3 }}>{t('map.share.hiddenHint')}</div>
          )}
          <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
            <button type="button" onClick={regenerate} disabled={busy} style={btn} data-testid="my-heatmap-share-regenerate">
              {t('map.share.regenerate')}
            </button>
            <button type="button" onClick={revoke} disabled={busy} style={{ ...btn, color: '#b42318' }} data-testid="my-heatmap-share-revoke">
              {t('map.share.revoke')}
            </button>
          </div>
        </>
      )}
      {busy && status.active && (
        <div role="status" data-testid="my-heatmap-share-busy" style={{ marginTop: 4 }}>
          <Spinner /> {t('map.share.updating')}
        </div>
      )}
      {error && <div role="alert" style={{ color: '#b42318', marginTop: 4 }}>{t('map.share.error')}</div>}
    </div>
  );
}
