'use client';

import Link from 'next/link';
import TopNav from '@/components/TopNav';
import { useT } from '@/lib/i18n';

export default function PrivacyPage() {
  const t = useT();
  return (
    <div style={{ minHeight: '100vh', background: '#f5f5f0' }}>
      <TopNav breadcrumbs={[{ label: t('privacy.breadcrumb') }]} />

      <div style={{ maxWidth: 720, margin: '32px auto', padding: '0 24px 60px' }}>
        <div style={{ background: '#fff', borderRadius: 14, padding: '32px 36px', boxShadow: '0 1px 4px rgba(0,0,0,0.06)', border: '1px solid #eee' }}>

          <h1 style={{ fontSize: 24, fontWeight: 800, color: '#1a4731', margin: '0 0 6px' }}>
            {t('privacy.title')}
          </h1>
          <p style={{ fontSize: 13, color: '#999', margin: '0 0 28px' }}>
            {t('privacy.lastUpdate')}
          </p>

          <Section title={t('privacy.whoWeAre')}>
            <P>{t('privacy.whoWeAreText')} <a href="https://github.com/polomarcus/common-trails" target="_blank" rel="noopener noreferrer" style={{ color: '#2d6a4f' }}>GitHub</a>.</P>
            <P>{t('privacy.responsible')}</P>
          </Section>

          <Section title={t('privacy.dataCollected')}>
            <P>{t('privacy.dataCollectedIntro')}</P>
            <Ul>
              <Li>{t('privacy.gpsTraces')}</Li>
              <Li>{t('privacy.activityMeta')}</Li>
              <Li>{t('privacy.thirdPartyId')}</Li>
            </Ul>
            <P>{t('privacy.alsoCollect')}</P>
            <Ul>
              <Li>{t('privacy.email')}</Li>
              <Li>{t('privacy.techData')}</Li>
            </Ul>
          </Section>

          <Section title={t('privacy.privateVsCommunity')}>
            <P>{t('privacy.activitiesPrivate')}</P>
            <P>{t('privacy.kAnonymity')}</P>
            <P>{t('privacy.odbl')}</P>
          </Section>

          <Section title={t('privacy.useTitle')}>
            <P>{t('privacy.usedFor')}</P>
            <Ul>
              <Li>{t('privacy.displayTraces')}</Li>
              <Li>{t('privacy.feedHeatmap')}</Li>
              <Li>{t('privacy.routeCalc')}</Li>
              <Li>{t('privacy.personalStats')}</Li>
            </Ul>
            <P><strong>{t('privacy.weDontTitle')}</strong></P>
            <Ul>
              <Li>{t('privacy.noAds')}</Li>
              <Li>{t('privacy.noSale')}</Li>
              <Li>{t('privacy.noProfiling')}</Li>
              <Li>{t('privacy.noSponsored')}</Li>
            </Ul>
          </Section>

          <Section title={t('privacy.thirdPartyTitle')}>
            <P>{t('privacy.oauthExplain')}</P>
            <P>{t('privacy.revokeAccess')}</P>
            <P>{t('privacy.noPassword')}</P>
          </Section>

          <Section title={t('privacy.hosting')}>
            <Ul>
              <Li>{t('privacy.hostingGcp')}</Li>
              <Li>{t('privacy.hostingDb')}</Li>
              <Li>{t('privacy.hostingTls')}</Li>
              <Li>{t('privacy.hostingAuth')}</Li>
            </Ul>
          </Section>

          <Section title={t('privacy.retentionTitle')}>
            <P>{t('privacy.retention')}</P>
            <P><strong>{t('privacy.youCan')}</strong></P>
            <Ul>
              <Li>{t('privacy.deleteActivity')}</Li>
              <Li>{t('privacy.deleteAccount')}</Li>
            </Ul>
            <P>{t('privacy.heatmapRecalc')}</P>
          </Section>

          <Section title={t('privacy.cookiesTitle')}>
            <P>{t('privacy.cookiesText')}</P>
          </Section>

          <Section title={t('privacy.gdprTitle')}>
            <P>{t('privacy.gdpr')}</P>
            <Ul>
              <Li>{t('privacy.rightAccess')}</Li>
              <Li>{t('privacy.rightRectification')}</Li>
              <Li>{t('privacy.rightDeletion')}</Li>
              <Li>{t('privacy.rightPortability')}</Li>
              <Li>{t('privacy.rightOpposition')}</Li>
            </Ul>
            <P>{t('privacy.exerciseRights')}</P>
          </Section>

          <Section title={t('privacy.changesTitle')}>
            <P>{t('privacy.policyUpdate')}</P>
          </Section>

          <div style={{ marginTop: 28, paddingTop: 20, borderTop: '1px solid #eee', textAlign: 'center' }}>
            <Link href="/map" style={{ color: '#2d6a4f', fontWeight: 700, fontSize: 14, textDecoration: 'none' }}>
              {t('privacy.backToMap')}
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

function Ul({ children }: { children: React.ReactNode }) {
  return <ul style={{ margin: '6px 0 10px', paddingLeft: 20, fontSize: 14, color: '#444', lineHeight: 1.7 }}>{children}</ul>;
}

function Li({ children }: { children: React.ReactNode }) {
  return <li style={{ marginBottom: 3 }}>{children}</li>;
}
