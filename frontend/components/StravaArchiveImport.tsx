'use client';

/**
 * Help-only: "Using Strava? Get your archive in 3 minutes."
 *
 * The actual deposit (file picker + ODbL consent + upload) now lives ONCE, in
 * the source-neutral hero `ContributionDropzone`. This component is purely
 * INSTRUCTIONAL: it explains WHY reclaiming your data matters and HOW to get
 * the export archive out of Strava, then points at the two Strava pages. It
 * owns no upload UI — no dropzone, no consent checkbox, no progress. Drop the
 * downloaded `.zip` on the hero above and it just works.
 *
 * Rendered inside the collapsed `StravaHelpDetails` on /strava (both logged-in
 * and logged-out), so it no longer competes with the hero for attention.
 */
import { type ReactNode } from 'react';

import { useI18n } from '@/lib/i18n';
import {
  STRAVA_ARCHIVE_REQUEST_URL,
  STRAVA_BULK_EXPORT_HELP_URL,
} from '@/lib/strava-archive-consent';

export default function StravaArchiveImport() {
  const { t } = useI18n();

  const linkStyle = { color: '#5dade2', textDecoration: 'underline' } as const;

  // ── Numbered, visual how-to steps (GET the archive from Strava) ─────────────
  // Step 2 embeds a real link on the "Download or Delete Your Account" label so
  // the user lands straight on Strava's archive-request page. The copy carries a
  // {exportLink} token we split on, keeping the sentence translatable.
  const [s2Before, s2After] = t('strava.archive.s2').split('{exportLink}');
  const steps: ReactNode[] = [
    t('strava.archive.s1'),
    <>
      {s2Before}
      <a href={STRAVA_ARCHIVE_REQUEST_URL} target="_blank" rel="noopener noreferrer" style={linkStyle}>
        {t('strava.archive.s2LinkLabel')}
      </a>
      {s2After}
    </>,
    t('strava.archive.s3'),
    t('strava.archive.s4'),
  ];

  return (
    <div
      data-testid="strava-archive-import"
      style={{
        background: 'rgba(45,106,79,0.06)',
        border: '1px solid rgba(45,106,79,0.2)',
        borderRadius: 12, padding: '16px', marginBottom: 12,
      }}
    >
      <p style={{ color: '#2ecc71', fontSize: 15, fontWeight: 700, margin: '0 0 6px' }}>
        {t('strava.archive.title')}
      </p>
      <p style={{ color: 'rgba(255,255,255,0.5)', fontSize: 12, lineHeight: 1.5, margin: '0 0 10px' }}>
        {t('strava.archive.intro')}
      </p>

      {/* The "why" (vulgarisation) — framed as a POSITIVE act (reclaim your own
          data / the mission), never "because of Strava's rules". Collapsed by
          default (<details>) so the page stays short; the summary is always
          visible. See docs/strava/community-contribution-ux.md §1. */}
      <details
        data-testid="archive-why"
        style={{
          background: 'rgba(93,173,226,0.08)', border: '1px solid rgba(93,173,226,0.22)',
          borderRadius: 8, padding: '8px 12px', margin: '0 0 12px',
        }}
      >
        <summary style={{ color: '#5dade2', fontSize: 13, fontWeight: 700, cursor: 'pointer' }}>
          {t('strava.archive.whyTitle')}
        </summary>
        <p style={{ color: 'rgba(255,255,255,0.7)', fontSize: 12, lineHeight: 1.55, margin: '8px 0 0' }}>
          {t('strava.archive.whyBody')}
        </p>
      </details>

      {/* Reassurance: Strava hides the export behind a scary "delete account"
          page. Make it unambiguous that nothing is deleted. */}
      <p
        data-testid="archive-reassure"
        style={{
          color: '#2ecc71', fontSize: 12, fontWeight: 600, lineHeight: 1.5,
          background: 'rgba(46,204,113,0.08)', border: '1px solid rgba(46,204,113,0.18)',
          borderRadius: 8, padding: '8px 12px', margin: '0 0 14px',
        }}
      >
        {t('strava.archive.reassure')}
      </p>

      {/* ── Guided, numbered how-to — GET the archive from Strava ── */}
      <p style={{ color: 'rgba(255,255,255,0.7)', fontSize: 12, fontWeight: 700, margin: '0 0 8px' }}>
        {t('strava.archive.howto')}
      </p>
      <ol data-testid="archive-steps" style={{ listStyle: 'none', margin: '0 0 10px', padding: 0 }}>
        {steps.map((label, i) => (
          <li key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', marginBottom: 8 }}>
            <span style={{
              width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
              background: 'linear-gradient(135deg, #2d6a4f, #1a4731)',
              color: '#fff', fontSize: 11, fontWeight: 700,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              boxShadow: '0 2px 6px rgba(45,106,79,0.35)',
            }}>{i + 1}</span>
            <span style={{ color: 'rgba(255,255,255,0.62)', fontSize: 12, lineHeight: 1.5, paddingTop: 2 }}>
              {label}
            </span>
          </li>
        ))}
      </ol>

      {/* The two Strava pages: the help article + the archive-request page.
          Once the ZIP lands in your inbox, drop it on the hero dropzone above. */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, margin: '0 0 6px' }}>
        <a
          data-testid="archive-help-link"
          href={STRAVA_BULK_EXPORT_HELP_URL}
          target="_blank" rel="noopener noreferrer" style={linkStyle}
        >
          {t('strava.archive.helpLink')}
        </a>
        <a
          data-testid="archive-export-link"
          href={STRAVA_ARCHIVE_REQUEST_URL}
          target="_blank" rel="noopener noreferrer" style={linkStyle}
        >
          {t('strava.archive.exportPageLink')}
        </a>
      </div>

      <p style={{ color: 'rgba(255,255,255,0.4)', fontSize: 11, lineHeight: 1.5, margin: '8px 0 0' }}>
        {t('strava.archive.dropOnHero')}
      </p>
    </div>
  );
}
