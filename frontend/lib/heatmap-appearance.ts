/**
 * Réglages d'apparence des heatmaps de /map (menu « Calques ») — par
 * navigateur, mémorisés dans localStorage. Trois réglages, volontairement peu :
 *   - couleur de « Ma heatmap » (palette fixe, lisible sur les fonds de carte) ;
 *   - opacité de « Ma heatmap » ;
 *   - opacité de la heatmap commune.
 * Les opacités sont des MULTIPLICATEURS appliqués aux rampes par zoom (via
 * `withMult`), jamais des constantes : la lisibilité par zoom est conservée.
 *
 * Store module-level : les couches sont (re)peintes depuis plusieurs hooks,
 * qui lisent tous `getHeatmapAppearance()` ; les composants React s'abonnent
 * avec `useHeatmapAppearance()`. Rien n'est lu pendant le rendu serveur/statique
 * (valeurs par défaut au premier rendu, localStorage dans un effet).
 */
import { useEffect, useState } from 'react';
import { MY_TRACES_COLOR } from './constants';

export interface HeatmapAppearance {
  myColor: string;
  myOpacity: number;
  communityOpacity: number;
}

/** Couleurs proposées pour « Ma heatmap » — toutes loin de la rampe commune
 *  (prune → rose → orange). La première est la couleur par défaut. */
export const MY_COLOR_CHOICES: readonly string[] = [
  MY_TRACES_COLOR, '#06b6d4', '#15803d', '#111827', '#7c3aed',
];
export const OPACITY_MIN = 0.3;
export const OPACITY_MAX = 1;
export const COMMUNITY_OPACITY_MAX = 1.3;
export const STORAGE_KEY = 'cc_heatmap_appearance';

export const DEFAULT_APPEARANCE: HeatmapAppearance = {
  myColor: MY_TRACES_COLOR, myOpacity: 1, communityOpacity: 1,
};

const clamp = (v: unknown, min: number, max: number, dflt: number): number =>
  typeof v === 'number' && Number.isFinite(v) ? Math.min(max, Math.max(min, v)) : dflt;

/** Never trust what is in localStorage: unknown colour / out-of-range → default. */
export function sanitizeAppearance(raw: unknown): HeatmapAppearance {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  return {
    myColor: typeof r.myColor === 'string' && MY_COLOR_CHOICES.includes(r.myColor)
      ? r.myColor : DEFAULT_APPEARANCE.myColor,
    myOpacity: clamp(r.myOpacity, OPACITY_MIN, OPACITY_MAX, 1),
    communityOpacity: clamp(r.communityOpacity, OPACITY_MIN, COMMUNITY_OPACITY_MAX, 1),
  };
}

let current: HeatmapAppearance = DEFAULT_APPEARANCE;
let loaded = false;
const listeners = new Set<() => void>();

function load(): void {
  if (loaded || typeof window === 'undefined') return;
  loaded = true;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) current = sanitizeAppearance(JSON.parse(raw));
  } catch { /* private mode / junk → defaults */ }
}

export function getHeatmapAppearance(): HeatmapAppearance {
  load();
  return current;
}

export function setHeatmapAppearance(patch: Partial<HeatmapAppearance>): void {
  load();
  current = sanitizeAppearance({ ...current, ...patch });
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(current)); } catch { /* ignore */ }
  listeners.forEach((fn) => fn());
}

export function resetHeatmapAppearance(): void {
  setHeatmapAppearance(DEFAULT_APPEARANCE);
}

export function subscribeHeatmapAppearance(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/** React binding. First render = defaults (identical to the static HTML, no
 *  hydration mismatch); the stored values arrive in an effect. */
export function useHeatmapAppearance(): HeatmapAppearance {
  const [value, setValue] = useState<HeatmapAppearance>(DEFAULT_APPEARANCE);
  useEffect(() => {
    setValue(getHeatmapAppearance());
    return subscribeHeatmapAppearance(() => setValue(getHeatmapAppearance()));
  }, []);
  return value;
}

/** Test-only: forget the in-memory state (localStorage is the test's business). */
export function __resetHeatmapAppearanceForTests(): void {
  current = DEFAULT_APPEARANCE;
  loaded = false;
  listeners.clear();
}
