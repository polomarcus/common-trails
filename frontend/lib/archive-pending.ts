/**
 * "An archive was just accepted and is still being processed" marker.
 *
 * The drain runs in a background job (minutes to ~1 h for a big export), so
 * right after a successful upload /map still has 0 activities. Without this
 * marker the empty-state card told the rider to « Importez vos traces » —
 * which they had just done. The dropzone stamps the time; for
 * ARCHIVE_PENDING_WINDOW_MS afterwards /map shows a "processing" variant.
 *
 * localStorage only (per-browser convenience, not state that must persist),
 * every access wrapped in try/catch (private mode / blocked storage → no-op),
 * and callers read it in an effect, never during render (static export, #418).
 */
export const ARCHIVE_PENDING_KEY = 'cc_archive_pending_at';
export const ARCHIVE_PENDING_WINDOW_MS = 2 * 60 * 60 * 1000;

export function markArchivePending(now: number = Date.now()): void {
  try { localStorage.setItem(ARCHIVE_PENDING_KEY, String(now)); } catch { /* storage unavailable */ }
}

export function isArchivePending(now: number = Date.now()): boolean {
  try {
    const at = Number(localStorage.getItem(ARCHIVE_PENDING_KEY));
    return Number.isFinite(at) && at > 0 && now - at >= 0 && now - at < ARCHIVE_PENDING_WINDOW_MS;
  } catch {
    return false;
  }
}
