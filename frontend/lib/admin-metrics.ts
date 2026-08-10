/**
 * Pure logic for the admin heatmap-monitoring panel — freshness chips +
 * evolution sparklines. Kept dependency-free and React-free so it is unit
 * testable in isolation (see lib/__tests__/admin-metrics.test.ts). All
 * user-facing strings live in i18n; this module returns only structured
 * values (levels, numeric relative-time parts, SVG path data).
 */

export type ChipLevel = 'green' | 'amber' | 'red' | 'unknown';

export interface HeatmapMetricPoint {
  captured_at: string | null;
  heat_edges: number;
  agg_ways: number;
  activities: number;
  contributors: number;
  network_km: number;
  grid_fallback_pct: number | null;
  source: string;
}

export interface Freshness {
  last_activity_at: string | null;
  last_activity_date: string | null;
  heat_agg_updated_at: string | null;
  heat_edges_agg: number;
  last_resync_at: string | null;
  last_resync_status: string | null;
  strava_connected: number;
  strava_sync_failures: number;
  strava_reconnect_required: boolean;
  reconnect_threshold: number;
  webhook_subscription_present: boolean;
}

export interface HeatmapMetricsResponse {
  freshness: Freshness;
  series: HeatmapMetricPoint[];
  count: number;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Milliseconds elapsed since `iso`, or null when the timestamp is absent/invalid. */
export function ageMs(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.max(0, now - t);
}

/**
 * Green/amber/red chip for a freshness age. `amberMs`/`redMs` are the
 * thresholds; a null age (never happened) is `unknown`.
 */
export function freshnessLevel(age: number | null, amberMs: number, redMs: number): ChipLevel {
  if (age === null) return 'unknown';
  if (age >= redMs) return 'red';
  if (age >= amberMs) return 'amber';
  return 'green';
}

/** Default freshness thresholds per signal (ms). Exported so the UI + tests agree. */
export const FRESHNESS_THRESHOLDS = {
  // Heatmap rebuild: amber after a day, red after a week.
  rebuild: { amber: DAY, red: 7 * DAY },
  // Last activity ingested: amber after 3 days, red after 2 weeks.
  activity: { amber: 3 * DAY, red: 14 * DAY },
  // Last resync run: amber after 2 days, red after a week.
  resync: { amber: 2 * DAY, red: 7 * DAY },
} as const;

/**
 * Coarse relative-time bucket for i18n rendering. Returns a unit + value the
 * component turns into "il y a 3 h" / "3 h ago" via t(). Locale-agnostic.
 */
export function relativeParts(age: number | null): { unit: 'never' | 'now' | 'min' | 'h' | 'd'; value: number } {
  if (age === null) return { unit: 'never', value: 0 };
  const minutes = Math.floor(age / MINUTE);
  if (minutes < 1) return { unit: 'now', value: 0 };
  if (minutes < 60) return { unit: 'min', value: minutes };
  const hours = Math.floor(age / HOUR);
  if (hours < 24) return { unit: 'h', value: hours };
  return { unit: 'd', value: Math.floor(age / DAY) };
}

/** Resync-status chip: red on FAILED, amber on RUNNING/PENDING, green on COMPLETED. */
export function resyncLevel(status: string | null): ChipLevel {
  if (!status) return 'unknown';
  const s = status.toUpperCase();
  if (s === 'FAILED') return 'red';
  if (s === 'RUNNING' || s === 'PENDING') return 'amber';
  if (s === 'COMPLETED') return 'green';
  return 'unknown';
}

/**
 * Strava-sync chip: red once reconnect is required, amber on any accumulated
 * failure, green when connected + clean, unknown when nobody is connected.
 */
export function stravaLevel(
  f: Pick<Freshness, 'strava_reconnect_required' | 'strava_sync_failures' | 'strava_connected'>,
): ChipLevel {
  if (f.strava_connected <= 0) return 'unknown';
  if (f.strava_reconnect_required) return 'red';
  if (f.strava_sync_failures > 0) return 'amber';
  return 'green';
}

export interface Point { x: number; y: number }

/**
 * Normalise a numeric series into SVG coordinates inside a `width`×`height`
 * box (padding `pad`). y is flipped so larger values sit HIGHER on screen. A
 * flat series maps to a centred horizontal line; a single point sits centred.
 */
export function sparklinePoints(values: number[], width: number, height: number, pad = 2): Point[] {
  if (values.length === 0) return [];
  const innerW = Math.max(1, width - 2 * pad);
  const innerH = Math.max(1, height - 2 * pad);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min;
  const n = values.length;
  return values.map((v, i) => {
    const x = n === 1 ? pad + innerW / 2 : pad + (i / (n - 1)) * innerW;
    // No spread → centre the flat line rather than divide by zero.
    const norm = span === 0 ? 0.5 : (v - min) / span;
    const y = pad + (1 - norm) * innerH;
    return { x, y };
  });
}

/** SVG polyline `d` for a series. Empty string for an empty series. */
export function sparklinePath(values: number[], width: number, height: number, pad = 2): string {
  const pts = sparklinePoints(values, width, height, pad);
  if (pts.length === 0) return '';
  return pts
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(' ');
}
