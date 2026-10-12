/**
 * Shared direction arrow utilities for MapLibre maps.
 *
 * Provides a canvas-drawn chevron image and standard layout props
 * for symbol layers that show route direction along lines.
 */

/**
 * Register a white chevron arrow image on the map. Call once after map.on('load').
 *
 * Returns `true` when the sprite is available, `false` when it could not be
 * registered — NEVER throws. The arrow is decorative (direction chevrons on
 * one-way trails); MapLibre renders a symbol layer whose `icon-image` is missing
 * by simply skipping the icon, so a failure here must cost the chevrons and
 * nothing else.
 *
 * ⚠️ Two calls below can genuinely throw in the wild (verified against the
 * pinned maplibre-gl 4.3.2):
 *   - `map.addImage()` → `RangeError: mismatched image size` — it wraps the
 *     bytes in `new RGBAImage({width, height}, …)`, which throws synchronously
 *     when `width * height * 4 !== data.length`. THE prod bug of 2026-10-12,
 *     see the dimension comment below;
 *   - `ctx.getImageData()` → SecurityError on hardened browsers/extensions that
 *     block canvas reads (anti-fingerprinting).
 * Unguarded, either one aborted the ENTIRE /map layer setup (this was the first
 * statement of addMapSourcesAndLayers) and left a bare basemap. See the
 * `safeBlock` doc in lib/init-map-layers.ts.
 */
export function registerDirectionArrow(map: unknown): boolean {
  try {
    return drawAndRegisterArrow(map);
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn('[map-arrows] direction-arrow sprite unavailable (chevrons skipped):', e);
    return false;
  }
}

function drawAndRegisterArrow(map: unknown): boolean {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const m = map as any;
  // Already registered (a second setup pass on the same map). `Style.addImage`
  // does NOT throw on a duplicate id in maplibre 4.3.2 — it fires an ErrorEvent
  // and silently keeps the old image — so without this guard a second pass would
  // redraw the canvas for nothing and emit a spurious map `error` event.
  if (typeof m?.hasImage === 'function' && m.hasImage('direction-arrow')) return true;
  const size = 16;
  const canvas = document.createElement('canvas');
  // ⭐ INTEGER pixel dimensions. `devicePixelRatio` is routinely FRACTIONAL —
  // Firefox reports 1.1 / 2.2 / 2.4 at browser zoom, and OS display scaling does
  // the same (1.25, 1.5…). `size * dpr` is then fractional: the canvas TRUNCATES
  // it (16 * 1.1 = 17.6 → a 17 px bitmap), so `getImageData` returns 17*17*4
  // bytes while `addImage` was told the image is 17.6 px wide. MapLibre checks
  // `width * height * 4 === data.length` and THROWS "RGBAImage size mismatch" —
  // which used to abort the entire /map layer setup (blank map, prod 2026-10-12,
  // reproduced headless at deviceScaleFactor 1.1). Rounding once, and deriving
  // BOTH the bitmap and the declared size from that single integer, keeps the
  // three numbers consistent at any dpr.
  const dpr = window.devicePixelRatio || 1;
  const px = Math.max(1, Math.round(size * dpr));
  canvas.width = px;
  canvas.height = px;
  const ctx = canvas.getContext('2d');
  if (!ctx) return false;
  // Draw in the 16x16 design space; `px / size` is the ACTUAL scale (not `dpr`).
  ctx.scale(px / size, px / size);

  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  // Chevron ">" path, drawn TWICE: a wide dark halo first, then a white
  // chevron on top. The halo gives the arrow contrast over the warm orange
  // heat lines it sits on (a bare white chevron washed out over bright ways).
  const stroke = () => {
    ctx.beginPath();
    ctx.moveTo(4, 3);
    ctx.lineTo(12, 8);
    ctx.lineTo(4, 13);
    ctx.stroke();
  };
  ctx.strokeStyle = 'rgba(20,10,25,0.65)';
  ctx.lineWidth = 4.5;
  stroke();
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 2.5;
  stroke();

  const data = new Uint8Array(ctx.getImageData(0, 0, px, px).data);
  m.addImage('direction-arrow', { width: px, height: px, data }, { pixelRatio: px / size });
  return true;
}

/** Standard layout properties for direction arrow symbol layers. */
export function directionArrowLayout(overrides?: Record<string, unknown>): Record<string, unknown> {
  return {
    'symbol-placement': 'line',
    'symbol-spacing': [
      'interpolate', ['linear'], ['zoom'],
      10, 200,
      13, 100,
      16, 50,
    ],
    'icon-image': 'direction-arrow',
    'icon-rotation-alignment': 'map',
    'icon-allow-overlap': true,
    'icon-ignore-placement': true,
    ...overrides,
  };
}
