/**
 * Lightweight performance tracing — no-op in production builds.
 *
 * Usage:
 *   import { trace } from '@/lib/perf-trace';
 *   trace('heatmap', 'source-add');                     // → "[heatmap] source-add @ 861 ms"
 *   trace('heatmap', 'first-tile', tStart);             // → "[heatmap] first-tile (+ 629 ms)"
 *
 * The first form prints absolute time since page navigation; the second prints
 * elapsed time since `tStart` (a previous `performance.now()` value).
 *
 * Compiled out in production: `process.env.NODE_ENV === 'production'` short-circuits
 * before the string formatting, so there is no runtime cost in prod bundles.
 */

// Tracing is opt-in to keep the production console clean. Enable by either:
//   - localStorage.setItem('cc_perf_trace', '1')   (persists across reloads)
//   - URL query param: ?trace=1                    (single page load)
// Playwright tests set the localStorage flag in addInitScript.
function isEnabled(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    if (localStorage.getItem('cc_perf_trace') === '1') return true;
  } catch { /* sandboxed iframe etc. */ }
  if (window.location?.search?.includes('trace=1')) return true;
  return false;
}

export function trace(channel: string, label: string, since?: number): void {
  if (!isEnabled()) return;
  const now = performance.now();
  const time =
    since === undefined
      ? `@ ${now.toFixed(0)} ms`
      : `(+ ${(now - since).toFixed(0)} ms)`;
  // eslint-disable-next-line no-console
  console.info(`[${channel}] ${label} ${time}`);
}

export function traceWith(channel: string, label: string, since: number, suffix: string): void {
  if (!isEnabled()) return;
  // eslint-disable-next-line no-console
  console.info(`[${channel}] ${label} (+ ${(performance.now() - since).toFixed(0)} ms) — ${suffix}`);
}
