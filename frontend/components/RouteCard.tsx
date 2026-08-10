'use client';

import { memo } from 'react';
import Link from 'next/link';
import { SPORT_COLORS, SPORT_ICONS } from '@/lib/constants';
import { formatDist, fmtElev } from '@/lib/format';
import { surfaceSummary } from '@/lib/explain';
import { useT } from '@/lib/i18n';

export interface RouteCardRoute {
  id: string;
  name: string;
  sport: string;
  visibility: string;
  status?: string;
  description?: string;
  distance_m?: number;
  elevation_gain_m?: number;
  forked_from_id?: string;
  fork_count?: number;
  surface_pct?: string;
  last_accessed_at?: string;
  deleted_at?: string;
  center?: number[];
}

interface Props {
  route: RouteCardRoute;
  showActions: 'owner' | 'public' | 'compare';
  onClone?: (id: string) => void;
  cloningId?: string | null;
  onDelete?: (id: string) => void;
  onRestore?: (id: string) => void;
  onRename?: (id: string, name: string) => void;
  onToggleVisibility?: (id: string, visibility: string) => void;
  compareMode?: boolean;
  selectedForCompare?: boolean;
  onToggleCompare?: (id: string) => void;
}

export default memo(function RouteCard({
  route,
  showActions,
  onClone,
  cloningId,
  onDelete,
  onRestore,
  onRename,
  onToggleVisibility,
  compareMode,
  selectedForCompare,
  onToggleCompare,
}: Props) {
  const t = useT();
  const color = SPORT_COLORS[route.sport] ?? '#2d6a4f';
  const icon = SPORT_ICONS[route.sport] ?? '\u{1F5FA}\u{FE0F}';
  const elev = fmtElev(route.elevation_gain_m) as string;
  const isDeleted = !!route.deleted_at;

  return (
    <div style={{
      background: isDeleted ? '#fafafa' : '#fff',
      borderRadius: 14,
      overflow: 'hidden',
      boxShadow: '0 2px 10px rgba(0,0,0,0.07)',
      border: '1px solid #eef0ec',
      display: 'flex',
      flexDirection: 'column',
      opacity: isDeleted ? 0.6 : 1,
    }}>
      {/* Sport colour bar */}
      <div style={{ height: 4, background: color }} />

      <div style={{ padding: '16px 18px', flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          {compareMode && (
            <input
              type="checkbox"
              checked={selectedForCompare}
              onChange={() => onToggleCompare?.(route.id)}
              style={{ accentColor: '#1a4731', width: 18, height: 18, marginTop: 4, flexShrink: 0, cursor: 'pointer' }}
            />
          )}
          <span style={{ fontSize: 22, flexShrink: 0 }}>{icon}</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 800, fontSize: 15, color: '#1a1a1a', lineHeight: 1.3 }}>
              {route.name}
            </div>
            <div style={{ display: 'flex', gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
              <span style={{
                padding: '2px 8px', borderRadius: 10, fontSize: 11, fontWeight: 700,
                background: color, color: '#fff', textTransform: 'uppercase',
              }}>
                {icon} {route.sport}
              </span>
              {route.status === 'draft' && (
                <span style={{ fontSize: 11, color: '#e67e22', padding: '2px 6px', background: '#fff3e0', borderRadius: 8, fontWeight: 700 }}>
                  {t('routeCard.draft')}
                </span>
              )}
              {route.forked_from_id && (
                <span style={{ fontSize: 11, color: '#aaa', padding: '2px 6px', background: '#f5f5f5', borderRadius: 8 }}>
                  {t('routeCard.forkedFrom')}
                </span>
              )}
              {(route.fork_count ?? 0) > 0 && (
                <span style={{ fontSize: 11, color: '#666', padding: '2px 6px', background: '#f0f0f0', borderRadius: 8 }}>
                  {'\u{1F374}'} {t('routeCard.forkCount', { count: String(route.fork_count ?? 0), s: (route.fork_count ?? 0) > 1 ? 's' : '' })}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Description */}
        {route.description && (
          <p style={{ fontSize: 13, color: '#666', margin: 0, lineHeight: 1.5 }}>
            {route.description}
          </p>
        )}

        {/* Stats */}
        <div style={{ display: 'flex', gap: 16, fontSize: 13, color: '#555', marginTop: 'auto', paddingTop: 4, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 600 }}>{'\u{1F4CF}'} {formatDist(route.distance_m)}</span>
          {elev && <span style={{ fontWeight: 600 }}>{elev}</span>}
          {(() => {
            try {
              if (!route.surface_pct) return null;
              const pct = JSON.parse(route.surface_pct);
              const txt = surfaceSummary(pct);
              return txt ? <span style={{ fontWeight: 600, color: '#795548' }}>{txt}</span> : null;
            } catch { return null; }
          })()}
        </div>
      </div>

      {/* Actions */}
      <div style={{
        padding: '10px 18px',
        borderTop: '1px solid #f0f0ec',
        background: '#fafaf8',
        display: 'flex',
        gap: 8,
        flexWrap: 'wrap',
      }}>
        {isDeleted && onRestore ? (
          <button
            onClick={() => onRestore(route.id)}
            style={{
              padding: '7px 14px', background: '#e8f5e9', color: '#2e7d32',
              border: '1.5px solid #4caf50', borderRadius: 8, cursor: 'pointer',
              fontSize: 12, fontWeight: 700,
            }}
          >
            {t('common.restore')}
          </button>
        ) : (
          <>
            <Link
              href={`/map?route=${route.id}`}
              style={{
                padding: '7px 14px', background: '#1a4731', color: '#fff',
                borderRadius: 8, fontSize: 12, fontWeight: 700, textDecoration: 'none',
                display: 'inline-block',
              }}
            >
              {t('routeCard.openMap')}
            </Link>

            {showActions === 'public' && (
              <>
                <Link
                  href={`/map?route=${route.id}`}
                  style={{
                    padding: '7px 14px', background: '#fff', color: '#555',
                    border: '1.5px solid #ddd', borderRadius: 8, fontSize: 12, fontWeight: 600,
                    textDecoration: 'none', display: 'inline-block',
                  }}
                >
                  {t('common.details')}
                </Link>
                {onClone && (
                  <button
                    onClick={() => onClone(route.id)}
                    disabled={cloningId === route.id}
                    style={{
                      padding: '7px 14px', background: '#fff3e0', color: '#e67e22',
                      border: '1.5px solid #e67e22', borderRadius: 8, cursor: 'pointer',
                      fontSize: 12, fontWeight: 700, opacity: cloningId === route.id ? 0.6 : 1,
                    }}
                  >
                    {cloningId === route.id ? '\u2026' : t('routeCard.clone')}
                  </button>
                )}
              </>
            )}

            {showActions === 'owner' && (
              <>
                {onToggleVisibility && (
                  <button
                    onClick={() => onToggleVisibility(route.id, route.visibility === 'public' ? 'private' : 'public')}
                    style={{
                      padding: '7px 14px', background: '#fff', color: '#555',
                      border: '1.5px solid #ddd', borderRadius: 8, cursor: 'pointer',
                      fontSize: 12, fontWeight: 600,
                    }}
                  >
                    {route.visibility === 'public' ? t('routeCard.makePrivate') : t('routeCard.makePublic')}
                  </button>
                )}
                {onDelete && (
                  <button
                    onClick={() => onDelete(route.id)}
                    style={{
                      padding: '7px 14px', background: '#fff', color: '#c62828',
                      border: '1.5px solid #ef5350', borderRadius: 8, cursor: 'pointer',
                      fontSize: 12, fontWeight: 600,
                    }}
                  >
                    {t('common.delete')}
                  </button>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
});
