'use client';

import { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useI18n } from '@/lib/i18n';
import { verifyMagicLink } from '@/lib/email-auth';
import { setAuthState } from '@/lib/auth';

/**
 * Magic-link landing page. Reads ?token from the URL (client-side, so it is
 * static-export-safe), POSTs /auth/email/verify, and on success sets the auth
 * state + redirects. On failure shows a friendly error with a "request a new
 * link" path back home.
 */
export default function VerifyMagicLinkPage() {
  const { t } = useI18n();
  const router = useRouter();
  const [state, setState] = useState<'verifying' | 'success' | 'error' | 'missing'>('verifying');
  // Guard against React 18 StrictMode double-invoke consuming the single-use
  // token twice (the second call would 401 and flip a successful login to error).
  const ranRef = useRef(false);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;

    const token = new URLSearchParams(window.location.search).get('token');
    if (!token) {
      setState('missing');
      return;
    }

    verifyMagicLink(token).then((res) => {
      if (res.ok && res.userId) {
        setAuthState(res.userId);
        setState('success');
        router.push(res.redirect || '/strava');
      } else {
        setState('error');
      }
    });
  }, [router]);

  const message =
    state === 'verifying' ? t('emailAuth.verifying')
      : state === 'success' ? t('emailAuth.verifySuccess')
        : state === 'missing' ? t('emailAuth.verifyMissingToken')
          : t('emailAuth.verifyError');

  const isError = state === 'error' || state === 'missing';

  return (
    <div style={{
      fontFamily: 'system-ui, -apple-system, sans-serif',
      minHeight: '100vh',
      background: 'linear-gradient(160deg, #0a0e1a 0%, #111827 40%, #0f172a 100%)',
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      padding: 20,
    }}>
      <div style={{
        background: 'linear-gradient(180deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.02) 100%)',
        borderRadius: 20, border: '1px solid rgba(255,255,255,0.08)',
        padding: '36px 32px', maxWidth: 420, width: '100%', textAlign: 'center',
        boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
      }}>
        {state === 'verifying' && (
          <div style={{
            width: 36, height: 36, border: '3px solid rgba(255,255,255,0.08)',
            borderTopColor: '#2d6a4f', borderRadius: '50%',
            animation: 'spin 0.8s linear infinite', margin: '0 auto 16px',
          }} />
        )}
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        <p
          data-testid="verify-status"
          style={{ color: isError ? '#ff6b6b' : '#fff', fontSize: 16, fontWeight: 600, margin: 0 }}
        >
          {message}
        </p>
        {isError && (
          <Link
            href="/"
            data-testid="verify-back-home"
            style={{
              display: 'inline-block', marginTop: 20, padding: '10px 24px',
              background: 'linear-gradient(135deg, #2d6a4f, #27ae60)',
              color: '#fff', borderRadius: 12, fontWeight: 600, fontSize: 14,
              textDecoration: 'none',
            }}
          >
            {t('emailAuth.resend')}
          </Link>
        )}
      </div>
    </div>
  );
}
