'use client';

import { useRef, useCallback, useImperativeHandle, forwardRef, useMemo } from 'react';
import type { ProfileData } from '@/lib/elevation-profile';
import { findNearestByDistance, slopeColor } from '@/lib/elevation-profile';
import { PROFILE_HOVER } from '@/lib/routing-style';
import { useT } from '@/lib/i18n';

export interface ElevationProfileProHandle {
  setHoverDist(dist_m: number | null): void;
}

interface Props {
  profileData: ProfileData;
  width: number;
  height: number;
  onHover?: (dist_m: number) => void;
  onHoverEnd?: () => void;
}

const PAD = 2;

const ElevationProfilePro = forwardRef<ElevationProfileProHandle, Props>(
  function ElevationProfilePro({ profileData, width, height, onHover, onHoverEnd }, ref) {
    const t = useT();
    const cursorGroupRef = useRef<SVGGElement>(null);
    const cursorLineRef = useRef<SVGLineElement>(null);
    const cursorDotRef = useRef<SVGCircleElement>(null);
    const tooltipRef = useRef<HTMLDivElement>(null);
    const rafRef = useRef(0);
    const lastIdxRef = useRef(-1);

    const { points, totalDist, minEle, maxEle } = profileData;
    const rangeEle = maxEle - minEle || 1;

    const toX = useCallback(
      (dist_m: number) => PAD + (dist_m / totalDist) * (width - 2 * PAD),
      [totalDist, width],
    );
    const toY = useCallback(
      (ele: number) => PAD + ((maxEle - ele) / rangeEle) * (height - 2 * PAD),
      [maxEle, rangeEle, height],
    );

    // Build fill polygon points string (memoized — O(n) string concat)
    const fillPolygon = useMemo(() => {
      const fillPts = points
        .map((p) => `${toX(p.dist_m)},${toY(p.ele_m)}`)
        .join(' ');
      return `${toX(0)},${height - PAD} ${fillPts} ${toX(totalDist)},${height - PAD}`;
    }, [points, toX, toY, height, totalDist]);

    // Build slope-colored segments (memoized — O(n) array build)
    const segments = useMemo(() => {
      const segs: { x1: number; y1: number; x2: number; y2: number; color: string }[] = [];
      for (let i = 0; i < points.length - 1; i++) {
        const p = points[i];
        const q = points[i + 1];
        segs.push({
          x1: toX(p.dist_m),
          y1: toY(p.ele_m),
          x2: toX(q.dist_m),
          y2: toY(q.ele_m),
          color: slopeColor(q.grade),
        });
      }
      return segs;
    }, [points, toX, toY]);

    /** Update cursor visuals directly (no setState). */
    const updateCursor = useCallback(
      (idx: number) => {
        const p = points[idx];
        if (!p) return;

        const x = toX(p.dist_m);
        const y = toY(p.ele_m);

        // Cursor line
        if (cursorLineRef.current) {
          cursorLineRef.current.setAttribute('x1', String(x));
          cursorLineRef.current.setAttribute('x2', String(x));
          cursorLineRef.current.setAttribute('y1', String(PAD));
          cursorLineRef.current.setAttribute('y2', String(height - PAD));
        }
        // Cursor dot
        if (cursorDotRef.current) {
          cursorDotRef.current.setAttribute('cx', String(x));
          cursorDotRef.current.setAttribute('cy', String(y));
        }
        // Show group
        if (cursorGroupRef.current) {
          cursorGroupRef.current.style.display = '';
        }

        // Tooltip
        if (tooltipRef.current) {
          const distKm = (p.dist_m / 1000).toFixed(1);
          const ele = Math.round(p.ele_m);
          const grade = p.grade.toFixed(1);
          const gradeColor = slopeColor(p.grade);

          let html = `<span style="font-weight:600">${distKm} km</span> · ${ele} m`;
          html += ` · <span style="color:${gradeColor};font-weight:600">${p.grade > 0 ? '+' : ''}${grade}%</span>`;
          if (p.surface) {
            html += ` · ${p.surface}`;
          }

          tooltipRef.current.innerHTML = html;
          tooltipRef.current.style.opacity = '1';

          // Position: above the SVG, centered on x, flip at edges
          const tipWidth = tooltipRef.current.offsetWidth || 120;
          let left = x - tipWidth / 2;
          if (left < 0) left = 0;
          if (left + tipWidth > width) left = width - tipWidth;
          tooltipRef.current.style.left = `${left}px`;
        }

        lastIdxRef.current = idx;
      },
      [points, toX, toY, height, width],
    );

    const hideCursor = useCallback(() => {
      if (cursorGroupRef.current) {
        cursorGroupRef.current.style.display = 'none';
      }
      if (tooltipRef.current) {
        tooltipRef.current.style.opacity = '0';
      }
      lastIdxRef.current = -1;
    }, []);

    /** Imperative handle: called by map hover (no onHover callback). */
    useImperativeHandle(ref, () => ({
      setHoverDist(dist_m: number | null) {
        if (dist_m == null) {
          hideCursor();
          return;
        }
        const idx = findNearestByDistance(profileData, dist_m);
        updateCursor(idx);
      },
    }), [profileData, hideCursor, updateCursor]);

    const handleMouseMove = useCallback(
      (e: React.MouseEvent<SVGRectElement>) => {
        cancelAnimationFrame(rafRef.current);
        // Capture rect & clientX synchronously — e.currentTarget is null after handler returns
        const rect = e.currentTarget.getBoundingClientRect();
        const clientX = e.clientX;
        rafRef.current = requestAnimationFrame(() => {
          const relX = clientX - rect.left;
          const dist_m = ((relX - PAD) / (width - 2 * PAD)) * totalDist;
          const clamped = Math.max(0, Math.min(totalDist, dist_m));
          const idx = findNearestByDistance(profileData, clamped);
          updateCursor(idx);
          onHover?.(clamped);
        });
      },
      [profileData, width, totalDist, updateCursor, onHover],
    );

    const handleMouseLeave = useCallback(() => {
      cancelAnimationFrame(rafRef.current);
      hideCursor();
      onHoverEnd?.();
    }, [hideCursor, onHoverEnd]);

    return (
      <div style={{ position: 'relative', maxWidth: width }}>
        {/* Header: min/max elevation + title */}
        <div
          style={{
            fontSize: 10,
            color: '#aaa',
            marginBottom: 3,
            display: 'flex',
            justifyContent: 'space-between',
          }}
        >
          <span>{Math.round(minEle)} m</span>
          <span style={{ color: '#555', fontWeight: 600 }}>{t('elevation.title')}</span>
          <span>{Math.round(maxEle)} m</span>
        </div>

        {/* Tooltip */}
        <div
          ref={tooltipRef}
          style={{
            position: 'absolute',
            top: 8,
            left: 0,
            pointerEvents: 'none',
            background: 'rgba(255,255,255,0.95)',
            border: '1px solid #ddd',
            borderRadius: 4,
            padding: '3px 8px',
            fontSize: 11,
            color: '#333',
            whiteSpace: 'nowrap',
            boxShadow: '0 2px 6px rgba(0,0,0,0.12)',
            opacity: 0,
            transition: 'opacity 80ms ease-in',
            zIndex: 10,
          }}
        />

        {/* SVG profile */}
        <svg
          width={width}
          height={height}
          style={{
            display: 'block',
            borderRadius: 6,
            overflow: 'visible',
            background: '#f8faf8',
            cursor: 'crosshair',
          }}
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

          {/* Cursor group — hidden by default */}
          <g ref={cursorGroupRef} style={{ display: 'none' }}>
            <line
              ref={cursorLineRef}
              x1={0}
              y1={PAD}
              x2={0}
              y2={height - PAD}
              stroke={PROFILE_HOVER}
              strokeWidth={1}
              strokeDasharray="3 2"
              opacity={0.7}
            />
            <circle
              ref={cursorDotRef}
              cx={0}
              cy={0}
              r={4}
              fill={PROFILE_HOVER}
              stroke="#fff"
              strokeWidth={1.5}
            />
          </g>

          {/* Transparent mouse capture overlay */}
          <rect
            x={0}
            y={0}
            width={width}
            height={height}
            fill="transparent"
            onMouseMove={handleMouseMove}
            onMouseLeave={handleMouseLeave}
          />
        </svg>

        {/* Legend */}
        <div style={{ fontSize: 10, color: '#aaa', marginTop: 3, display: 'flex', gap: 8 }}>
          <span style={{ color: '#4ade80' }}>&#9632;</span><span>&lt;2%</span>
          <span style={{ color: '#facc15' }}>&#9632;</span><span>2–5%</span>
          <span style={{ color: '#f97316' }}>&#9632;</span><span>5–9%</span>
          <span style={{ color: '#ef4444' }}>&#9632;</span><span>9–14%</span>
          <span style={{ color: '#7c3aed' }}>&#9632;</span><span>&gt;14%</span>
        </div>
      </div>
    );
  },
);

export default ElevationProfilePro;
