'use client';

import React from 'react';
import { SPORT_COLORS, SPORT_DASH, SPORT_LABELS } from '@/lib/constants';
import { HEAT_RAMP_CSS } from '@/lib/community-heatmap-layers';
import { useI18n } from '@/lib/i18n';
import type { LayerToggles, WaymarkedToggles } from '@/hooks/useLayerToggles';

interface MapLegendProps {
  layers: LayerToggles;
  waymarked: WaymarkedToggles;
}

function MapLegend({ layers, waymarked }: MapLegendProps) {
  const { t } = useI18n();
  const show = layers.heatmap || layers.dfci || layers.myTraces || waymarked.hiking || waymarked.cycling || waymarked.mtb;
  if (!show) return null;

  return (
    <div style={{
      position: 'absolute', bottom: 48, left: 8, zIndex: 16,
      background: 'rgba(255,255,255,0.92)', borderRadius: 8,
      padding: '6px 10px', fontSize: 11, color: '#444',
      display: 'flex', flexDirection: 'column', gap: 4,
      backdropFilter: 'blur(4px)', border: '1px solid rgba(0,0,0,0.08)',
      maxHeight: 'calc(100vh - 120px)', overflowY: 'auto',
    }}>
      {layers.heatmap && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {/* Was a stale hand-copied BLUE gradient that never followed the
              plum→orange retune — the swatch now derives from the ramp SSOT. */}
          <div style={{ width: 40, height: 4, borderRadius: 2, background: HEAT_RAMP_CSS }} />
          <span>{t('map.legend.popularity')}</span>
        </div>
      )}
      {layers.dfci && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{ width: 40, height: 0, borderTop: '2px dashed #cc0000' }} />
          <span>{t('map.dfciTracks')}</span>
        </div>
      )}
      {layers.myTraces && (
        <>
          {(['road', 'gravel', 'mtb', 'running'] as const).map(s => (
            <div key={s} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <svg width="40" height="6" viewBox="0 0 40 6">
                {SPORT_DASH[s] ? (
                  <line x1="0" y1="3" x2="40" y2="3" stroke={SPORT_COLORS[s]} strokeWidth="3" strokeDasharray={SPORT_DASH[s]!.join(',')} strokeLinecap="round" />
                ) : (
                  <line x1="0" y1="3" x2="40" y2="3" stroke={SPORT_COLORS[s]} strokeWidth="3" strokeLinecap="round" />
                )}
              </svg>
              <span>{SPORT_LABELS[s]}</span>
            </div>
          ))}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <svg width="40" height="6" viewBox="0 0 40 6">
              <line x1="0" y1="3" x2="40" y2="3" stroke={SPORT_COLORS['offroad']} strokeWidth="3" strokeDasharray="8,4,2,4" strokeLinecap="round" />
            </svg>
            <span>{t('map.legend.offroad')}</span>
          </div>
        </>
      )}
      {waymarked.hiking && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{ width: 40, height: 3, borderRadius: 2, background: 'rgba(196, 60, 60, 0.55)' }} />
          <span>GR / PR</span>
        </div>
      )}
      {(waymarked.cycling || waymarked.mtb) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{ width: 40, height: 3, borderRadius: 2, background: 'rgba(34, 139, 34, 0.55)' }} />
          <span>{waymarked.cycling && waymarked.mtb ? t('map.layerEuroVeloMtb') : waymarked.cycling ? t('map.layerEuroVelo') : t('map.layerMtbTrails')}</span>
        </div>
      )}
    </div>
  );
}

export default React.memo(MapLegend);
