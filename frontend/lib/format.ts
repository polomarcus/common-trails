/** Format metres as "X.Y km" or "X m" for short distances. */
export function formatDist(m?: number): string {
  if (!m) return '–';
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`;
}

/** Format metres as "X.Y km", returns '–' for falsy. */
export function fmtKm(m?: number): string {
  if (!m) return '–';
  return `${(m / 1000).toFixed(1)} km`;
}

/** Format elevation as "↑X m", returns null for falsy. */
export function fmtElev(m?: number): string | null {
  if (!m) return null;
  return `↑${Math.round(m)} m`;
}

/** Format elevation with locale separator, returns '—' for falsy. */
export function fmtElevFull(m?: number, locale: string = 'fr-FR'): string {
  if (!m) return '—';
  return `${Math.round(m).toLocaleString(locale)} m`;
}

/** Format ISO date string to locale. */
export function fmtDate(iso?: string, locale: string = 'fr-FR'): string {
  if (!iso) return '–';
  return new Date(iso).toLocaleDateString(locale, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

/**
 * Average speed in km/h by sport profile — used for ETA estimates during route planning.
 * Conservative averages including stops, climbs, and surface penalties.
 */
const SPORT_SPEEDS_KMH: Record<string, number> = {
  road: 22,      // road cycling on tarmac
  gravel: 16,    // mixed surface, moderate climbs
  mtb: 12,       // MTB on trails, includes technical sections
  offroad: 14,   // generic off-road (gravel + MTB blend)
  running: 9,    // trail running
  hiking: 4,     // walking pace on trails
};

/** Format minutes as "Xh Ym" or "Z min" for short durations. */
export function fmtDuration(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes - h * 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

/** Estimate duration in minutes from distance (km) and sport profile. */
export function estimateDurationMin(distanceKm: number, sport: string): number {
  const kmh = SPORT_SPEEDS_KMH[sport] ?? SPORT_SPEEDS_KMH.gravel;
  return (distanceKm / kmh) * 60;
}

/** Format ISO date string to long locale. */
export function fmtDateLong(iso?: string, locale: string = 'fr-FR'): string {
  if (!iso) return '–';
  return new Date(iso).toLocaleDateString(locale, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
}
