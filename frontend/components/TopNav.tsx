'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useI18n } from '@/lib/i18n';
import LanguageToggle from './LanguageToggle';

interface BreadcrumbItem {
  label: string;
  href?: string;
}

interface TopNavProps {
  activeHref?: string;
  breadcrumbs?: BreadcrumbItem[];
}

export default function TopNav({ activeHref, breadcrumbs }: TopNavProps) {
  const pathname = usePathname();
  const { t } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);
  const [moreMenuOpen, setMoreMenuOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  // Top-level links kept in the bar (declutter — "trop de boutons").
  const TOP_LEVEL_LINKS = [
    { href: '/map', label: t('nav.map') },
  ];
  // Demoted into the "Menu" dropdown — still reachable, not top-level.
  // Reframed around the user's own content (2026-07 pivot): stats, the traces
  // they IMPORTED (upload), the routes they CREATED (tracer), and the single
  // Import/Contribute entry. No "Connecter Strava" — the personal-connect flow
  // is gone. Admin stays where it already lives (the map account menu), so it
  // is not duplicated here (TopNav has no is_admin signal).
  // /stats restored to the nav (Paul, 2026-08-08): the page was rewritten into
  // a clean post-pivot PERSONAL dashboard (summary cards + Personal Bests /
  // Records + Sport breakdown). The matched-era heatmap/by-sport-cells sections
  // that read empty post-pivot were removed, so it no longer looks broken.
  // The old /stats#mes-traces "myTraces" entry stays OUT — that section is now
  // masked. See project_backlog_launchpad_2026_08_04 "/stats".
  const MENU_LINKS = [
    { href: '/stats', label: t('nav.myStats'), authOnly: true },
    { href: '/me/routes', label: t('nav.myRoutes'), authOnly: true },
    { href: '/strava', label: t('nav.contribute') },
    { href: '/calque', label: t('nav.overlay') },
  ];

  useEffect(() => {
    try {
      const userId = typeof window !== 'undefined' ? localStorage.getItem('user_id') : null;
      setIsLoggedIn(!!userId);
    } catch { /* SSR / no localStorage */ }
  }, []);

  useEffect(() => {
    const mql = window.matchMedia('(max-width: 640px)');
    const handler = (e: MediaQueryListEvent | MediaQueryList) => setIsMobile(e.matches);
    handler(mql);
    mql.addEventListener('change', handler as (e: MediaQueryListEvent) => void);
    return () => mql.removeEventListener('change', handler as (e: MediaQueryListEvent) => void);
  }, []);

  // Close menus on navigation
  useEffect(() => { setMenuOpen(false); setMoreMenuOpen(false); }, [pathname]);

  const isActive = (href: string) => {
    if (activeHref) return activeHref === href;
    if (pathname === '/me/stats' && href === '/stats') return true;
    if (pathname === '/me/routes' && href === '/me/routes') return true;
    return pathname === href;
  };

  const visibleMenuLinks = MENU_LINKS.filter(l => !l.authOnly || isLoggedIn);
  // Mobile hamburger keeps everything reachable in one flat list.
  const visibleLinks = [...TOP_LEVEL_LINKS, ...visibleMenuLinks];
  const menuActive = visibleMenuLinks.some(l => isActive(l.href));

  const linkStyle = (href: string): React.CSSProperties => ({
    color: isActive(href) ? '#333' : '#777',
    fontWeight: isActive(href) ? 600 : 400,
    textDecoration: 'none',
    fontSize: 12,
  });

  const navLinks = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
      {TOP_LEVEL_LINKS.map((link) => (
        <Link
          key={link.href}
          href={link.href}
          aria-current={isActive(link.href) ? 'page' : undefined}
          style={linkStyle(link.href)}
        >
          {link.label}
        </Link>
      ))}

      {/* "Menu" dropdown — demoted links (My routes, Collections, Stats, Method) */}
      <div style={{ position: 'relative' }}>
        <button
          onClick={() => setMoreMenuOpen(v => !v)}
          aria-expanded={moreMenuOpen}
          aria-haspopup="menu"
          style={{
            display: 'flex', alignItems: 'center', gap: 4,
            background: 'none', border: 'none', cursor: 'pointer', padding: 0,
            color: menuActive || moreMenuOpen ? '#333' : '#777',
            fontWeight: menuActive ? 600 : 400,
            fontSize: 12,
          }}
        >
          {t('nav.menu')}
          <svg width="9" height="6" viewBox="0 0 10 6" fill="none" style={{ transform: moreMenuOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
            <path d="M1 1l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        {moreMenuOpen && (
          <>
            {/* Backdrop to close on outside click */}
            <div style={{ position: 'fixed', inset: 0, zIndex: 98 }} onClick={() => setMoreMenuOpen(false)} />
            <div
              role="menu"
              style={{
                position: 'absolute', top: 'calc(100% + 8px)', left: 0,
                background: '#fff', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,0.15)',
                border: '1px solid #e8e8e8', minWidth: 180, zIndex: 99, padding: '6px 0',
                display: 'flex', flexDirection: 'column',
              }}
            >
              {visibleMenuLinks.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  role="menuitem"
                  aria-current={isActive(link.href) ? 'page' : undefined}
                  onClick={() => setMoreMenuOpen(false)}
                  style={{
                    padding: '8px 16px', fontSize: 13, textDecoration: 'none',
                    color: isActive(link.href) ? '#2d6a4f' : '#444',
                    fontWeight: isActive(link.href) ? 600 : 400,
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = '#f5f5f5')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                >
                  {link.label}
                </Link>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );

  const centerContent = breadcrumbs ? (
    // Nav links + breadcrumb trail
    <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
      {navLinks}
      <span style={{ color: '#e0e0e0', fontSize: 14 }}>|</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
        {breadcrumbs.filter((c) => !c.href).map((crumb, i) => (
          <span key={i} style={{ color: '#555', fontWeight: 600 }}>{crumb.label}</span>
        ))}
      </div>
    </div>
  ) : navLinks;

  const mobileItems = visibleLinks;

  return (
    <nav
      role="navigation"
      aria-label={t('nav.ariaLabel')}
      style={{ background: '#fff', borderBottom: '1px solid #e5e5e5', position: 'relative' }}
    >
      <div style={{
        height: 44,
        padding: '0 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
      }}>
        {/* LEFT: Logo */}
        <Link href="/" style={{ fontWeight: 700, fontSize: 14, color: '#2d6a4f', textDecoration: 'none', flexShrink: 0, letterSpacing: '-0.01em' }}>
          CHEMINS COMMUNS
        </Link>
        <span style={{ color: '#e0e0e0', fontSize: 14 }}>|</span>

        {/* CENTER */}
        {!isMobile && centerContent}

        <div style={{ flex: 1 }} />

        {/* RIGHT: Hamburger (mobile) */}
        {isMobile && (
          <button
            onClick={() => setMenuOpen((v) => !v)}
            aria-expanded={menuOpen}
            aria-label={t('nav.menuAriaLabel')}
            style={{
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              padding: 6,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <svg width="22" height="18" viewBox="0 0 22 18" fill="none">
              <rect y="0" width="22" height="2" rx="1" fill="#555" />
              <rect y="8" width="22" height="2" rx="1" fill="#555" />
              <rect y="16" width="22" height="2" rx="1" fill="#555" />
            </svg>
          </button>
        )}

        {/* RIGHT: Language toggle */}
        <LanguageToggle />

        {/* RIGHT: Account / Login */}
        <Link href={isLoggedIn ? '/strava' : '/'} style={{
          padding: '3px 10px',
          background: '#2d6a4f',
          color: '#fff',
          borderRadius: 5,
          textDecoration: 'none',
          fontSize: 12,
          fontWeight: 600,
          flexShrink: 0,
        }}>
          {isLoggedIn ? `👤 ${t('nav.account')}` : t('nav.login')}
        </Link>
      </div>

      {/* Mobile dropdown */}
      {isMobile && menuOpen && (
        <div style={{
          position: 'absolute',
          top: 44,
          left: 0,
          right: 0,
          background: '#fff',
          borderBottom: '1px solid #e0e0e0',
          boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
          zIndex: 100,
          display: 'flex',
          flexDirection: 'column',
          padding: '8px 0',
        }}>
          {mobileItems.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              aria-current={isActive(link.href) ? 'page' : undefined}
              style={{
                padding: '10px 24px',
                fontSize: 14,
                color: isActive(link.href) ? '#555' : '#888',
                fontWeight: isActive(link.href) ? 600 : 400,
                textDecoration: 'none',
              }}
            >
              {link.label}
            </Link>
          ))}
        </div>
      )}

      <style>{`
        nav[role="navigation"] a:focus-visible,
        nav[role="navigation"] button:focus-visible {
          outline: 2px solid #2d6a4f;
          outline-offset: 2px;
        }
      `}</style>
    </nav>
  );
}
