'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useT } from '@/lib/i18n';

/** localStorage flag remembering that the visitor closed the beta banner. */
export const BETA_BANNER_DISMISSED_KEY = 'cc_beta_footer_dismissed';

export default function BetaBanner() {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  // Hidden state is read from localStorage AFTER mount (never during render:
  // the static export has no storage → a render-time read would mismatch on
  // hydration, React #418). Storage can throw (private mode, blocked site
  // data) — the banner then simply stays visible.
  const [dismissed, setDismissed] = useState(false);
  // Phones: one short line. The full sentence wrapped onto three lines and,
  // with the toolbars, left about two thirds of the screen to the map.
  // Read after mount (static export → no matchMedia during render).
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const mql = window.matchMedia('(max-width: 640px)');
    const on = () => setCompact(mql.matches);
    on();
    mql.addEventListener?.('change', on);
    return () => mql.removeEventListener?.('change', on);
  }, []);
  useEffect(() => {
    try {
      if (localStorage.getItem(BETA_BANNER_DISMISSED_KEY) === '1') setDismissed(true);
    } catch { /* storage unavailable — keep the banner */ }
  }, []);

  const dismiss = () => {
    setDismissed(true);
    try { localStorage.setItem(BETA_BANNER_DISMISSED_KEY, '1'); } catch { /* best-effort */ }
  };

  // Publish the banner's actual rendered height as a CSS variable so any
  // bottom-anchored UI (map legend, selected-activity card, GPX preview…) can
  // sit ABOVE it instead of being overlapped. ResizeObserver re-publishes on
  // safe-area / font-size changes (rotation, mobile keyboard, locale). Once
  // dismissed the variable drops to 0px so those panels slide back down.
  useEffect(() => {
    const root = document.documentElement;
    if (dismissed || !ref.current) {
      root.style.setProperty('--beta-banner-h', '0px');
      return;
    }
    const el = ref.current;
    const publish = () => {
      root.style.setProperty('--beta-banner-h', `${el.offsetHeight}px`);
    };
    publish();
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(publish) : null;
    ro?.observe(el);
    return () => {
      ro?.disconnect();
      root.style.removeProperty('--beta-banner-h');
    };
  }, [dismissed]);

  if (dismissed) return null;

  return (
    <div
      ref={ref}
      data-testid="beta-banner"
      style={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        zIndex: 9998,
        padding: '8px 36px 8px 16px',
        fontSize: '13px',
        textAlign: 'center',
        color: '#1e3a5f',
        background: '#dbeafe',
        borderTop: '1px solid #93c5fd',
        paddingBottom: 'max(8px, env(safe-area-inset-bottom))',
      }}
    >
      {compact ? (
        <>
          <strong>{t('layout.betaShort')}</strong>
          <span style={{ margin: '0 8px', opacity: 0.4 }}>·</span>
          <a href="mailto:paul@epauler.fr" data-testid="beta-banner-report" style={{ color: '#1d4ed8', fontWeight: 600 }}>
            {t('layout.betaReport')}
          </a>
        </>
      ) : (
        <>
          {t('layout.beta')}{' '}
          <a href="mailto:paul@epauler.fr" style={{ color: '#1d4ed8', fontWeight: 600 }}>
            paul@epauler.fr
          </a>
        </>
      )}
      <span style={{ margin: '0 8px', opacity: 0.4 }}>·</span>
      <Link href="/privacy" style={{ color: '#1d4ed8', fontWeight: 500, textDecoration: 'underline' }}>
        {t('nav.privacy')}
      </Link>
      <span style={{ margin: '0 8px', opacity: 0.4 }}>·</span>
      <Link href="/support" style={{ color: '#1d4ed8', fontWeight: 500, textDecoration: 'underline' }}>
        {t('nav.support')}
      </Link>
      <button
        type="button"
        data-testid="beta-banner-dismiss"
        onClick={dismiss}
        aria-label={t('layout.betaDismiss')}
        title={t('layout.betaDismiss')}
        style={{
          position: 'absolute', top: 4, right: 6,
          background: 'none', border: 'none', cursor: 'pointer',
          color: '#1e3a5f', fontSize: 18, lineHeight: 1, padding: '2px 6px', opacity: 0.6,
        }}
      >
        ×
      </button>
    </div>
  );
}
