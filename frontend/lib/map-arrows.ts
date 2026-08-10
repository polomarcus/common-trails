/**
 * Shared direction arrow utilities for MapLibre maps.
 *
 * Provides a canvas-drawn chevron image and standard layout props
 * for symbol layers that show route direction along lines.
 */

/** Register a white chevron arrow image on the map. Call once after map.on('load'). */
export function registerDirectionArrow(map: unknown): void {
  const size = 16;
  const canvas = document.createElement('canvas');
  const dpr = window.devicePixelRatio || 1;
  canvas.width = size * dpr;
  canvas.height = size * dpr;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.scale(dpr, dpr);

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

  const data = new Uint8Array(ctx.getImageData(0, 0, size * dpr, size * dpr).data);
  // @ts-ignore — maplibre addImage
  map.addImage('direction-arrow', { width: size * dpr, height: size * dpr, data }, { pixelRatio: dpr });
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
