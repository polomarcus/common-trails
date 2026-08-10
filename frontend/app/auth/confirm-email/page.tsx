'use client';

import { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useI18n } from '@/lib/i18n';
import { confirmEmailChange } from '@/lib/email-auth';

/**
 * Email-change confirmation landing page. Reads ?token from the URL
 * (client-side, static-export-safe), POSTs /auth/me/email/confirm, and on
 * success shows a confirmation + redirects to /strava. On failure (expired /
 * reused link, or the address got claimed → 409) shows a friendly error.
 */
export default function ConfirmEmailPage() {
  const { t } = useI18n();
  const router = useRouter();
  const [state, setState] = useState<'verifying' | 'success' | 'error' | 'missing'>('verifying');
  // Guard against React 18 StrictMode double-invoke consuming the single-use
  // token twice (the second call would fail and flip success to error).
  const ranRef = useRef(false);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;

    const token = new URLSearchParams(window.location.search).get('token');
    if (!token) {
      setState('missing');
      return;
    }

    confirmEmailChange(token).then((res) => {
      if (res.ok) {
        setState('success');
        setTimeout(() => router.push('/strava'), 1200);
      } else {
        setState('error');
      }
    });
  }, [router]);

  const message =
    state === 'verifying' ? t('accountEmail.confirming')
      : state === 'success' ? t('accountEmail.confirmSuccess')
        : state === 'missing' ? t('accountEmail.confirmMissingToken')
          : t('accountEmail.confirmError');

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
          data-testid="confirm-email-status"
          style={{ color: isError ? '#ff6b6b' : '#fff', fontSize: 16, fontWeight: 600, margin: 0 }}
        >
          {message}
        </p>
        {isError && (
          <Link
            href="/strava"
            data-testid="confirm-email-back"
            style={{
              display: 'inline-block', marginTop: 20, padding: '10px 24px',
              background: 'linear-gradient(135deg, #2d6a4f, #27ae60)',
              color: '#fff', borderRadius: 12, fontWeight: 600, fontSize: 14,
              textDecoration: 'none',
            }}
          >
            {t('accountEmail.backToStrava')}
          </Link>
        )}
      </div>
    </div>
  );
}
