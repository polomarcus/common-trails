'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useT } from '@/lib/i18n';

/**
 * Banner shown on /map when a Strava import is running in the background.
 * Reads from localStorage (set by /strava page during polling).
 * Auto-hides when import completes or data is stale (>10 min).
 */
export default function StravaImportBanner() {
  const [info, setInfo] = useState<{
    phase: string | null;
    imported: number;
    total: number;
    gpsUpgraded: number;
    gpsTotal: number;
  } | null>(null);

  useEffect(() => {
    const check = () => {
      try {
        const raw = localStorage.getItem('strava_import_active');
        if (!raw) { setInfo(null); return; }
        const data = JSON.parse(raw);
        // Hide if data is stale (>10 min without update from strava page)
        if (Date.now() - data.ts > 10 * 60 * 1000) {
          localStorage.removeItem('strava_import_active');
          setInfo(null);
          return;
        }
        setInfo(data);
      } catch { setInfo(null); }
    };

    check();
    const interval = setInterval(check, 5000);
    return () => clearInterval(interval);
  }, []);

  const t = useT();

  if (!info) return null;

  const phase = info.phase;
  let label = t('importBanner.strava');
  if (phase === 'gps_upgrade') {
    label = t('importBanner.gpsUpgrade', { upgraded: info.gpsUpgraded, total: info.gpsTotal });
  } else if (phase === 'ingesting') {
    label = t('importBanner.importing', { imported: info.imported, total: info.total });
  } else if (phase === 'photo_import') {
    label = t('importBanner.photos');
  }

  return (
    <Link
      href="/strava"
      style={{
        position: 'fixed',
        top: 8,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '6px 14px',
        background: 'rgba(252,76,2,0.9)',
        color: '#fff',
        borderRadius: 20,
        fontSize: 12,
        fontWeight: 600,
        textDecoration: 'none',
        boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
        backdropFilter: 'blur(8px)',
      }}
    >
      <div style={{
        width: 8, height: 8, borderRadius: '50%',
        background: '#fff',
        animation: 'pulse 1.5s ease-in-out infinite',
      }} />
      {label}
      <style>{`@keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.3; } }`}</style>
    </Link>
  );
}
