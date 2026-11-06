/**
 * Post-login `?redirect=` sanitizer (open-redirect guard).
 *
 * Only a same-origin RELATIVE path is accepted: it must start with exactly one
 * "/" — "//evil.com" (protocol-relative), "/\evil.com" (browsers fold "\" into
 * "/"), any scheme ("https:", "javascript:") and control characters are
 * rejected, and the fallback is returned instead. Mirrored server-side in
 * backend/app/api/auth.py::_safe_redirect (the magic-link URL carries it).
 */
export const DEFAULT_POST_LOGIN_PATH = '/strava';

export function sanitizeRedirect(
  raw: string | null | undefined,
  fallback: string = DEFAULT_POST_LOGIN_PATH,
): string {
  if (typeof raw !== 'string') return fallback;
  const v = raw.trim();
  if (!v.startsWith('/') || v.startsWith('//')) return fallback;
  if (v.includes('\\')) return fallback;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(v)) return fallback;
  if (v.length > 512) return fallback;
  return v;
}

/**
 * Where to go after a successful magic-link login: the link's own
 * `?redirect=` if it is a safe same-origin path, else the server-provided
 * default (itself re-sanitized), else DEFAULT_POST_LOGIN_PATH.
 */
export function postLoginTarget(
  linkRedirect: string | null | undefined,
  serverRedirect?: string | null,
): string {
  return sanitizeRedirect(linkRedirect, sanitizeRedirect(serverRedirect));
}
