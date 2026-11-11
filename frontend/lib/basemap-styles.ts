/**
 * Map basemap style definitions.
 * Extracted from map/page.tsx for reuse and clarity.
 */

// IGN Plan v2 — free WMTS, no API key, topographic detail for France
export const IGN_PLANV2_STYLE: Record<string, unknown> = {
  version: 8,
  name: 'IGN Plan v2',
  glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
  sources: {
    'ign-planv2': {
      type: 'raster',
      tiles: [
        'https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&FORMAT=image/png&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}',
      ],
      tileSize: 256,
      attribution: '© IGN France',
      maxzoom: 18,
    },
  },
  layers: [{ id: 'ign-planv2-layer', type: 'raster', source: 'ign-planv2' }],
};

// CyclOSM — cycling-focused basemap with elevation contours
export const CYCLOSM_STYLE: Record<string, unknown> = {
  version: 8,
  name: 'CyclOSM',
  glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
  sources: {
    cyclosm: {
      type: 'raster',
      tiles: [
        'https://a.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png',
        'https://b.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png',
        'https://c.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png',
      ],
      tileSize: 256,
      attribution: '© CyclOSM | © OpenStreetMap contributors',
      maxzoom: 19,
    },
  },
  layers: [{ id: 'cyclosm-layer', type: 'raster', source: 'cyclosm' }],
};

// Esri World Imagery — high-resolution satellite/aerial basemap
export const SATELLITE_STYLE: Record<string, unknown> = {
  version: 8,
  name: 'Satellite',
  glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
  sources: {
    satellite: {
      type: 'raster',
      tiles: [
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      ],
      tileSize: 256,
      attribution: '© Esri, Maxar, Earthstar Geographics',
      maxzoom: 19,
    },
  },
  layers: [{ id: 'satellite-layer', type: 'raster', source: 'satellite' }],
};

// Hybrid — satellite base + IGN semi-transparent overlay
export const HYBRID_STYLE: Record<string, unknown> = {
  version: 8,
  name: 'Hybride IGN + Satellite',
  glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
  sources: {
    satellite: {
      type: 'raster',
      tiles: [
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      ],
      tileSize: 256,
      attribution: '© Esri, Maxar, Earthstar Geographics',
      maxzoom: 19,
    },
    'ign-planv2': {
      type: 'raster',
      tiles: [
        'https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&FORMAT=image/png&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}',
      ],
      tileSize: 256,
      attribution: '© IGN France',
      maxzoom: 18,
    },
  },
  layers: [
    { id: 'satellite-layer', type: 'raster', source: 'satellite' },
    { id: 'ign-planv2-layer', type: 'raster', source: 'ign-planv2', paint: { 'raster-opacity': 0.5 } },
  ],
};

// Thumbnail tiles: z=7, x=64, y=45 — centered on south France (Massif Central area)
const _THUMB_Z = 7, _THUMB_X = 64, _THUMB_Y = 45;

// The dark basemap's thumbnail used a Carto tile, which now answers with an
// "API KEY REQUIRED" image (seen in the picker, 2026-11-11). Inline swatch: no
// third-party request, never broken.
const DARK_THUMB = `data:image/svg+xml,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#1b1f27"/>'
  + '<path d="M-4 44 C14 30 26 52 40 34 S60 22 70 28" stroke="#3a4250" stroke-width="3" fill="none"/>'
  + '<path d="M8 -4 C14 16 30 18 28 36 S40 58 46 70" stroke="#2c323d" stroke-width="2" fill="none"/>'
  + '<path d="M-4 18 L24 24 L48 10 L70 16" stroke="#2c323d" stroke-width="1.5" fill="none"/></svg>',
)}`;

export const BASEMAPS: { key: string; style: string | Record<string, unknown>; label: string; title: string; thumb: string }[] = [
  // label + title are i18n KEYS (map.basemap.*) resolved via t() at the render
  // site (MapToolbar: `x.startsWith('map.') ? t(x) : x`). Keeps the picker
  // labels/tooltips locale-aware instead of hardcoded French.
  { key: 'bright', style: 'https://tiles.openfreemap.org/styles/bright', label: 'map.basemap.label.bright', title: 'map.basemap.title.bright', thumb: `https://a.tile.openstreetmap.org/${_THUMB_Z}/${_THUMB_X}/${_THUMB_Y}.png` },
  { key: 'dark', style: 'https://tiles.openfreemap.org/styles/dark', label: 'map.basemap.label.dark', title: 'map.basemap.title.dark', thumb: DARK_THUMB },
  { key: 'liberty', style: 'https://tiles.openfreemap.org/styles/liberty', label: 'map.basemap.label.liberty', title: 'map.basemap.title.liberty', thumb: `https://a.tile-cyclosm.openstreetmap.fr/cyclosm/${_THUMB_Z}/${_THUMB_X}/${_THUMB_Y}.png` },
  { key: 'cyclosm', style: CYCLOSM_STYLE, label: 'map.basemap.label.cyclosm', title: 'map.basemap.cyclOSM', thumb: `https://a.tile-cyclosm.openstreetmap.fr/cyclosm/${_THUMB_Z}/${_THUMB_X}/${_THUMB_Y}.png` },
  { key: 'ign', style: IGN_PLANV2_STYLE, label: 'map.basemap.label.ign', title: 'map.basemap.title.ign', thumb: `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&FORMAT=image/png&TILEMATRIXSET=PM&TILEMATRIX=${_THUMB_Z}&TILEROW=${_THUMB_Y}&TILECOL=${_THUMB_X}` },
  { key: 'hybrid', style: HYBRID_STYLE, label: 'map.basemap.label.hybrid', title: 'map.basemap.title.hybrid', thumb: `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${_THUMB_Z}/${_THUMB_Y}/${_THUMB_X}` },
  { key: 'satellite', style: SATELLITE_STYLE, label: 'map.basemap.label.satellite', title: 'map.basemap.title.satellite', thumb: `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${_THUMB_Z}/${_THUMB_Y}/${_THUMB_X}` },
];

// label is an i18n KEY (map.waymarked.*) resolved via t() at the render site.
export const WAYMARKED_LAYERS = [
  { key: 'hiking' as const, label: 'map.waymarked.hiking', tileType: 'hiking' },
  { key: 'cycling' as const, label: 'map.waymarked.cycling', tileType: 'cycling' },
  { key: 'mtb' as const, label: 'map.waymarked.mtb', tileType: 'mtb' },
] as const;

export const PROPOSAL_LOADING_MESSAGES = [
  'Analyse des traces de la communauté\u2009...',
  'On consulte les anciens du peloton\u2009...',
  'Recherche du meilleur gravier\u2009...',
  'On évite les autoroutes (promis)\u2009...',
  'map.elevCalc',
  'Détection des chemins secrets\u2009...',
  'Négociation avec les sangliers\u2009...',
  'map.gravelCheck',
  'Interrogation des pistes DFCI\u2009...',
  'On cherche la route la moins boueuse\u2009...',
  'Triangulation des cols perdus\u2009...',
  'On demande aux vaches le chemin\u2009...',
  'Repérage des fontaines sur le trajet\u2009...',
  'Optimisation du ratio asphalte/terre\u2009...',
  'Cartographie des single tracks\u2009...',
];
