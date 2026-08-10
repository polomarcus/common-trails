'use client';

import { memo } from 'react';
import { slopeColor } from '@/lib/elevation';
import { useT } from '@/lib/i18n';

interface ElevationProfileProps {
  coords: number[][];
  width?: number;
  height?: number;
}

/**
 * SVG elevation profile with slope-based colour gradient.
 * Returns null when no 3D (altitude) data is present in coords.
 */
export default memo(function ElevationProfile({ coords, width = 280, height = 72 }: ElevationProfileProps) {
  const t = useT();
  const elevations = coords.map((c) => c[2] ?? null);
  if (elevations.every((e) => e === null || e === 0)) return null;
  const filled = elevations.filter((e): e is number => e !== null);
  if (filled.length < 2) return null;

  // Cumulative haversine distances for X axis (km)
  const dists: number[] = [0];
  for (let i = 1; i < coords.length; i++) {
    const [lon1, lat1] = coords[i - 1];
    const [lon2, lat2] = coords[i];
    const dLat = ((lat2 - lat1) * Math.PI) / 180;
    const dLon = ((lon2 - lon1) * Math.PI) / 180;
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
    dists.push(dists[i - 1] + 6371 * 2 * Math.asin(Math.sqrt(a)));
  }
  const totalDist = dists[dists.length - 1] || 1;

  const minE = Math.min(...filled);
  const maxE = Math.max(...filled);
  const rangeE = maxE - minE || 1;

  const pad = 2;
  const toX = (i: number) => pad + (dists[i] / totalDist) * (width - 2 * pad);
  const toY = (e: number | null) =>
    e === null ? height - pad : pad + ((maxE - e) / rangeE) * (height - 2 * pad);

  // Slope-coloured line segments
  const segments: { x1: number; y1: number; x2: number; y2: number; color: string }[] = [];
  for (let i = 0; i < coords.length - 1; i++) {
    const e1 = elevations[i] ?? minE;
    const e2 = elevations[i + 1] ?? minE;
    const dx = dists[i + 1] - dists[i]; // km
    const slope = dx > 0 ? ((e2 - e1) / (dx * 1000)) * 100 : 0;
    segments.push({ x1: toX(i), y1: toY(e1), x2: toX(i + 1), y2: toY(e2), color: slopeColor(slope) });
  }

  // Fill area polygon
  const fillPts = coords.map((_, i) => `${toX(i)},${toY(elevations[i])}`).join(' ');
  const fillPolygon = `${toX(0)},${height - pad} ${fillPts} ${toX(coords.length - 1)},${height - pad}`;

  return (
    <div>
      <div
        style={{
          fontSize: 10,
          color: '#aaa',
          marginBottom: 3,
          display: 'flex',
          justifyContent: 'space-between',
        }}
      >
        <span>{Math.round(minE)} m</span>
        <span style={{ color: '#555', fontWeight: 600 }}>{t('elevation.title')}</span>
        <span>{Math.round(maxE)} m</span>
      </div>
      <svg
        width={width}
        height={height}
        style={{ display: 'block', borderRadius: 6, overflow: 'hidden', background: '#f8faf8' }}
      >
        <polygon points={fillPolygon} fill="#d1fae5" opacity={0.6} />
        {segments.map((s, i) => (
          <line
            key={i}
            x1={s.x1}
            y1={s.y1}
            x2={s.x2}
            y2={s.y2}
            stroke={s.color}
            strokeWidth={2}
            strokeLinecap="round"
          />
        ))}
      </svg>
      <div style={{ fontSize: 10, color: '#aaa', marginTop: 3, display: 'flex', gap: 8 }}>
        <span style={{ color: '#4ade80' }}>■</span><span>&lt;2%</span>
        <span style={{ color: '#facc15' }}>■</span><span>2–5%</span>
        <span style={{ color: '#f97316' }}>■</span><span>5–9%</span>
        <span style={{ color: '#ef4444' }}>■</span><span>&gt;9%</span>
      </div>
    </div>
  );
});
