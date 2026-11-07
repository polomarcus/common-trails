import { authHeaders } from './strava-archive-upload';

export interface ContributionStatus {
  contributed: boolean;
  count: number;
  last: string | null;
}

/**
 * GET /me/contribution-status for the /strava "already contributed" panel.
 *
 * Auth goes through `authHeaders()`: for a magic-link / cookie session
 * `getToken()` is the literal placeholder `'cookie-auth'`, and sending
 * `Authorization: Bearer cookie-auth` made the backend prefer the bogus bearer
 * over the valid cookie → 401 on every /strava load, so the panel never
 * showed. The cookie (`credentials: 'include'`) is the credential.
 *
 * Resolves `null` on any error (the panel is optional; the dropzone works
 * without it).
 */
export async function fetchContributionStatus(
  apiUrl: string,
  token: string | null,
): Promise<ContributionStatus | null> {
  try {
    const r = await fetch(`${apiUrl}/me/contribution-status`, {
      credentials: 'include',
      headers: authHeaders(token),
    });
    if (!r.ok) return null;
    const d = await r.json();
    if (!d) return null;
    return { contributed: d.contributed, count: d.count, last: d.last_contribution_at };
  } catch {
    return null;
  }
}
