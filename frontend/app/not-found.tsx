'use client';

import Link from 'next/link';
import TopNav from '@/components/TopNav';
import { useT } from '@/lib/i18n';

/**
 * 404 — exported as `404.html`, which the backend serves (with a real 404
 * status) for any unknown path. Until 2026-11-11 this was Next's default
 * page: unstyled, in English ("This page could not be found.") and with no
 * way back to the site.
 */
export default function NotFound() {
  const t = useT();
  return (
    <div style={{ minHeight: '100vh', background: '#f5f5f0' }}>
      <TopNav />
      <div style={{ maxWidth: 560, margin: '64px auto', padding: '0 24px 60px', textAlign: 'center' }}>
        <div data-testid="not-found" style={{ background: '#fff', borderRadius: 14, padding: '36px 32px', boxShadow: '0 1px 4px rgba(0,0,0,0.06)', border: '1px solid #e8e8e8' }}>
          <p style={{ fontSize: 13, fontWeight: 700, letterSpacing: '0.08em', color: '#2d6a4f', margin: '0 0 8px' }}>404</p>
          <h1 style={{ fontSize: 24, fontWeight: 800, color: '#1a4731', margin: '0 0 10px' }}>{t('notFound.title')}</h1>
          <p style={{ fontSize: 14, color: '#444', lineHeight: 1.7, margin: '0 0 24px' }}>{t('notFound.text')}</p>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
            <Link href="/map" data-testid="not-found-map" style={{ padding: '10px 20px', borderRadius: 10, background: '#2d6a4f', color: '#fff', fontWeight: 700, fontSize: 14, textDecoration: 'none' }}>
              {t('notFound.map')}
            </Link>
            <Link href="/" style={{ padding: '10px 20px', borderRadius: 10, border: '1px solid #cfd8d2', color: '#1a4731', fontWeight: 600, fontSize: 14, textDecoration: 'none' }}>
              {t('notFound.home')}
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
