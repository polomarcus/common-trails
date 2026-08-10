/** Canonical sport → colour mapping used across all pages. */
export const SPORT_COLORS: Record<string, string> = {
  road: '#2563eb',
  gravel: '#d97706',
  mtb: '#7c3aed',
  offroad: '#0891b2',
  running: '#dc2626',
};

/** Canonical sport → emoji icon mapping. */
export const SPORT_ICONS: Record<string, string> = {
  road: '🚴',
  gravel: '🪨',
  mtb: '⛰️',
  offroad: '🌿',
  running: '🏃',
};

/** Canonical sport → French label mapping. */
export const SPORT_LABELS: Record<string, string> = {
  road: 'Route',
  gravel: 'Gravel',
  mtb: 'VTT',
  offroad: 'Off-road',
  running: 'Course / Trail',
};

/** Combined sport metadata: label + icon + color. */
export const SPORT_META: Record<string, { label: string; icon: string; color: string }> = {
  road:    { label: 'Route',          icon: '🚴', color: '#2563eb' },
  gravel:  { label: 'Gravel',         icon: '🪨', color: '#d97706' },
  mtb:     { label: 'VTT',           icon: '⛰️', color: '#7c3aed' },
  offroad: { label: 'Off-road',       icon: '🌿', color: '#0891b2' },
  running: { label: 'Course / Trail', icon: '🏃', color: '#dc2626' },
};

/** Sport → MapLibre line-dasharray for CVD-accessible differentiation. undefined = solid. */
export const SPORT_DASH: Record<string, number[] | undefined> = {
  road: undefined,
  gravel: [8, 4],
  mtb: [2, 4],
  offroad: [8, 4, 2, 4],
  running: [4, 4],
};

/** All sport keys for iteration. */
export const SPORTS = ['road', 'gravel', 'mtb', 'offroad', 'running'] as const;

/** POI type → icon + French label mapping. */
export const POI_TYPES: Record<string, { icon: string; label: string }> = {
  water:     { icon: '💧', label: 'Eau' },
  food:      { icon: '🍽️', label: 'Ravitaillement' },
  camp:      { icon: '⛺', label: 'Bivouac' },
  shelter:   { icon: '🏠', label: 'Abri' },
  shop:      { icon: '🛒', label: 'Commerce' },
  train:     { icon: '🚂', label: 'Gare' },
  viewpoint: { icon: '👁', label: 'Point de vue' },
  custom:    { icon: '📌', label: 'Autre' },
};
