/**
 * Routing Visual Language — Design Tokens
 *
 * Double-casing + core + skin structure (cartographic standard):
 *   1. Outer casing (dark)  — contrast anchor, readable on ANY basemap
 *   2. Inner casing (white) — isolates core from outer casing
 *   3. Core line            — brand color (saturated, never < 3.5px)
 *   4. Surface skin         — earth-tone overlay when surface data available
 *
 * Two rendering modes:
 *   NORMAL        — balanced visibility/aesthetics
 *   HIGH_CONTRAST — maximum readability (wider casings, stronger opacity)
 *
 * Visual Grammar:
 * - SOLID = committed/stable state
 * - DASHED = provisional/uncertain (drag preview, fork parent)
 * - Hierarchy: waypoints > hover > surface > draft > final > parent > heatmap > basemap
 */

// ── Outer casing (dark — contrast anchor on any basemap) ────────────
export const OUTER_CASING_COLOR = '#0b1220';

// ── Inner casing (white — isolation ring) ───────────────────────────
export const INNER_CASING_COLOR = '#ffffff';

// ── Normal mode values ──────────────────────────────────────────────
export const NORMAL = {
  outerCasingWidth: 12,
  outerCasingOpacity: 0.38,
  outerCasingBlur: 1.5,
  innerCasingWidth: 8,
  innerCasingOpacity: 0.75,
  innerCasingBlur: 0.5,
  coreWidth: 4,
  surfaceWidth: 3.5,
  surfaceOpacity: 0.85,
  // Viewed route (read-only)
  viewOuterCasingWidth: 11,
  viewInnerCasingWidth: 7,
  viewCoreWidth: 3.5,
  // Profile hover
  hoverOuterCasingWidth: 10,
  hoverInnerCasingWidth: 7,
  hoverCoreWidth: 3,
} as const;

// ── High contrast mode values ───────────────────────────────────────
export const HIGH_CONTRAST = {
  outerCasingWidth: 14,
  outerCasingOpacity: 0.5,
  outerCasingBlur: 1,
  innerCasingWidth: 10,
  innerCasingOpacity: 0.85,
  innerCasingBlur: 0,
  coreWidth: 4.5,
  surfaceWidth: 4,
  surfaceOpacity: 0.9,
  // Viewed route (read-only)
  viewOuterCasingWidth: 13,
  viewInnerCasingWidth: 9,
  viewCoreWidth: 4,
  // Profile hover
  hoverOuterCasingWidth: 12,
  hoverInnerCasingWidth: 8,
  hoverCoreWidth: 3.5,
} as const;

// ── Saved route (read-only view mode) ───────────────────────────────
export const ROUTE_FINAL = '#1e3a8a';          // deep alpine blue for saved routes
export const ROUTE_PARENT = '#f59e0b';         // amber for parent fork reference

// ── Profile hover ───────────────────────────────────────────────────
export const PROFILE_HOVER = '#e11d48';
export const PROFILE_HOVER_DOT = '#e11d48';
export const PROFILE_HOVER_HALO = 'rgba(225,29,72,0.22)';
export const PROFILE_HOVER_OPACITY = 0.8;

// ── Surface colors (earth tones — high contrast on any basemap) ─────
export const SURFACE_ASPHALT = '#111827';      // charcoal
export const SURFACE_GRAVEL = '#c0841a';       // earth ochre
export const SURFACE_DIRT = '#92400e';          // burnt earth
export const SURFACE_ROCK = '#6b7280';          // cool grey stone
export const SURFACE_UNKNOWN = 'transparent';   // core line shows through

// ── Heatmap ─────────────────────────────────────────────────────────
export const HEATMAP_DIM_OPACITY = 0.35;
export const HEATMAP_DEFAULT_OPACITY = 1.0;

// ── Hero map (homepage) ─────────────────────────────────────────────
export const HERO_MAP_ZOOM = 10;
export const HERO_MAP_CENTER: [number, number] = [3.87, 43.61]; // Montpellier

// ── DFCI trails (south France fire-prevention tracks) ───────────────
export const DFCI_COLOR = '#cc0000';              // DFCI red
export const DFCI_COLOR_WHITE = '#ffffff';         // DFCI white (casing)
export const DFCI_LINE_WIDTH = 3;
export const DFCI_DASH_ARRAY = [4, 3];

// ── Route confidence levels (per-edge community evidence) ──────────
// 3-tier system: high (verified), medium (few riders), low (unverified)
export const CONFIDENCE_HIGH_COLOR = '#16a34a';    // green-600 — multiple riders or official trail
export const CONFIDENCE_MEDIUM_COLOR = '#f59e0b';  // amber-500 — 1-2 riders
export const CONFIDENCE_LOW_COLOR = '#94a3b8';     // slate-400 — no evidence (OSM only)

export const CONFIDENCE_HIGH_OPACITY = 0.9;
export const CONFIDENCE_MEDIUM_OPACITY = 0.9;
export const CONFIDENCE_LOW_OPACITY = 0.75;

export const CONFIDENCE_LINE_WIDTH = 3.5;
