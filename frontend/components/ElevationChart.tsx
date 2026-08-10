'use client';

import { slopeColor } from '@/lib/elevation';

interface ElevationChartProps {
  coords: number[][];
  width?: number;
  height?: number;
}

/**
 * Large, detailed SVG elevation profile.
 * - Y axis: elevation labels + horizontal grid lines
 * - X axis: distance ticks (auto interval)
 * - Slope-coloured line (3px) with fill area
 * - Full legend including >14% purple
 * Returns null when no 3D (altitude) data is present.
 */
export default function ElevationChart({ coords, width = 700, height = 200 }: ElevationChartProps) {
  const elevations = coords.map((c) => c[2] ?? null);
  if (elevations.every((e) => e === null || e === 0)) return null;
  const filled = elevations.filter((e): e is number => e !== null);
  if (filled.length < 2) return null;

  // Cumulative haversine distances (km) for X axis
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

  // Plot area insets — room for Y labels left, X labels bottom
  const pL = 44, pR = 8, pT = 10, pB = 22;

  const toX = (i: number) => pL + (dists[i] / totalDist) * (width - pL - pR);
  const toY = (e: number | null) =>
    e === null ? height - pB : pT + ((maxE - e) / rangeE) * (height - pT - pB);

  // Slope-coloured segments
  const segments: { x1: number; y1: number; x2: number; y2: number; color: string }[] = [];
  for (let i = 0; i < coords.length - 1; i++) {
    const e1 = elevations[i] ?? minE;
    const e2 = elevations[i + 1] ?? minE;
    const dx = dists[i + 1] - dists[i];
    const slope = dx > 0 ? ((e2 - e1) / (dx * 1000)) * 100 : 0;
    segments.push({ x1: toX(i), y1: toY(e1), x2: toX(i + 1), y2: toY(e2), color: slopeColor(slope) });
  }

  // Fill polygon
  const fillPts = coords.map((_, i) => `${toX(i)},${toY(elevations[i])}`).join(' ');
  const fillPolygon = `${toX(0)},${height - pB} ${fillPts} ${toX(coords.length - 1)},${height - pB}`;

  // Y-axis grid — 5 lines (min, 25%, 50%, 75%, max)
  const gridElevs = [0, 0.25, 0.5, 0.75, 1].map((f) => minE + f * rangeE);

  // X-axis distance ticks — pick a round interval so we get ~5–7 ticks
  const rawInterval = totalDist / 6;
  const candidates = [0.5, 1, 2, 5, 10, 20, 25, 50, 100];
  const tickInterval = candidates.find((v) => v >= rawInterval) ?? 100;
  const distTicks: number[] = [];
  for (let d = tickInterval; d < totalDist - tickInterval * 0.4; d += tickInterval) {
    distTicks.push(Math.round(d * 10) / 10);
  }

  return (
    <div>
      {/* Min / label / max row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#888', marginBottom: 4, paddingLeft: pL, paddingRight: pR }}>
        <span>{Math.round(minE)} m</span>
        <span style={{ fontWeight: 600, color: '#555' }}>Profil altimétrique</span>
        <span>{Math.round(maxE)} m</span>
      </div>

      <svg
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        style={{ display: 'block', borderRadius: 8, background: '#f8faf8', maxWidth: '100%', height: 'auto' }}
      >
        {/* Horizontal grid lines + Y labels */}
        {gridElevs.map((e, i) => {
          const y = toY(e);
          const isBase = i === 0;
          return (
            <g key={i}>
              <line
                x1={pL} y1={y} x2={width - pR} y2={y}
                stroke={isBase ? '#ccc' : '#e4e4e4'}
                strokeWidth={isBase ? 1 : 0.7}
                strokeDasharray={isBase ? undefined : '3,5'}
              />
              <text x={pL - 5} y={y + 3.5} fontSize={9} textAnchor="end" fill="#aaa">
                {Math.round(e)}
              </text>
            </g>
          );
        })}

        {/* Fill area */}
        <polygon points={fillPolygon} fill="#d1fae5" opacity={0.55} />

        {/* Slope-coloured line */}
        {segments.map((s, i) => (
          <line
            key={i}
            x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2}
            stroke={s.color}
            strokeWidth={3}
            strokeLinecap="round"
          />
        ))}

        {/* X baseline */}
        <line x1={pL} y1={height - pB} x2={width - pR} y2={height - pB} stroke="#ccc" strokeWidth={0.8} />

        {/* Distance ticks */}
        {distTicks.map((d) => {
          const x = pL + (d / totalDist) * (width - pL - pR);
          return (
            <g key={d}>
              <line x1={x} y1={height - pB} x2={x} y2={height - pB + 4} stroke="#bbb" strokeWidth={0.8} />
              <text x={x} y={height - 5} fontSize={9} textAnchor="middle" fill="#bbb">{d} km</text>
            </g>
          );
        })}

        {/* Total distance at far right */}
        <text x={width - pR} y={height - 5} fontSize={9} textAnchor="end" fill="#888" fontWeight="600">
          {totalDist.toFixed(1)} km
        </text>
      </svg>

      {/* Legend */}
      <div style={{ fontSize: 10, color: '#aaa', marginTop: 5, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <span><span style={{ color: '#4ade80' }}>■</span> &lt;2%</span>
        <span><span style={{ color: '#facc15' }}>■</span> 2–5%</span>
        <span><span style={{ color: '#f97316' }}>■</span> 5–9%</span>
        <span><span style={{ color: '#ef4444' }}>■</span> 9–14%</span>
        <span><span style={{ color: '#7c3aed' }}>■</span> &gt;14%</span>
      </div>
    </div>
  );
}
