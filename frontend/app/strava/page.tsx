'use client';

/**
 * /strava — SINGLE-JOB contribution page (2026-07 compliance pivot).
 *
 * The ONLY compliant path to the OPEN community map is a MANUAL upload of the
 * user's OWN data, with explicit consent, anonymised, ODbL — GPX / FIT traces
 * or a Strava/Garmin/Komoot export `.zip`, all through the ONE smart
 * `ContributionDropzone` (any `.zip` → the GB-safe archive path; loose GPX/FIT
 * → /imports/files).
 *
 * The old "connect Strava = a personal view of your rides" flow is CANCELLED at
 * the UI level (the backend OAuth / webhook / sync code stays in place but
 * dormant — there is simply no UI entry point here anymore). This page has one
 * job: "deposit your traces to contribute to the common map."
 *
 * The page is source-neutral: Strava is one source among GPX / FIT / others, so
 * the hero is a single deposit dropzone and the Strava-specific material (how to
 * GET your archive) is folded into a collapsed, HELP-ONLY "Using Strava?"
 * section (`StravaArchiveImport` — instructions + links, no picker/consent). A
 * deposit needs an account (consent is tied to a user), so a logged-out visitor
 * gets the same hero dropzone plus ONE inline account gate (EmailLoginForm) and
 * the same collapsed Strava help.
 */
import { useState, useEffect, type ReactNode } from 'react';
import Link from 'next/link';
import { API_URL } from '@/lib/api-client';
import { getToken, setAuthState, clearAuthState } from '@/lib/auth';
import { useI18n } from '@/lib/i18n';
import ContributionDropzone from '@/components/ContributionDropzone';
import StravaArchiveImport from '@/components/StravaArchiveImport';
import EmailLoginForm from '@/components/EmailLoginForm';

/**
 * Collapsed "Using Strava? Get your archive" section. Since Strava is now one
 * source among many (GPX / FIT / others), all Strava-specific material (the
 * bulk-archive wizard + its how-to) is folded away so it no longer dominates
 * the page. The summary carries the always-visible `archive-strava-help` hook.
 */
function StravaHelpDetails({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  return (
    <details id="strava-help" data-testid="archive-strava-help" style={{ marginTop: 12 }}>
      <summary style={{
        cursor: 'pointer', listStyle: 'none',
        padding: '12px 14px', borderRadius: 10,
        background: 'rgba(252,76,2,0.08)', border: '1px solid rgba(252,76,2,0.22)',
        color: '#fc8c4c', fontSize: 13, fontWeight: 700,
        display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12,
      }}>
        <span aria-hidden="true">📦</span> {t('strava.stravaHelpSummary')}
      </summary>
      {children}
    </details>
  );
}

// Garmin equivalent of the Strava archive help — parity across sources (Paul,
// 2026-08-08). Blue-themed, collapsed by default. Garmin's export is simpler
// than Strava's (one page), so the how-to is inline rather than a big component.
function GarminHelpDetails() {
  const { t } = useI18n();
  return (
    <details data-testid="archive-garmin-help" style={{ marginTop: 8 }}>
      <summary style={{
        cursor: 'pointer', listStyle: 'none',
        padding: '12px 14px', borderRadius: 10,
        background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.24)',
        color: '#6ea8fe', fontSize: 13, fontWeight: 700,
        display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12,
      }}>
        <span aria-hidden="true">🟦</span> {t('strava.garminHelpSummary')}
      </summary>
      <div style={{ padding: '0 4px 8px', color: 'rgba(255,255,255,0.6)', fontSize: 12.5, lineHeight: 1.6 }}>
        <p style={{ margin: '0 0 10px' }}>{t('strava.garminHelpBody')}</p>
        <a
          href="https://www.garmin.com/fr-FR/account/datamanagement/exportdata"
          target="_blank"
          rel="noopener noreferrer"
          data-testid="garmin-help-export-link"
          style={{ color: '#6ea8fe', textDecoration: 'underline', fontWeight: 600 }}
        >
          {t('strava.garminHelpLink')}
        </a>
      </div>
    </details>
  );
}

export default function StravaPage() {
  const { t } = useI18n();

  // SSG-safe init: token stays null on both the static build and the first
  // client render (getToken() / URL parsing must NOT run during initial
  // useState eval — that diverges between build + hydrating client → React
  // #418). `ready` gates the branch so we never flash the wrong state.
  const [token, setTokenState] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const urlUserId = params.get('user_id');
    if (urlUserId) {
      setAuthState(urlUserId);
      fetch(`${API_URL}/auth/me`, { credentials: 'include' })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (!data || data.user_id !== urlUserId) clearAuthState();
        })
        .catch(() => clearAuthState());
    }
    setTokenState(getToken());
    setReady(true);
  }, []);

  // When logged in, tell the user whether they've ALREADY contributed to the
  // community heatmap (Paul, 2026-08-08 — "on devrait voir si on a déjà
  // contribué"). Community-eligible count only (manual_upload), via the
  // provenance SSOT on the backend. Silent on error — the dropzone still works.
  const [contrib, setContrib] = useState<{ contributed: boolean; count: number; last: string | null } | null>(null);
  useEffect(() => {
    if (!ready || !token) return;
    let cancelled = false;
    fetch(`${API_URL}/me/contribution-status`, { credentials: 'include', headers: { Authorization: `Bearer ${token}` } })
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (!cancelled && d) setContrib({ contributed: d.contributed, count: d.count, last: d.last_contribution_at }); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [ready, token]);

  // Subtitle "your Strava export" link → reveal + scroll to the on-page
  // "Using Strava? Request your archive" how-to (the collapsed <details>).
  const openStravaHelp = () => {
    const el = document.getElementById('strava-help') as HTMLDetailsElement | null;
    if (!el) return;
    el.open = true;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // ── The compact "how it flows" strip — 3 steps, scans in one glance. ──
  const flowSteps = [
    { n: '1', label: t('strava.flow.step1') },
    { n: '2', label: t('strava.flow.step2') },
    { n: '3', label: t('strava.flow.step3') },
  ];

  return (
    <>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes ct-strava-rise { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: translateY(0); } }
      `}</style>

      <div style={{
        fontFamily: 'system-ui, -apple-system, sans-serif',
        minHeight: '100vh',
        background: 'radial-gradient(1200px 600px at 50% -10%, rgba(46,204,113,0.10), transparent 60%), linear-gradient(160deg, #0a0e1a 0%, #101827 45%, #0d1420 100%)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'flex-start',
        padding: '28px 20px 48px',
      }}>
        {/* Back navigation */}
        <div style={{ position: 'fixed', top: 16, left: 20, zIndex: 10 }}>
          <Link href="/map" style={{
            color: 'rgba(255,255,255,0.55)', textDecoration: 'none', fontSize: 13,
            display: 'flex', alignItems: 'center', gap: 5,
            padding: '6px 12px', borderRadius: 8,
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.06)',
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
            {t('nav.map')}
          </Link>
        </div>

        <div style={{
          maxWidth: 520, width: '100%', marginTop: 24,
          animation: 'ct-strava-rise 0.4s ease-out',
        }}>
          {/* ── Header ── */}
          <header style={{ textAlign: 'center', marginBottom: 22 }}>
            <div style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: 54, height: 54, borderRadius: 15,
              background: 'linear-gradient(135deg, #27ae60, #2d6a4f)',
              boxShadow: '0 8px 28px rgba(39,174,96,0.28)',
              marginBottom: 16,
            }}>
              <span style={{ fontSize: 27 }} aria-hidden="true">🚲</span>
            </div>
            <h1 style={{
              color: '#fff', fontSize: 26, fontWeight: 800, margin: '0 0 8px',
              letterSpacing: '-0.02em', lineHeight: 1.15,
            }}>
              {t('strava.pageHeader')}
            </h1>
            <p style={{
              color: 'rgba(255,255,255,0.55)', fontSize: 14, lineHeight: 1.55,
              margin: '0 auto', maxWidth: 400,
            }}>
              {t('strava.pageSubtitle')}
            </p>
          </header>

          {/* ── Logged-out: "create your account to contribute" FIRST, right
                 under the header (Paul, 2026-08-09: "ça doit être en haut ça").
                 The deposit is gated because consent is tied to a user;
                 passwordless email. Logged-in users never see this. ── */}
          {ready && !token && (
            <div data-testid="strava-signup-gate" style={{
              background: 'rgba(46,204,113,0.06)',
              border: '1px solid rgba(46,204,113,0.2)',
              borderRadius: 12, padding: '16px', marginBottom: 20,
            }}>
              <p style={{ color: '#fff', fontSize: 15, fontWeight: 700, margin: '0 0 2px' }}>
                {t('strava.signupToContribute')}
              </p>
              <p style={{ color: 'rgba(255,255,255,0.5)', fontSize: 12, margin: '0 0 12px', lineHeight: 1.5 }}>
                {t('strava.signupToContributeSub')}
              </p>
              <EmailLoginForm variant="dark" />
            </div>
          )}

          {/* ── How to get your data — Strava / Garmin, right at the top
                 (Paul, 2026-08-09: "droit au but", these 2 parts tout en haut). */}
          <div style={{ marginBottom: 20 }}>
            <StravaHelpDetails>
              <StravaArchiveImport />
            </StravaHelpDetails>
            <GarminHelpDetails />
          </div>

          {/* ── 3-step flow strip — the how-to at a glance ── */}
          <ol data-testid="strava-flow-strip" style={{
            listStyle: 'none', margin: '0 0 24px', padding: 0,
            display: 'flex', alignItems: 'stretch', gap: 8,
          }}>
            {flowSteps.map((step, i) => (
              <li key={step.n} style={{
                flex: 1, position: 'relative',
                display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8,
                background: 'rgba(46,204,113,0.06)',
                border: '1px solid rgba(46,204,113,0.18)',
                borderRadius: 12, padding: '14px 8px', textAlign: 'center',
              }}>
                <span style={{
                  width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
                  background: 'linear-gradient(135deg, #27ae60, #2d6a4f)',
                  color: '#fff', fontSize: 13, fontWeight: 800,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>{step.n}</span>
                <span style={{ color: 'rgba(255,255,255,0.72)', fontSize: 11.5, lineHeight: 1.35, fontWeight: 500 }}>
                  {step.label}
                </span>
                {i < flowSteps.length - 1 && (
                  <span aria-hidden="true" style={{
                    position: 'absolute', right: -9, top: '50%', transform: 'translateY(-50%)',
                    color: 'rgba(46,204,113,0.55)', fontSize: 14, fontWeight: 700, zIndex: 1,
                  }}>›</span>
                )}
              </li>
            ))}
          </ol>

          {/* ── Loading (token resolving) ── */}
          {!ready && (
            <div style={{ textAlign: 'center', padding: '24px 0' }}>
              <div style={{
                width: 34, height: 34, border: '3px solid rgba(255,255,255,0.08)',
                borderTopColor: '#2ecc71', borderRadius: '50%',
                animation: 'spin 0.8s linear infinite', margin: '0 auto',
              }} />
            </div>
          )}

          {/* ── Logged-out: the SAME hero dropzone (the account gate now sits
                 at the TOP, right under the header — see above). ── */}
          {ready && !token && (
            <section data-testid="strava-contribute-section">
              {/* HERO — the one source-neutral dropzone. Its dispatch gates on
                  getToken(): a logged-out attempt surfaces the "log in to
                  import" message, so signing up at the top unlocks the deposit. */}
              <ContributionDropzone />
            </section>
          )}

          {/* ── Logged-in: the ONE smart dropzone is the hero; Strava help is folded ── */}
          {ready && token && (
            <section data-testid="strava-contribute-section">
              {/* Contribution status — has this user already fed the heatmap? */}
              {contrib && (
                <div data-testid="contrib-status" style={{
                  background: contrib.contributed ? 'rgba(46,204,113,0.1)' : 'rgba(255,255,255,0.05)',
                  border: `1px solid ${contrib.contributed ? 'rgba(46,204,113,0.3)' : 'rgba(255,255,255,0.12)'}`,
                  borderRadius: 12, padding: '12px 16px', marginBottom: 12,
                  color: '#fff', fontSize: 14, lineHeight: 1.5,
                }}>
                  {contrib.contributed ? (
                    <span>✅ {t('strava.contribStatus.done').replace('{count}', String(contrib.count))}</span>
                  ) : (
                    <span>👋 {t('strava.contribStatus.none')}</span>
                  )}
                </div>
              )}
              {/* HERO — a single source-neutral dropzone. Drops route by file
                  type: any .zip → the GB-safe direct-to-storage archive path;
                  GPX/FIT → /imports/files. One ODbL consent gate for both. */}
              <ContributionDropzone />
            </section>
          )}
        </div>

        {/* Footer */}
        <div style={{ marginTop: 24, textAlign: 'center' }}>
          <Link href="/" style={{ color: 'rgba(255,255,255,0.2)', fontSize: 12, textDecoration: 'none' }}>
            {t('common.appName')}
          </Link>
        </div>
      </div>
    </>
  );
}
