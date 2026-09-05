'use client';

import { useState } from 'react';
import Link from 'next/link';
import TopNav from '@/components/TopNav';
import { useT } from '@/lib/i18n';

// ── Reusable components ──────────────────────────────────────────────────────

function Card({ children, accent }: { children: React.ReactNode; accent?: boolean }) {
  return (
    <div style={{
      background: accent ? '#f0faf4' : '#fff',
      borderRadius: 16,
      padding: '28px 30px',
      marginBottom: 20,
      boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
      border: accent ? '1px solid #d4edda' : '1px solid #eee',
    }}>
      {children}
    </div>
  );
}

function Title({ children, emoji }: { children: React.ReactNode; emoji?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
      {emoji && <span style={{ fontSize: 24 }}>{emoji}</span>}
      <h2 style={{ fontSize: 19, fontWeight: 800, color: '#1a4731', margin: 0 }}>{children}</h2>
    </div>
  );
}

function P({ children, muted }: { children: React.ReactNode; muted?: boolean }) {
  return (
    <p style={{
      fontSize: 14, color: muted ? '#999' : '#444', lineHeight: 1.7,
      margin: '0 0 10px', fontStyle: muted ? 'italic' : undefined,
    }}>
      {children}
    </p>
  );
}

function Pill({ children, color = '#2d6a4f' }: { children: React.ReactNode; color?: string }) {
  return (
    <span style={{
      display: 'inline-block', padding: '3px 10px', borderRadius: 20,
      fontSize: 11, fontWeight: 700, background: `${color}15`, color,
      border: `1px solid ${color}30`,
    }}>
      {children}
    </span>
  );
}

function Expandable({ label, children, defaultOpen = false }: { label: string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ margin: '8px 0' }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          background: 'none', border: 'none', cursor: 'pointer', padding: '6px 0',
          fontSize: 13, fontWeight: 600, color: '#2d6a4f', display: 'flex', alignItems: 'center', gap: 6,
        }}
      >
        <span style={{ transition: 'transform 0.2s', transform: open ? 'rotate(90deg)' : 'rotate(0)', display: 'inline-block' }}>&#9654;</span>
        {label}
      </button>
      {open && <div style={{ padding: '8px 0 4px 20px', fontSize: 13, color: '#555', lineHeight: 1.7 }}>{children}</div>}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function MethodePage() {
  const t = useT();
  return (
    <div style={{ minHeight: '100vh', background: '#f5f5f0' }}>
      <TopNav activeHref="/methode" />

      {/* Hero */}
      <div style={{
        background: 'linear-gradient(135deg, #1a4731 0%, #2d6a4f 100%)',
        padding: '48px 24px 40px', textAlign: 'center',
      }}>
        <h1 style={{ fontSize: 28, fontWeight: 900, color: '#fff', margin: '0 0 8px' }}>
          {t('methode.heroTitle')}
        </h1>
        <p style={{ fontSize: 15, color: 'rgba(255,255,255,0.7)', margin: 0, maxWidth: 460, marginLeft: 'auto', marginRight: 'auto' }}>
          {t('methode.heroSubtitle')}
        </p>
      </div>

      <div style={{ maxWidth: 740, margin: '0 auto', padding: '32px 20px 60px' }}>

        {/* Le principe */}
        <Card accent>
          <Title emoji="🎯">{t('methode.principleTitle')}</Title>
          <P>{t('methode.rawIntro')}</P>
          <P>{t('methode.rawNoMatch')}</P>
          <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
            <Pill>ODbL 1.0</Pill>
            <Pill>AGPLv3</Pill>
            <Pill>{t('methode.pill.masking')}</Pill>
          </div>
        </Card>

        {/* Des traces brutes, superposées */}
        <Card>
          <Title emoji="🔥">{t('methode.rawBuildTitle')}</Title>
          <P>{t('methode.rawBuildDesc')}</P>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, marginTop: 12 }}>
            {[
              { emoji: '⬆️', title: t('methode.rawStep1Title'), desc: t('methode.rawStep1Desc') },
              { emoji: '✂️', title: t('methode.rawStep2Title'), desc: t('methode.rawStep2Desc') },
              { emoji: '🗺️', title: t('methode.rawStep3Title'), desc: t('methode.rawStep3Desc') },
            ].map((s) => (
              <div key={s.title} style={{
                background: '#f8fdf9', borderRadius: 10, padding: '12px 14px',
                border: '1px solid #e0ede5', textAlign: 'center',
              }}>
                <div style={{ fontSize: 22, marginBottom: 4 }}>{s.emoji}</div>
                <div style={{ fontSize: 13, fontWeight: 700, color: '#1a4731' }}>{s.title}</div>
                <div style={{ fontSize: 11, color: '#888', marginTop: 2 }}>{s.desc}</div>
              </div>
            ))}
          </div>
          <Expandable label={t('methode.rawLatticeDetails')}>
            {t('methode.rawLatticeDetail')}
          </Expandable>
          <P muted>{t('methode.communityGrows')}</P>
        </Card>

        {/* Vie privée : le masquage des extrémités */}
        <Card>
          <Title emoji="🛡️">{t('methode.privacyTitle')}</Title>
          <P>{t('methode.privacyDesc')}</P>
          <div style={{
            background: '#f8f8f5', borderLeft: '3px solid #2d6a4f',
            borderRadius: '0 8px 8px 0', padding: '12px 16px',
            fontSize: 13, color: '#555', lineHeight: 1.7, margin: '4px 0 6px',
          }}>
            {t('methode.privacyMaskCallout')}
          </div>
          <Expandable label={t('methode.privacyFloorTitle')}>
            {t('methode.privacyFloorDetail')}
          </Expandable>
          <P muted>{t('methode.privacyNotKanon')}</P>
        </Card>

        {/* Vos traces, votre consentement */}
        <Card>
          <Title emoji="✅">{t('methode.provenanceTitle')}</Title>
          <P>{t('methode.provenanceDesc')}</P>
          <P>{t('methode.provenanceApi')}</P>
        </Card>

        {/* Traces intouchées */}
        <Card>
          <Title emoji="✋">{t('methode.integrityTitle')}</Title>
          <P>{t('methode.integrityDesc')}</P>
        </Card>

        {/* Le routage : on vous laisse les meilleurs outils */}
        <Card>
          <Title emoji="🧭">{t('methode.routingTitle')}</Title>
          <P>{t('methode.routingDesc')}</P>
          <div style={{ display: 'flex', gap: 8, margin: '4px 0 10px', flexWrap: 'wrap' }}>
            <Pill color="#3b82f6">gpx.studio</Pill>
            <Pill color="#3b82f6">BRouter</Pill>
            <Pill color="#3b82f6">Komoot</Pill>
          </div>
          <P muted>{t('methode.routingNote')}</P>
        </Card>

        {/* Footer */}
        <div style={{
          background: '#1a4731', borderRadius: 16, padding: '28px 32px',
          textAlign: 'center',
        }}>
          <p style={{ fontSize: 15, color: '#fff', fontWeight: 600, margin: '0 0 6px' }}>
            {t('common.thesis')}
          </p>
          <p style={{ fontSize: 13, color: 'rgba(255,255,255,0.6)', margin: '0 0 16px' }}>
            {t('methode.footerExplain')}
          </p>
          <div style={{ display: 'flex', justifyContent: 'center', gap: 12, marginBottom: 16 }}>
            <Link href="/map" style={{
              padding: '10px 24px', background: '#fff', color: '#1a4731',
              borderRadius: 8, textDecoration: 'none', fontSize: 14, fontWeight: 700,
            }}>
              {t('methode.exploreMap')}
            </Link>
            <a href="https://github.com/polomarcus/common-trails" target="_blank" rel="noopener noreferrer" style={{
              padding: '10px 20px', background: 'rgba(255,255,255,0.12)', color: '#fff',
              borderRadius: 8, textDecoration: 'none', fontSize: 13, fontWeight: 600,
              border: '1px solid rgba(255,255,255,0.2)',
            }}>
              GitHub
            </a>
          </div>
          <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.45)', margin: 0 }}>
            {t('methode.footerLicense')}
          </p>
        </div>
      </div>
    </div>
  );
}
