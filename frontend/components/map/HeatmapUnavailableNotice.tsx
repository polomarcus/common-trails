'use client';

import React, { useEffect, useState } from 'react';
import { useT } from '@/lib/i18n';

interface HeatmapUnavailableNoticeProps {
  /** True when the community heatmap source failed to load (see lib/heatmap-availability). */
  unavailable: boolean;
}

/**
 * Small, non-blocking, dismissible notice shown on /map when the community
 * heatmap tiles cannot be loaded. Without it the page showed the basemap alone
 * with the "Popularité" legend still on, and nothing said why.
 */
export default function HeatmapUnavailableNotice({ unavailable }: HeatmapUnavailableNoticeProps) {
  const t = useT();
  const [dismissed, setDismissed] = useState(false);

  // A new outage after recovery shows the notice again.
  useEffect(() => {
    if (!unavailable) setDismissed(false);
  }, [unavailable]);

  if (!unavailable || dismissed) return null;

  return (
    <div
      data-testid="heatmap-unavailable"
      role="status"
      style={{
        // Top-centre: clear of the empty-state card (top-left) and of
        // MapLibre's controls + the "capped" note (top-right).
        position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)',
        zIndex: 26, background: '#fff7ed', border: '1px solid #fed7aa', borderRadius: 10,
        boxShadow: '0 4px 16px rgba(0,0,0,0.12)', padding: '8px 10px 8px 12px',
        display: 'flex', alignItems: 'center', gap: 8, maxWidth: 'calc(100% - 140px)',
        fontSize: 13, color: '#7c2d12',
      }}
    >
      <span>{t('map.heatmapUnavailable')}</span>
      <button
        type="button"
        data-testid="heatmap-unavailable-dismiss"
        onClick={() => setDismissed(true)}
        aria-label={t('map.empty.dismiss')}
        title={t('map.empty.dismiss')}
        style={{
          background: 'none', border: 'none', cursor: 'pointer',
          fontSize: 16, color: '#9a3412', padding: '0 2px', lineHeight: 1,
        }}
      >
        ×
      </button>
    </div>
  );
}
