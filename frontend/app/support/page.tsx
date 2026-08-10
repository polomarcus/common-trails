'use client';

import Link from 'next/link';
import TopNav from '@/components/TopNav';
import { useT } from '@/lib/i18n';

const CONTACT_EMAIL = 'paul@epauler.fr';

export default function SupportPage() {
  const t = useT();
  return (
    <div style={{ minHeight: '100vh', background: '#f5f5f0' }}>
      <TopNav breadcrumbs={[{ label: t('support.breadcrumb') }]} />

      <div style={{ maxWidth: 720, margin: '32px auto', padding: '0 24px 60px' }}>
        <div style={{ background: '#fff', borderRadius: 14, padding: '32px 36px', boxShadow: '0 1px 4px rgba(0,0,0,0.06)', border: '1px solid #eee' }}>

          <h1 style={{ fontSize: 24, fontWeight: 800, color: '#1a4731', margin: '0 0 6px' }}>
            {t('support.title')}
          </h1>
          <p style={{ fontSize: 14, color: '#444', lineHeight: 1.7, margin: '0 0 28px' }}>
            {t('support.intro')}
          </p>

          <Section title={t('support.contactTitle')}>
            <P>{t('support.contactText')}</P>
            <P>
              <a href={`mailto:${CONTACT_EMAIL}`} style={{ color: '#2d6a4f', fontWeight: 700 }}>
                {CONTACT_EMAIL}
              </a>
            </P>
          </Section>

          <Section title={t('support.dataTitle')}>
            <P>{t('support.dataText')}</P>
            <P>
              <Link href="/privacy" style={{ color: '#2d6a4f', fontWeight: 700 }}>
                {t('support.dataLink')}
              </Link>
            </P>
          </Section>

          <div style={{ marginTop: 28, paddingTop: 20, borderTop: '1px solid #eee', textAlign: 'center' }}>
            <Link href="/map" style={{ color: '#2d6a4f', fontWeight: 700, fontSize: 14, textDecoration: 'none' }}>
              {t('support.backToMap')}
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <h2 style={{ fontSize: 16, fontWeight: 700, color: '#1a4731', margin: '0 0 8px' }}>{title}</h2>
      {children}
    </div>
  );
}

function P({ children }: { children: React.ReactNode }) {
  return <p style={{ fontSize: 14, color: '#444', lineHeight: 1.7, margin: '0 0 8px' }}>{children}</p>;
}
