/**
 * Auth state management — httpOnly cookie based.
 *
 * The JWT token is stored in an httpOnly cookie (set by the backend).
 * We store user_id in localStorage for UI state only (not sensitive).
 * All API requests include credentials automatically via cookies.
 */

export function isAuthenticated(): boolean {
  if (typeof window === 'undefined') return false;
  return !!localStorage.getItem('user_id');
}

export function getCurrentUserId(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('user_id');
}

export function setAuthState(userId: string): void {
  localStorage.setItem('user_id', userId);
}

export function clearAuthState(): void {
  localStorage.removeItem('user_id');
  localStorage.removeItem('strava_name');
}

/**
 * Backward-compat helper — returns a truthy string when authenticated.
 * The actual JWT is in an httpOnly cookie, not accessible from JS.
 */
export function getToken(): string | null {
  return isAuthenticated() ? 'cookie-auth' : null;
}
