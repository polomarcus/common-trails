/**
 * Profile system for CHEMINS COMMUNS.
 *
 * Profiles: road | gravel | mtb | offroad
 * - road    = route
 * - gravel  = gravel
 * - mtb     = VTT
 * - offroad = gravel + mtb (client-side aggregation)
 *
 * When the API supports "offroad" natively, remove the aggregation logic
 * in profileToSports() and pass "offroad" directly to the server.
 */

export type Profile = 'all' | 'road' | 'gravel' | 'mtb' | 'offroad' | 'running';

const PROFILE_STORAGE_KEY = 'cc_profile';

/** Display labels for each profile. */
export const PROFILE_LABELS: Record<Profile, string> = {
  all: 'Tout',
  road: 'Route',
  gravel: 'Gravel',
  mtb: 'VTT',
  offroad: 'Off-road',
  running: 'Running',
};

/** Emoji icons for each profile. */
export const PROFILE_ICONS: Record<Profile, string> = {
  all: '🌐',
  road: '🚴',
  gravel: '🪨',
  mtb: '⛰️',
  offroad: '🌿',
  running: '🏃',
};

/** Map a profile to the list of sports it covers.
 *
 * NOTE: When the API adds native "offroad" support, simplify this to:
 *   return [profile];
 */
export function profileToSports(profile: Profile): string[] {
  if (profile === 'all') return [];
  if (profile === 'offroad') {
    // Client-side aggregation: offroad = gravel + mtb
    // To migrate to server-side: replace with return ['offroad']
    return ['gravel', 'mtb'];
  }
  return [profile]; // road, gravel, mtb, running
}

/** Human-readable label for a profile. */
export function profileLabel(profile: Profile): string {
  return PROFILE_LABELS[profile] ?? profile;
}

/** Normalize an unknown string to a valid Profile, defaulting to 'all'. */
export function normalizeProfile(value: unknown): Profile {
  if (typeof value === 'string' && ['all', 'road', 'gravel', 'mtb', 'offroad', 'running'].includes(value)) {
    return value as Profile;
  }
  return 'all';
}

/** Read stored profile from localStorage. */
export function getStoredProfile(): Profile {
  if (typeof window === 'undefined') return 'road';
  const stored = localStorage.getItem(PROFILE_STORAGE_KEY);
  return normalizeProfile(stored);
}

/** Persist a profile to localStorage. */
export function setStoredProfile(profile: Profile): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(PROFILE_STORAGE_KEY, profile);
}

/**
 * Append profile-related query params to a URL.
 *
 * For single-sport APIs: adds ?sport=<sport>
 * For offroad (multi-sport): adds ?sports=gravel,mtb
 *
 * When the server supports a native offroad param:
 *   replace the offroad branch with: url.searchParams.set('profile', profile)
 */
export function withProfileQuery(baseUrl: string, profile: Profile): string {
  const url = new URL(baseUrl, 'http://localhost'); // base needed for relative URLs
  const sports = profileToSports(profile);

  if (sports.length === 1) {
    url.searchParams.set('sport', sports[0]);
  } else {
    // Multiple sports — use comma-separated for client aggregation
    url.searchParams.set('sports', sports.join(','));
  }

  // Return relative if input was relative
  if (!baseUrl.startsWith('http')) {
    return url.pathname + url.search;
  }
  return url.toString();
}

/** Get profile from URL query string (e.g., ?profile=gravel). */
export function getProfileFromUrl(): Profile {
  if (typeof window === 'undefined') return 'road';
  const params = new URLSearchParams(window.location.search);
  return normalizeProfile(params.get('profile'));
}
