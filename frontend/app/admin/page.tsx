'use client';

import { useEffect, useState } from 'react';
import { API_URL } from '@/lib/api-client';
import { useI18n } from '@/lib/i18n';
import {
  type ChipLevel,
  type Freshness,
  type HeatmapMetricPoint,
  type HeatmapMetricsResponse,
  ageMs,
  freshnessLevel,
  relativeParts,
  resyncLevel,
  stravaLevel,
  sparklinePath,
  FRESHNESS_THRESHOLDS,
} from '@/lib/admin-metrics';

interface SyncUser {
  athlete_name: string;
  last_synced_at: string | null;
  delay_days: number | null;
  sync_failures: number;
  token_expired: boolean;
  disabled: boolean;
}

interface SyncStatus {
  total_connected: number;
  stale_30d: number;
  token_expired: number;
  disabled: number;
  users: SyncUser[];
}

interface RecentJob {
  job: string;
  name: string;
  start_time: string | null;
  completion_time: string | null;
  status: 'ok' | 'failed' | 'running' | 'unknown';
  succeeded: number;
  failed: number;
}

interface DashboardData {
  users: {
    total: number;
    signups_7d: number;
    signups_30d: number;
    active_7d: number;
    active_30d: number;
  };
  activities: {
    total: number;
    total_distance_km: number;
    total_elevation_m: number;
    by_sport: Record<string, { count: number; distance_m: number }>;
    by_provider: Record<string, number>;
  };
  routes: {
    total: number;
    by_visibility: Record<string, number>;
  };
  integrations: {
    strava_connected: number;
  };
  heatmap: {
    edges: number;
    cells: number;
    max_contributors_on_edge: number;
    osm_tagged: number;
    grid_fallback: number;
    osm_way_id_pct: number;
    grid_fallback_pct: number;
  };
  graph_health: {
    total_vertices: number;
    dead_ends: number;
    dead_end_pct: number;
  };
  network: {
    dfci_edges: number;
    trail_edges: number;
  };
  database: {
    size: string;
  };
  system: {
    revision: string;
    service: string;
    is_prod: boolean;
  };
}

interface RecentRoute {
  id: string;
  name: string;
  sport: string;
  visibility: string;
  distance_m: number | null;
  elevation_gain_m: number | null;
  created_at: string | null;
  username: string | null;
}

interface RecentActivity {
  id: string;
  name: string | null;
  sport: string;
  provider: string;
  distance_m: number | null;
  elevation_gain_m: number | null;
  activity_date: string | null;
  created_at: string | null;
  username: string | null;
}

interface AdminUserRow {
  id: string;
  email: string;
  username: string;
  is_admin: boolean;
  created_at: string | null;
  strava: {
    athlete_id: string | null;
    athlete_name: string | null;
    connected_at: string | null;
    expires_at: number | null;
    token_expired: boolean;
    last_synced_at: string | null;
    sync_failures: number;
    reconnect_required: boolean;
  } | null;
  activities: {
    total: number;
    total_distance_m: number;
    by_sport: Record<string, { count: number; distance_m: number }>;
    by_provider: Record<string, number>;
    first_activity_date: string | null;
    last_activity_date: string | null;
    skipped_import_count: number;
    failed_import_count: number;
  };
}

interface ArchiveItem {
  id: string;
  user_id: string;
  email: string | null;
  status: string;
  attempts: number;
  members_total: number | null;
  imported: number;
  skipped: number;
  failed: number;
  last_error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface ArchivesData {
  counts: Record<string, number>;
  items: ArchiveItem[];
}

// Lifecycle order of a pending_archives row (matches the backend's
// _ARCHIVE_STATUSES — the counts object always carries all five, zero-filled).
const ARCHIVE_STATUSES = ['awaiting_upload', 'uploaded', 'processing', 'done', 'failed'] as const;

const ARCHIVE_STATUS_STYLES: Record<string, { bg: string; color: string }> = {
  awaiting_upload: { bg: '#f3f4f6', color: '#6b7280' },
  uploaded: { bg: '#eff6ff', color: '#2563eb' },
  processing: { bg: '#fffbeb', color: '#d97706' },
  done: { bg: '#f0fdf4', color: '#16a34a' },
  failed: { bg: '#fef2f2', color: '#dc2626' },
};

// SPORT_LABELS is built inside AdminPage from the i18n dictionary (locale-aware);
// only the colour map is a static module const.
const SPORT_COLORS: Record<string, string> = {
  road: '#2563eb', gravel: '#d97706', mtb: '#7c3aed', offroad: '#0891b2', running: '#dc2626',
};

// fmt is locale-aware — see AdminPage component
let _fmtLocale = 'fr-FR';
function fmt(n: number): string {
  return n.toLocaleString(_fmtLocale);
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{
      background: '#fff', borderRadius: 12, padding: '20px 24px',
      border: '1px solid #e5e7eb', boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
    }}>
      <h2 style={{ fontSize: 14, fontWeight: 600, color: '#6b7280', marginBottom: 16, textTransform: 'uppercase', letterSpacing: 1 }}>
        {title}
      </h2>
      {children}
    </div>
  );
}

function Stat({ label, value, unit, color }: { label: string; value: string | number; unit?: string; color?: string }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontSize: 28, fontWeight: 800, color: color || '#1f2937' }}>
        {typeof value === 'number' ? fmt(value) : value}
        {unit && <span style={{ fontSize: 14, fontWeight: 400, color: '#9ca3af', marginLeft: 4 }}>{unit}</span>}
      </div>
      <div style={{ fontSize: 12, color: '#6b7280', marginTop: 4 }}>{label}</div>
    </div>
  );
}

const CHIP_STYLES: Record<ChipLevel, { bg: string; color: string; dot: string }> = {
  green: { bg: '#f0fdf4', color: '#16a34a', dot: '#16a34a' },
  amber: { bg: '#fffbeb', color: '#d97706', dot: '#d97706' },
  red: { bg: '#fef2f2', color: '#dc2626', dot: '#dc2626' },
  unknown: { bg: '#f3f4f6', color: '#6b7280', dot: '#9ca3af' },
};

function Chip({ level, children }: { level: ChipLevel; children: React.ReactNode }) {
  const s = CHIP_STYLES[level];
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 600,
      padding: '3px 10px', borderRadius: 999, background: s.bg, color: s.color,
    }}>
      <span style={{ width: 7, height: 7, borderRadius: 999, background: s.dot }} />
      {children}
    </span>
  );
}

function FreshnessRow({ label, level, text }: { label: string; level: ChipLevel; text: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, padding: '6px 0' }}>
      <span style={{ fontSize: 13, color: '#374151' }}>{label}</span>
      <Chip level={level}>{text}</Chip>
    </div>
  );
}

function FreshnessPanel({ freshness }: { freshness: Freshness }) {
  const { t } = useI18n();
  const now = Date.now();
  const rel = (iso: string | null): string => {
    const p = relativeParts(ageMs(iso, now));
    if (p.unit === 'never') return t('admin.mon.rel.never');
    if (p.unit === 'now') return t('admin.mon.rel.now');
    return t(`admin.mon.rel.${p.unit}`, { n: p.value });
  };

  const activityLevel = freshnessLevel(
    ageMs(freshness.last_activity_at, now),
    FRESHNESS_THRESHOLDS.activity.amber, FRESHNESS_THRESHOLDS.activity.red,
  );
  const rebuildLevel = freshnessLevel(
    ageMs(freshness.heat_agg_updated_at, now),
    FRESHNESS_THRESHOLDS.rebuild.amber, FRESHNESS_THRESHOLDS.rebuild.red,
  );
  const resyncColor = resyncLevel(freshness.last_resync_status);
  const resyncStatusLabel = (() => {
    const s = (freshness.last_resync_status || '').toUpperCase();
    if (s === 'COMPLETED') return t('admin.mon.resync.completed');
    if (s === 'RUNNING') return t('admin.mon.resync.running');
    if (s === 'PENDING') return t('admin.mon.resync.pending');
    if (s === 'FAILED') return t('admin.mon.resync.failed');
    return t('admin.mon.resync.none');
  })();
  const stravaColor = stravaLevel(freshness);
  const stravaText = (() => {
    if (freshness.strava_connected <= 0) return t('admin.mon.strava.none');
    if (freshness.strava_reconnect_required) return t('admin.mon.strava.reconnect');
    if (freshness.strava_sync_failures > 0) return t('admin.mon.strava.failures', { n: freshness.strava_sync_failures });
    return t('admin.mon.strava.ok');
  })();

  return (
    <Card title={t('admin.mon.freshness')}>
      <div data-testid="admin-freshness">
        <FreshnessRow label={t('admin.mon.lastActivity')} level={activityLevel} text={rel(freshness.last_activity_at)} />
        <FreshnessRow
          label={t('admin.mon.lastRebuild')}
          level={rebuildLevel}
          text={`${rel(freshness.heat_agg_updated_at)} · ${t('admin.mon.ways', { n: freshness.heat_edges_agg })}`}
        />
        <FreshnessRow
          label={t('admin.mon.lastResync')}
          level={resyncColor}
          text={freshness.last_resync_at ? `${resyncStatusLabel} · ${rel(freshness.last_resync_at)}` : resyncStatusLabel}
        />
        <FreshnessRow label={t('admin.mon.stravaSync')} level={stravaColor} text={stravaText} />
        <FreshnessRow
          label={t('admin.mon.webhook')}
          level={freshness.webhook_subscription_present ? 'green' : 'amber'}
          text={freshness.webhook_subscription_present ? t('admin.mon.webhook.present') : t('admin.mon.webhook.missing')}
        />
      </div>
    </Card>
  );
}

function Sparkline({ values, color, label, latest }: { values: number[]; color: string; label: string; latest: string }) {
  const W = 260, H = 44, PAD = 4;
  const d = sparklinePath(values, W, H, PAD);
  return (
    <div style={{ flex: '1 1 220px', minWidth: 200 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
        <span style={{ color: '#6b7280', fontWeight: 600 }}>{label}</span>
        <span style={{ color, fontWeight: 700 }}>{latest}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" role="img" aria-label={label}>
        {d ? (
          <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        ) : null}
      </svg>
    </div>
  );
}

function EvolutionChart({ series }: { series: HeatmapMetricPoint[] }) {
  const { t, locale } = useI18n();
  const fmtLocale = locale === 'en' ? 'en-GB' : 'fr-FR';
  const nf = (n: number) => n.toLocaleString(fmtLocale);
  const last = <T,>(arr: T[]): T | undefined => arr[arr.length - 1];

  return (
    <Card title={t('admin.mon.evolution')}>
      {series.length === 0 ? (
        <p style={{ fontSize: 13, color: '#9ca3af' }}>{t('admin.mon.empty')}</p>
      ) : (
        <div data-testid="admin-evolution">
          <p style={{ fontSize: 12, color: '#9ca3af', marginBottom: 12 }}>
            {t('admin.mon.snapshots', { n: series.length })}
          </p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20 }}>
            <Sparkline
              values={series.map(p => p.heat_edges)} color="#dc2626"
              label={t('admin.mon.series.heatEdges')}
              latest={nf(last(series)?.heat_edges ?? 0)}
            />
            <Sparkline
              values={series.map(p => p.activities)} color="#2563eb"
              label={t('admin.mon.series.activities')}
              latest={nf(last(series)?.activities ?? 0)}
            />
            <Sparkline
              values={series.map(p => p.network_km)} color="#16a34a"
              label={t('admin.mon.series.networkKm')}
              latest={nf(Math.round(last(series)?.network_km ?? 0))}
            />
          </div>
        </div>
      )}
    </Card>
  );
}

export default function AdminPage() {
  const { t, locale } = useI18n();
  _fmtLocale = locale === 'en' ? 'en-GB' : 'fr-FR';
  // Locale-aware sport labels (was a hardcoded-French module const).
  const SPORT_LABELS: Record<string, string> = {
    road: t('admin.sport.road'), gravel: t('admin.sport.gravel'), mtb: t('admin.sport.mtb'),
    offroad: t('admin.sport.offroad'), running: t('admin.sport.running'),
  };
  const [data, setData] = useState<DashboardData | null>(null);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [recentRoutes, setRecentRoutes] = useState<RecentRoute[]>([]);
  const [recentActivities, setRecentActivities] = useState<RecentActivity[]>([]);
  const [recentJobs, setRecentJobs] = useState<RecentJob[]>([]);
  const [adminUsers, setAdminUsers] = useState<AdminUserRow[]>([]);
  const [archives, setArchives] = useState<ArchivesData | null>(null);
  const [requeueing, setRequeueing] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<HeatmapMetricsResponse | null>(null);
  const [resyncLoading, setResyncLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const fetchData = () => {
    setLoading(true);
    const token = document.cookie.match(/auth_token=([^;]+)/)?.[1]
      || localStorage.getItem('token');
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    fetch(`${API_URL}/admin/dashboard`, { headers, credentials: 'include' })
      .then(r => {
        if (r.status === 401) throw new Error(t('admin.notLoggedIn'));
        if (r.status === 403) throw new Error(t('admin.restricted'));
        if (!r.ok) throw new Error(t('admin.errorStatus', { status: r.status }));
        return r.json();
      })
      .then(d => { setData(d); setError(null); setLastRefresh(new Date()); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
    // Fetch sync status separately (non-blocking)
    fetch(`${API_URL}/admin/sync-status`, { headers, credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setSyncStatus(d); })
      .catch(() => {});
    // Recent routes + activities — fire-and-forget. If they 401/403 the
    // main dashboard load already surfaced the error; we just leave these
    // empty rather than re-render the error chrome.
    fetch(`${API_URL}/admin/routes/recent?limit=20`, { headers, credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.items) setRecentRoutes(d.items); })
      .catch(() => {});
    fetch(`${API_URL}/admin/activities/recent?limit=20`, { headers, credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.items) setRecentActivities(d.items); })
      .catch(() => {});
    // Recent Cloud Run Job executions (non-blocking). Fails silently in
    // local dev where there's no metadata server.
    fetch(`${API_URL}/admin/jobs/recent?per_job=3`, { headers, credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.items) setRecentJobs(d.items); })
      .catch(() => {});
    // Per-user observability rows (identity + Strava linkage + sync health
    // + traces breakdown). Non-blocking — the main dashboard load owns the
    // error chrome.
    fetch(`${API_URL}/admin/users`, { headers, credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.users) setAdminUsers(d.users); })
      .catch(() => {});
    // Archive-upload queue (pending_archives) — the beta safety net for a
    // friend's big archive that FAILED in the drain (invisible to them by
    // design). Non-blocking; the main dashboard load owns the error chrome.
    fetch(`${API_URL}/admin/archives`, { headers, credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.counts) setArchives(d); })
      .catch(() => {});
    // Heatmap monitoring: Level-1 freshness + Level-2 evolution series.
    // Non-blocking; the main dashboard load owns the error chrome.
    fetch(`${API_URL}/admin/heatmap-metrics`, { headers, credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.freshness) setMetrics(d); })
      .catch(() => {});
  };

  const triggerResync = () => {
    setResyncLoading(true);
    const token = document.cookie.match(/auth_token=([^;]+)/)?.[1]
      || localStorage.getItem('token');
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    fetch(`${API_URL}/admin/resync`, { method: 'POST', headers, credentials: 'include' })
      .then(r => r.json())
      .then(() => { /* refresh after a moment */ setTimeout(fetchData, 2000); })
      .catch(() => {})
      .finally(() => setResyncLoading(false));
  };

  // One-click recovery for a failed archive row: POST the requeue endpoint
  // (failed → uploaded + drain-job kick) then refresh just the archives card.
  const requeueArchive = (id: string) => {
    setRequeueing(id);
    const token = document.cookie.match(/auth_token=([^;]+)/)?.[1]
      || localStorage.getItem('token');
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    fetch(`${API_URL}/admin/archives/${id}/requeue`, { method: 'POST', headers, credentials: 'include' })
      .then(() => fetch(`${API_URL}/admin/archives`, { headers, credentials: 'include' }))
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.counts) setArchives(d); })
      .catch(() => {})
      .finally(() => setRequeueing(null));
  };

  useEffect(() => { fetchData(); }, []);

  return (
    <div style={{ minHeight: '100vh', background: '#f8fafc', padding: '32px 24px' }}>
      <div style={{ maxWidth: 960, margin: '0 auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 32 }}>
          <div>
            <h1 style={{ fontSize: 24, fontWeight: 800, color: '#1f2937' }}>{t('admin.title')}</h1>
            <p style={{ fontSize: 13, color: '#9ca3af', marginTop: 4 }}>
              {t('admin.monitoring')}
              {lastRefresh && <> · {t('admin.updatedAt', { time: lastRefresh.toLocaleTimeString(_fmtLocale) })}</>}
            </p>
          </div>
          <button
            onClick={fetchData}
            disabled={loading}
            style={{
              padding: '8px 16px', borderRadius: 8, border: '1px solid #d1d5db',
              background: '#fff', cursor: 'pointer', fontSize: 13, fontWeight: 600,
              opacity: loading ? 0.5 : 1,
            }}
          >
            {loading ? t('common.loading') : t('admin.refresh')}
          </button>
        </div>

        {error && (
          <div style={{ background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: 16, marginBottom: 24, color: '#dc2626' }}>
            {t('admin.error', { error })}
          </div>
        )}

        {/* Heatmap monitoring — freshness panel + evolution chart. Rendered
            independently of the main dashboard load so "is it alive + fresh?"
            still shows even if a heavier dashboard query degrades. */}
        {metrics && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 24, marginBottom: 24 }}>
            <FreshnessPanel freshness={metrics.freshness} />
            <EvolutionChart series={metrics.series} />
          </div>
        )}

        {data && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            {/* Revision badge — surfaces which Cloud Run revision is live, so a
                roll can be confirmed without leaving the page. K_REVISION is
                injected by Cloud Run; blank in dev. */}
            {data.system?.revision && (
              <div style={{
                background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 8,
                padding: '8px 14px', fontSize: 12, color: '#1e40af',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              }}>
                <span><strong>{t('admin.apiRevision')}</strong> {data.system.revision}</span>
                <span style={{ opacity: 0.7 }}>{data.system.service}</span>
              </div>
            )}

            {/* Top stats row */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 16 }}>
              <Card title={t('admin.card.users')}>
                <Stat label={t('admin.stat.signedUp')} value={data.users.total} />
              </Card>
              <Card title={t('admin.card.activities')}>
                <Stat label={t('admin.stat.importedTraces')} value={data.activities.total} />
              </Card>
              <Card title={t('admin.card.totalDistance')}>
                <Stat label={t('admin.stat.cumulativeDistance')} value={fmt(data.activities.total_distance_km)} unit="km" />
              </Card>
              <Card title={t('admin.totalElev')}>
                <Stat label={t('admin.stat.cumulativeElevation')} value={fmt(data.activities.total_elevation_m)} unit="m" />
              </Card>
            </div>

            {/* Engagement: signups + active users last 7d/30d */}
            <Card title={t('admin.card.engagement')}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 16 }}>
                <Stat label={t('admin.stat.signups7d')} value={data.users.signups_7d} color="#16a34a" />
                <Stat label={t('admin.stat.signups30d')} value={data.users.signups_30d} color="#16a34a" />
                <Stat label={t('admin.stat.active7d')} value={data.users.active_7d} color="#2563eb" />
                <Stat label={t('admin.stat.active30d')} value={data.users.active_30d} color="#2563eb" />
                <Stat label={t('admin.stat.stravaConnected')} value={data.integrations.strava_connected} color="#fc4c02" />
              </div>
            </Card>

            {/* Provenance des activités (GPX vs Strava vs autres) */}
            <Card title={t('admin.card.provenance')}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 16 }}>
                {Object.entries(data.activities.by_provider).map(([provider, count]) => {
                  const labels: Record<string, { label: string; color: string }> = {
                    strava: { label: 'Strava', color: '#fc4c02' },
                    file: { label: t('admin.provider.gpxUpload'), color: '#2563eb' },
                  };
                  const meta = labels[provider] || { label: provider, color: '#6b7280' };
                  return <Stat key={provider} label={meta.label} value={count} color={meta.color} />;
                })}
              </div>
            </Card>

            {/* Activities by sport */}
            <Card title={t('admin.card.bySport')}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 16 }}>
                {Object.entries(data.activities.by_sport).map(([sport, s]) => (
                  <div key={sport} style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 22, fontWeight: 800, color: SPORT_COLORS[sport] || '#374151' }}>
                      {fmt(s.count)}
                    </div>
                    <div style={{ fontSize: 12, color: '#6b7280' }}>
                      {SPORT_LABELS[sport] || sport}
                    </div>
                    <div style={{ fontSize: 11, color: '#9ca3af' }}>
                      {fmt(Math.round(s.distance_m / 1000))} km
                    </div>
                  </div>
                ))}
              </div>
            </Card>

            {/* Routes + Heatmap + Network */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
              <Card title={t('admin.card.routes')}>
                <div style={{ display: 'flex', gap: 24, justifyContent: 'center' }}>
                  <Stat label={t('admin.stat.total')} value={data.routes.total} />
                  {Object.entries(data.routes.by_visibility).map(([vis, count]) => (
                    <Stat key={vis} label={vis} value={count} />
                  ))}
                </div>
              </Card>
              <Card title={t('admin.card.heatmap')}>
                <div style={{ display: 'flex', gap: 24, justifyContent: 'center', flexWrap: 'wrap' }}>
                  <Stat label={t('admin.stat.edges')} value={data.heatmap.edges} />
                  <Stat label={t('admin.stat.cells')} value={data.heatmap.cells} />
                  <Stat label={t('admin.stat.maxContributors')} value={data.heatmap.max_contributors_on_edge} />
                  {/* Heatmap data quality: PR #247 alerting fires at
                      grid_fallback_ratio > 0.20, so show in amber/red. */}
                  <Stat
                    label={t('admin.stat.pctOnOsm')}
                    value={`${data.heatmap.osm_way_id_pct}%`}
                    color={data.heatmap.osm_way_id_pct < 80 ? '#d97706' : '#16a34a'}
                  />
                  <Stat
                    label={t('admin.stat.pctGridFallback')}
                    value={`${data.heatmap.grid_fallback_pct}%`}
                    color={data.heatmap.grid_fallback_pct > 20 ? '#dc2626' : data.heatmap.grid_fallback_pct > 5 ? '#d97706' : '#16a34a'}
                  />
                </div>
              </Card>
            </div>

            {/* Graph Health */}
            <Card title={t('admin.card.graphHealth')}>
              <div style={{ display: 'flex', gap: 24, justifyContent: 'center' }}>
                <Stat label={t('admin.stat.vertices')} value={data.graph_health.total_vertices} />
                <Stat label={t('admin.stat.deadEnds')} value={data.graph_health.dead_ends}
                  color={data.graph_health.dead_end_pct > 30 ? '#dc2626' : data.graph_health.dead_end_pct > 15 ? '#d97706' : '#16a34a'} />
                <Stat label={t('admin.stat.pctDeadEnds')} value={`${data.graph_health.dead_end_pct}%`}
                  color={data.graph_health.dead_end_pct > 30 ? '#dc2626' : data.graph_health.dead_end_pct > 15 ? '#d97706' : '#16a34a'} />
              </div>
            </Card>

            {/* Network + DB */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
              <Card title={t('admin.card.network')}>
                <div style={{ display: 'flex', gap: 24, justifyContent: 'center' }}>
                  <Stat label={t('admin.stat.dfciTracks')} value={data.network.dfci_edges} color="#cc0000" />
                  <Stat label={t('admin.stat.markedTrails')} value={data.network.trail_edges} color="#388e3c" />
                </div>
              </Card>
              <Card title={t('admin.card.database')}>
                <div style={{ display: 'flex', gap: 24, justifyContent: 'center' }}>
                  <Stat label={t('admin.stat.size')} value={data.database.size} />
                </div>
              </Card>
            </div>

            {/* Recent routes */}
            {recentRoutes.length > 0 && (
              <Card title={t('admin.card.recentRoutes')}>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                    <thead>
                      <tr style={{ textAlign: 'left', color: '#6b7280', fontWeight: 600, borderBottom: '1px solid #e5e7eb' }}>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.name')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.author')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.sport')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.visibility')}</th>
                        <th style={{ padding: '8px 12px', textAlign: 'right' }}>{t('admin.col.distance')}</th>
                        <th style={{ padding: '8px 12px', textAlign: 'right' }}>{t('admin.col.elevation')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.created')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentRoutes.map(r => (
                        <tr key={r.id} style={{ borderBottom: '1px solid #f3f4f6' }}>
                          <td style={{ padding: '8px 12px' }}>
                            <a href={`/routes?id=${r.id}`} style={{ color: '#2563eb', textDecoration: 'none' }}>
                              {r.name}
                            </a>
                          </td>
                          <td style={{ padding: '8px 12px', color: '#6b7280' }}>{r.username ?? '—'}</td>
                          <td style={{ padding: '8px 12px', color: SPORT_COLORS[r.sport] || '#374151', fontWeight: 600 }}>
                            {SPORT_LABELS[r.sport] || r.sport}
                          </td>
                          <td style={{ padding: '8px 12px' }}>
                            <span style={{
                              fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
                              background: r.visibility === 'public' ? '#f0fdf4' : r.visibility === 'unlisted' ? '#fefce8' : '#fef2f2',
                              color: r.visibility === 'public' ? '#16a34a' : r.visibility === 'unlisted' ? '#ca8a04' : '#dc2626',
                            }}>{r.visibility}</span>
                          </td>
                          <td style={{ padding: '8px 12px', textAlign: 'right', color: '#6b7280' }}>
                            {r.distance_m ? `${fmt(Math.round(r.distance_m / 100) / 10)} km` : '—'}
                          </td>
                          <td style={{ padding: '8px 12px', textAlign: 'right', color: '#6b7280' }}>
                            {r.elevation_gain_m ? `${fmt(Math.round(r.elevation_gain_m))} m` : '—'}
                          </td>
                          <td style={{ padding: '8px 12px', color: '#6b7280' }}>
                            {r.created_at ? new Date(r.created_at).toLocaleString(_fmtLocale, {
                              day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                            }) : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            {/* Recent activities */}
            {recentActivities.length > 0 && (
              <Card title={t('admin.card.recentActivities')}>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                    <thead>
                      <tr style={{ textAlign: 'left', color: '#6b7280', fontWeight: 600, borderBottom: '1px solid #e5e7eb' }}>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.name')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.author')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.sport')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.source')}</th>
                        <th style={{ padding: '8px 12px', textAlign: 'right' }}>{t('admin.col.distance')}</th>
                        <th style={{ padding: '8px 12px', textAlign: 'right' }}>{t('admin.col.elevation')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.imported')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentActivities.map(a => (
                        <tr key={a.id} style={{ borderBottom: '1px solid #f3f4f6' }}>
                          <td style={{ padding: '8px 12px' }}>{a.name ?? '—'}</td>
                          <td style={{ padding: '8px 12px', color: '#6b7280' }}>{a.username ?? '—'}</td>
                          <td style={{ padding: '8px 12px', color: SPORT_COLORS[a.sport] || '#374151', fontWeight: 600 }}>
                            {SPORT_LABELS[a.sport] || a.sport}
                          </td>
                          <td style={{ padding: '8px 12px' }}>
                            <span style={{
                              fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
                              background: a.provider === 'strava' ? '#fff7ed' : '#eff6ff',
                              color: a.provider === 'strava' ? '#fc4c02' : '#2563eb',
                            }}>{a.provider}</span>
                          </td>
                          <td style={{ padding: '8px 12px', textAlign: 'right', color: '#6b7280' }}>
                            {a.distance_m ? `${fmt(Math.round(a.distance_m / 100) / 10)} km` : '—'}
                          </td>
                          <td style={{ padding: '8px 12px', textAlign: 'right', color: '#6b7280' }}>
                            {a.elevation_gain_m ? `${fmt(Math.round(a.elevation_gain_m))} m` : '—'}
                          </td>
                          <td style={{ padding: '8px 12px', color: '#6b7280' }}>
                            {a.created_at ? new Date(a.created_at).toLocaleString(_fmtLocale, {
                              day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                            }) : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            {/* Cloud Run Job exec history — "did the last rebuild succeed,
                and how long did it take" without leaving the app. */}
            {recentJobs.length > 0 && (
              <Card title={t('admin.card.recentJobs')}>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                    <thead>
                      <tr style={{ textAlign: 'left', color: '#6b7280', fontWeight: 600, borderBottom: '1px solid #e5e7eb' }}>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.job')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.execution')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.status')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.col.start')}</th>
                        <th style={{ padding: '8px 12px', textAlign: 'right' }}>{t('admin.col.duration')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentJobs.map(j => {
                        const statusColor =
                          j.status === 'ok' ? '#16a34a' :
                          j.status === 'failed' ? '#dc2626' :
                          j.status === 'running' ? '#2563eb' : '#9ca3af';
                        const statusBg =
                          j.status === 'ok' ? '#f0fdf4' :
                          j.status === 'failed' ? '#fef2f2' :
                          j.status === 'running' ? '#eff6ff' : '#f3f4f6';
                        const dur =
                          j.start_time && j.completion_time
                            ? Math.round((new Date(j.completion_time).getTime() - new Date(j.start_time).getTime()) / 1000)
                            : null;
                        const durLabel =
                          dur === null ? '—' :
                          dur < 60 ? `${dur}s` :
                          dur < 3600 ? `${Math.round(dur / 60)} min` :
                          `${(dur / 3600).toFixed(1)} h`;
                        const shortJob = j.job.replace('common-trails-', '').replace('-prod', '');
                        return (
                          <tr key={`${j.job}/${j.name}`} style={{ borderBottom: '1px solid #f3f4f6' }}>
                            <td style={{ padding: '8px 12px', fontWeight: 600 }}>{shortJob}</td>
                            <td style={{ padding: '8px 12px', color: '#6b7280', fontFamily: 'monospace', fontSize: 11 }}>
                              {j.name}
                            </td>
                            <td style={{ padding: '8px 12px' }}>
                              <span style={{
                                fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
                                background: statusBg, color: statusColor,
                              }}>{j.status}</span>
                            </td>
                            <td style={{ padding: '8px 12px', color: '#6b7280' }}>
                              {j.start_time ? new Date(j.start_time).toLocaleString(_fmtLocale, {
                                day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                              }) : '—'}
                            </td>
                            <td style={{ padding: '8px 12px', textAlign: 'right', color: '#6b7280' }}>{durLabel}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </div>
        )}

        {/* Archive-upload queue (pending_archives) — status pills + the most
            recent rows. A friend's failed big-archive upload is invisible to
            them by design; THIS is where the admin sees it, with the drain
            progress + the failure reason. Rendered independently of the main
            dashboard load (own fetch, own state). */}
        {archives && (
          <div style={{ marginTop: 24 }} data-testid="admin-archives">
            <Card title={t('admin.archives.title')}>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: archives.items.length > 0 ? 16 : 0 }}>
                {ARCHIVE_STATUSES.map(status => {
                  const count = archives.counts[status] ?? 0;
                  // A pill only takes its colour when non-zero — so `failed`
                  // reads red (and bold) the moment there is 1 failed archive,
                  // and every zero pill stays quiet grey.
                  const s = count > 0 ? ARCHIVE_STATUS_STYLES[status] : { bg: '#f9fafb', color: '#9ca3af' };
                  return (
                    <span
                      key={status}
                      data-testid={`admin-archives-pill-${status}`}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12,
                        fontWeight: status === 'failed' && count > 0 ? 700 : 600,
                        padding: '3px 10px', borderRadius: 999, background: s.bg, color: s.color,
                      }}
                    >
                      {t(`admin.archives.status.${status}`)}
                      <span style={{ fontWeight: 800 }}>{fmt(count)}</span>
                    </span>
                  );
                })}
              </div>
              {archives.items.length === 0 ? (
                <p style={{ fontSize: 13, color: '#9ca3af', marginTop: 12 }}>{t('admin.archives.empty')}</p>
              ) : (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                    <thead>
                      <tr style={{ textAlign: 'left', color: '#6b7280', fontWeight: 600, borderBottom: '1px solid #e5e7eb' }}>
                        <th style={{ padding: '8px 12px' }}>{t('admin.archives.col.user')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.archives.col.status')}</th>
                        <th style={{ padding: '8px 12px', textAlign: 'right' }}>{t('admin.archives.col.progress')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.archives.col.error')}</th>
                        <th style={{ padding: '8px 12px' }}>{t('admin.archives.col.updated')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {archives.items.map(a => {
                        const s = ARCHIVE_STATUS_STYLES[a.status] || { bg: '#f3f4f6', color: '#6b7280' };
                        return (
                          <tr key={a.id} style={{ borderBottom: '1px solid #f3f4f6' }}>
                            <td style={{ padding: '8px 12px', color: '#6b7280' }}>{a.email ?? a.user_id}</td>
                            <td style={{ padding: '8px 12px' }}>
                              <span style={{
                                fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
                                background: s.bg, color: s.color,
                              }}>
                                {t(`admin.archives.status.${a.status}`)}
                              </span>
                              {a.attempts > 1 && (
                                <span style={{ fontSize: 10, color: '#d97706', marginLeft: 6 }}>
                                  {t('admin.archives.attempts', { n: a.attempts })}
                                </span>
                              )}
                              {a.status === 'failed' && (
                                <button
                                  data-testid={`admin-archives-requeue-${a.id}`}
                                  onClick={() => requeueArchive(a.id)}
                                  disabled={requeueing === a.id}
                                  style={{
                                    marginLeft: 8, fontSize: 11, fontWeight: 600, padding: '2px 8px',
                                    borderRadius: 4, border: '1px solid #d1d5db', background: '#fff',
                                    color: '#2563eb', cursor: 'pointer',
                                    opacity: requeueing === a.id ? 0.5 : 1,
                                  }}
                                >
                                  {requeueing === a.id ? '…' : t('admin.archives.requeue')}
                                </button>
                              )}
                            </td>
                            <td style={{ padding: '8px 12px', textAlign: 'right', color: '#6b7280', whiteSpace: 'nowrap' }}>
                              <span style={{ color: '#16a34a', fontWeight: 600 }}>{fmt(a.imported)}</span>
                              {' / '}
                              <span style={{ color: '#d97706' }}>{fmt(a.skipped)}</span>
                              {' / '}
                              <span style={{ color: a.failed > 0 ? '#dc2626' : '#6b7280', fontWeight: a.failed > 0 ? 700 : 400 }}>{fmt(a.failed)}</span>
                              {a.members_total !== null && (
                                <span style={{ color: '#9ca3af' }}> · {t('admin.archives.members', { n: fmt(a.members_total) })}</span>
                              )}
                            </td>
                            <td style={{ padding: '8px 12px', color: '#dc2626', fontSize: 12, maxWidth: 260 }}>
                              {a.last_error ? (
                                <span
                                  title={a.last_error}
                                  style={{ display: 'inline-block', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom' }}
                                >
                                  {a.last_error}
                                </span>
                              ) : (
                                <span style={{ color: '#cbd5e1' }}>—</span>
                              )}
                            </td>
                            <td style={{ padding: '8px 12px', color: '#6b7280', whiteSpace: 'nowrap' }}>
                              {a.updated_at ? new Date(a.updated_at).toLocaleString(_fmtLocale, {
                                day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                              }) : '—'}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
        )}

        {/* Per-user observability table — identity, Strava linkage, sync
            health chip, traces breakdown. Built for multi-user beta even
            though today there's one user. */}
        {adminUsers.length > 0 && (
          <div style={{ marginTop: 24 }} data-testid="admin-users-table">
            <Card title={t('admin.users.section')}>
              <p style={{ fontSize: 12, color: '#9ca3af', marginBottom: 12 }}>
                {t('admin.users.count', { n: adminUsers.length })}
              </p>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: '2px solid #e5e7eb', textAlign: 'left', color: '#6b7280', fontWeight: 600 }}>
                      <th style={{ padding: '8px 12px' }}>{t('admin.users.col.user')}</th>
                      <th style={{ padding: '8px 12px' }}>{t('admin.users.col.role')}</th>
                      <th style={{ padding: '8px 12px' }}>{t('admin.users.col.strava')}</th>
                      <th style={{ padding: '8px 12px' }}>{t('admin.users.col.sync')}</th>
                      <th style={{ padding: '8px 12px', textAlign: 'right' }}>{t('admin.users.col.traces')}</th>
                      <th style={{ padding: '8px 12px', textAlign: 'right' }}>{t('admin.users.col.distance')}</th>
                      <th style={{ padding: '8px 12px' }}>{t('admin.users.col.sports')}</th>
                      <th style={{ padding: '8px 12px' }}>{t('admin.users.col.lastActivity')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {adminUsers.map(u => {
                      // Sync-health chip: red reconnect/expired > amber failures > green OK.
                      const s = u.strava;
                      let chip: { label: string; bg: string; color: string } | null = null;
                      if (s) {
                        if (s.reconnect_required) chip = { label: t('admin.users.sync.reconnect'), bg: '#fef2f2', color: '#dc2626' };
                        else if (s.token_expired) chip = { label: t('admin.users.sync.tokenExpired'), bg: '#fef2f2', color: '#dc2626' };
                        else if (s.sync_failures > 0) chip = { label: t('admin.users.sync.failures', { n: s.sync_failures }), bg: '#fffbeb', color: '#d97706' };
                        else chip = { label: t('admin.users.sync.ok'), bg: '#f0fdf4', color: '#16a34a' };
                      }
                      const km = Math.round(u.activities.total_distance_m / 1000);
                      const sportsSummary = Object.entries(u.activities.by_sport)
                        .sort((a, b) => b[1].count - a[1].count)
                        .map(([sp, v]) => `${SPORT_LABELS[sp] || sp} ${v.count}`)
                        .join(' · ');
                      return (
                        <tr key={u.id} data-testid={`admin-user-${u.id}`} style={{ borderBottom: '1px solid #f3f4f6' }}>
                          <td style={{ padding: '8px 12px' }}>
                            <div style={{ fontWeight: 600, color: '#1f2937' }}>{u.username}</div>
                            <div style={{ fontSize: 11, color: '#9ca3af' }}>{u.email}</div>
                            <div style={{ fontSize: 11, color: '#cbd5e1' }}>
                              {u.created_at ? new Date(u.created_at).toLocaleDateString(_fmtLocale, { day: 'numeric', month: 'short', year: 'numeric' }) : '—'}
                            </div>
                          </td>
                          <td style={{ padding: '8px 12px' }}>
                            <span style={{
                              fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
                              background: u.is_admin ? '#eef2ff' : '#f3f4f6',
                              color: u.is_admin ? '#4338ca' : '#6b7280',
                            }}>
                              {u.is_admin ? t('admin.users.role.admin') : t('admin.users.role.user')}
                            </span>
                          </td>
                          <td style={{ padding: '8px 12px' }}>
                            {s?.athlete_id ? (
                              <a
                                href={`https://www.strava.com/athletes/${s.athlete_id}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                title={t('admin.users.strava.viewProfile')}
                                style={{ color: '#fc4c02', textDecoration: 'none', fontWeight: 600 }}
                              >
                                {s.athlete_name || s.athlete_id} ↗
                              </a>
                            ) : (
                              <span style={{ color: '#9ca3af' }}>{t('admin.users.strava.none')}</span>
                            )}
                          </td>
                          <td style={{ padding: '8px 12px' }}>
                            {chip ? (
                              <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4, background: chip.bg, color: chip.color }}>
                                {chip.label}
                              </span>
                            ) : (
                              <span style={{ color: '#cbd5e1' }}>—</span>
                            )}
                          </td>
                          <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600 }}>
                            {fmt(u.activities.total)}
                            {u.activities.skipped_import_count > 0 && (
                              <div style={{ fontSize: 10, color: '#d97706', fontWeight: 400 }}>
                                {t('admin.users.skipped', { n: u.activities.skipped_import_count })}
                              </div>
                            )}
                          </td>
                          <td style={{ padding: '8px 12px', textAlign: 'right', color: '#6b7280' }}>
                            {fmt(km)} km
                          </td>
                          <td style={{ padding: '8px 12px', color: '#6b7280', fontSize: 12 }}>
                            {sportsSummary || '—'}
                          </td>
                          <td style={{ padding: '8px 12px', color: '#6b7280' }}>
                            {u.activities.last_activity_date
                              ? new Date(u.activities.last_activity_date).toLocaleDateString(_fmtLocale, { day: 'numeric', month: 'short', year: 'numeric' })
                              : '—'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        )}

        {/* Strava Sync Status */}
        {syncStatus && (
          <div style={{ marginTop: 24 }}>
            <Card title={t('admin.card.stravaSync')}>
              <div style={{ display: 'flex', gap: 16, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
                <Stat label={t('admin.stat.connected')} value={syncStatus.total_connected} />
                <Stat label={t('admin.stat.delay30d')} value={syncStatus.stale_30d} color={syncStatus.stale_30d > 0 ? '#d97706' : undefined} />
                <Stat label={t('admin.stat.tokenExpired')} value={syncStatus.token_expired} color={syncStatus.token_expired > 0 ? '#dc2626' : undefined} />
                <Stat label={t('admin.disabledPlural')} value={syncStatus.disabled} color={syncStatus.disabled > 0 ? '#dc2626' : undefined} />
                <button
                  onClick={triggerResync}
                  disabled={resyncLoading}
                  style={{
                    padding: '8px 16px', borderRadius: 8, border: 'none',
                    background: '#2d6a4f', color: '#fff', cursor: 'pointer',
                    fontSize: 13, fontWeight: 600, opacity: resyncLoading ? 0.5 : 1,
                    marginLeft: 'auto',
                  }}
                >
                  {resyncLoading ? t('admin.launching') : t('admin.resyncNow')}
                </button>
              </div>
              {syncStatus.users.length > 0 && (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                    <thead>
                      <tr style={{ borderBottom: '2px solid #e5e7eb', textAlign: 'left' }}>
                        <th style={{ padding: '8px 12px', fontWeight: 600, color: '#6b7280' }}>{t('admin.col.athlete')}</th>
                        <th style={{ padding: '8px 12px', fontWeight: 600, color: '#6b7280' }}>{t('admin.col.lastSync')}</th>
                        <th style={{ padding: '8px 12px', fontWeight: 600, color: '#6b7280' }}>{t('admin.col.delay')}</th>
                        <th style={{ padding: '8px 12px', fontWeight: 600, color: '#6b7280' }}>{t('admin.col.failures')}</th>
                        <th style={{ padding: '8px 12px', fontWeight: 600, color: '#6b7280' }}>{t('admin.col.status')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {syncStatus.users.map((u, i) => (
                        <tr key={i} style={{ borderBottom: '1px solid #f3f4f6' }}>
                          <td style={{ padding: '8px 12px' }}>{u.athlete_name}</td>
                          <td style={{ padding: '8px 12px', color: '#6b7280' }}>
                            {u.last_synced_at
                              ? new Date(u.last_synced_at).toLocaleDateString(_fmtLocale, { day: 'numeric', month: 'short', year: 'numeric' })
                              : t('admin.never')}
                          </td>
                          <td style={{ padding: '8px 12px', color: (u.delay_days ?? 999) > 30 ? '#d97706' : '#6b7280' }}>
                            {u.delay_days !== null ? `${u.delay_days}j` : '—'}
                          </td>
                          <td style={{ padding: '8px 12px' }}>{u.sync_failures}</td>
                          <td style={{ padding: '8px 12px' }}>
                            {u.disabled ? (
                              <span style={{ background: '#fef2f2', color: '#dc2626', padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600 }}>{t('admin.disabled')}</span>
                            ) : u.token_expired ? (
                              <span style={{ background: '#fef2f2', color: '#dc2626', padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600 }}>{t('admin.tokenExpired')}</span>
                            ) : (
                              <span style={{ background: '#f0fdf4', color: '#16a34a', padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600 }}>OK</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
