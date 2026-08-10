import { resolveApiUrl } from './env-url';

// Fail-LOUD env resolution (see env-url.ts): an UNSET NEXT_PUBLIC_API_URL in a
// prod build no longer silently bakes `localhost:8787` (which CORS-fails every
// call — the 2026-08-07 trap). An explicit `''` (same-origin) is still the
// intended prod value and is honoured as-is.
export const API_URL = resolveApiUrl(
  process.env.NEXT_PUBLIC_API_URL,
  process.env.NODE_ENV === 'production',
);

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = 'ApiError';
  }
}

/**
 * Centralized fetch wrapper.
 * - Uses httpOnly cookie for auth (credentials: 'include').
 * - Parses backend `detail` field on error responses.
 * - Does NOT set Content-Type for FormData bodies (browser handles boundary).
 */
export async function apiFetch<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);

  // Let the browser set Content-Type for FormData (multipart boundary)
  if (!(options.body instanceof FormData) && !headers.has('Content-Type') && options.body) {
    headers.set('Content-Type', 'application/json');
  }

  const resp = await fetch(`${API_URL}${path}`, { ...options, headers, credentials: 'include' });

  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      if (data.detail) detail = data.detail;
    } catch { /* use default */ }

    // Show non-intrusive toast on rate limit (429)
    if (resp.status === 429 && typeof document !== 'undefined') {
      _showRateLimitToast(detail);
    }

    throw new ApiError(resp.status, detail);
  }

  // 204 No Content
  if (resp.status === 204) return undefined as T;

  return resp.json() as Promise<T>;
}

/**
 * Same as apiFetch but returns null on error instead of throwing.
 * Useful for non-critical fetches (heatmap, stats).
 */
let _rateLimitToastTimer: ReturnType<typeof setTimeout> | null = null;

function _showRateLimitToast(message: string) {
  const id = 'cc-rate-limit-toast';
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement('div');
    el.id = id;
    Object.assign(el.style, {
      position: 'fixed', bottom: '24px', left: '50%', transform: 'translateX(-50%)',
      background: '#333', color: '#fff', padding: '10px 20px', borderRadius: '10px',
      fontSize: '13px', fontWeight: '600', zIndex: '9999',
      boxShadow: '0 4px 20px rgba(0,0,0,0.25)', transition: 'opacity 0.3s',
    });
    document.body.appendChild(el);
  }
  el.textContent = `⏳ ${message}`;
  el.style.opacity = '1';
  if (_rateLimitToastTimer) clearTimeout(_rateLimitToastTimer);
  _rateLimitToastTimer = setTimeout(() => { el!.style.opacity = '0'; }, 4000);
}

export async function apiFetchSafe<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T | null> {
  try {
    return await apiFetch<T>(path, options);
  } catch {
    return null;
  }
}
