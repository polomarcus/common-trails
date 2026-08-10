'use client';

import { useState } from 'react';
import { useI18n } from '@/lib/i18n';
import { requestMagicLink } from '@/lib/email-auth';

/**
 * Passwordless email-login form. Email input → POST /auth/email/request →
 * "check your inbox" state. Used on the home auth panel and the /strava
 * logged-out CTA.
 *
 * `variant` tunes colors for the two surfaces:
 *  - 'dark'  (default) — on the dark home/strava cards (light text).
 *  - 'light' — on a white card.
 */
export default function EmailLoginForm({ variant = 'dark' }: { variant?: 'dark' | 'light' }) {
  const { t, locale } = useI18n();
  const [email, setEmail] = useState('');
  const [state, setState] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');

  const dark = variant === 'dark';
  const textColor = dark ? '#fff' : '#1a4731';
  const subColor = dark ? 'rgba(255,255,255,0.5)' : '#666';

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    setState('sending');
    const res = await requestMagicLink(email.trim(), locale);
    setState(res.ok ? 'sent' : 'error');
  };

  if (state === 'sent') {
    return (
      <div data-testid="email-login-sent" style={{ textAlign: 'center', padding: '4px 0' }}>
        <p style={{ color: textColor, fontSize: 15, fontWeight: 700, margin: '0 0 6px' }}>
          {t('emailAuth.sentTitle')}
        </p>
        <p style={{ color: subColor, fontSize: 13, lineHeight: 1.5, margin: '0 0 12px' }}>
          {t('emailAuth.sentBody')}
        </p>
        <button
          type="button"
          data-testid="email-login-resend"
          onClick={() => { setState('idle'); }}
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: dark ? 'rgba(255,255,255,0.7)' : '#2d6a4f',
            fontSize: 13, fontWeight: 600, textDecoration: 'underline',
          }}
        >
          {t('emailAuth.resend')}
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} data-testid="email-login-form" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <input
        type="email"
        required
        data-testid="email-login-input"
        placeholder={t('emailAuth.emailPlaceholder')}
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        style={{
          padding: '10px 12px', borderRadius: 8, fontSize: 13, outline: 'none',
          border: dark ? '1px solid rgba(255,255,255,0.15)' : '1px solid rgba(0,0,0,0.15)',
          background: dark ? 'rgba(255,255,255,0.08)' : '#fff',
          color: textColor,
        }}
      />
      {state === 'error' && (
        <p data-testid="email-login-error" style={{ color: '#ff6b6b', fontSize: 12, margin: 0 }}>
          {t('emailAuth.error')}
        </p>
      )}
      <button
        type="submit"
        disabled={state === 'sending'}
        data-testid="email-login-submit"
        style={{
          padding: '11px', border: 'none', borderRadius: 8,
          cursor: state === 'sending' ? 'not-allowed' : 'pointer',
          fontWeight: 700, fontSize: 14, opacity: state === 'sending' ? 0.7 : 1,
          background: dark ? '#fff' : '#2d6a4f',
          color: dark ? '#1a4731' : '#fff',
        }}
      >
        {state === 'sending' ? t('emailAuth.sending') : t('emailAuth.sendBtn')}
      </button>
    </form>
  );
}
