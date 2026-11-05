'use client';

import React, { useEffect, useState } from 'react';
import { MY_TRACES_COLOR } from '@/lib/constants';
import { getToken } from '@/lib/auth';
import { HEAT_RAMP_CSS } from '@/lib/community-heatmap-layers';
import { useI18n } from '@/lib/i18n';
import type { LayerToggles, WaymarkedToggles } from '@/hooks/useLayerToggles';

interface MapLegendProps {
  layers: LayerToggles;
  waymarked: WaymarkedToggles;
}

function MapLegend({ layers, waymarked }: MapLegendProps) {
  const { t } = useI18n();
  // "Mes sorties" is for a signed-in rider only. Auth is read AFTER mount, never
  // during render: /map is a static export built logged out, so reading
  // getToken() here would make a signed-in client's first render differ from
  // the HTML → hydration mismatch (React #418, see MapToolbar). null/false on
  // first paint = identical static render for everyone; the effect upgrades it.
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  useEffect(() => { setIsLoggedIn(getToken() !== null); }, []);
  const showMine = layers.myTraces && isLoggedIn;
  const show = layers.heatmap || layers.dfci || showMine || waymarked.hiking || waymarked.cycling || waymarked.mtb;
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
        <div data-testid="legend-community" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {/* Was a stale hand-copied BLUE gradient that never followed the
              plum→orange retune — the swatch now derives from the ramp SSOT. */}
          <div style={{ width: 40, height: 4, borderRadius: 2, background: HEAT_RAMP_CSS }} />
          <span>{t('map.legend.popularity')}</span>
        </div>
      )}
      {showMine && (
        // ONE entry for the rider's own traces ("Ma heatmap"): they are painted
        // in a single cyan on the map (MY_TRACES_COLOR), not per sport, so they
        // read against the community ramp above when both are superposed.
        <div data-testid="legend-my-activities" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{ width: 40, height: 4, borderRadius: 2, background: MY_TRACES_COLOR }} />
          <span>{t('map.legend.myActivities')}</span>
        </div>
      )}
      {layers.dfci && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{ width: 40, height: 0, borderTop: '2px dashed #cc0000' }} />
          <span>{t('map.dfciTracks')}</span>
        </div>
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
