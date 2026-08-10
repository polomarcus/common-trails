'use client';

import { useEffect, useRef } from 'react';
import Link from 'next/link';
import { useT } from '@/lib/i18n';

export default function BetaBanner() {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);

  // Publish the banner's actual rendered height as a CSS variable so any
  // bottom-anchored UI (route-editor panel, proposal sheets…) can sit
  // ABOVE it instead of being overlapped. ResizeObserver re-publishes on
  // safe-area / font-size changes (rotation, mobile keyboard, locale).
  useEffect(() => {
    if (!ref.current) return;
    const el = ref.current;
    const publish = () => {
      document.documentElement.style.setProperty(
        '--beta-banner-h',
        `${el.offsetHeight}px`,
      );
    };
    publish();
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    return () => {
      ro.disconnect();
      document.documentElement.style.removeProperty('--beta-banner-h');
    };
  }, []);

  return (
    <div
      ref={ref}
      style={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        zIndex: 9998,
        padding: '8px 16px',
        fontSize: '13px',
        textAlign: 'center',
        color: '#1e3a5f',
        background: '#dbeafe',
        borderTop: '1px solid #93c5fd',
        paddingBottom: 'max(8px, env(safe-area-inset-bottom))',
      }}
    >
      {t('layout.beta')}{' '}
      <a href="mailto:paul@epauler.fr" style={{ color: '#1d4ed8', fontWeight: 600 }}>
        paul@epauler.fr
      </a>
      <span style={{ margin: '0 8px', opacity: 0.4 }}>·</span>
      <Link href="/privacy" style={{ color: '#1d4ed8', fontWeight: 500, textDecoration: 'underline' }}>
        {t('nav.privacy')}
      </Link>
      <span style={{ margin: '0 8px', opacity: 0.4 }}>·</span>
      <Link href="/support" style={{ color: '#1d4ed8', fontWeight: 500, textDecoration: 'underline' }}>
        {t('nav.support')}
      </Link>
    </div>
  );
}
