'use client';

import { useState } from 'react';
import { useI18n } from '@/lib/i18n';
import { setAccountEmail } from '@/lib/email-auth';

/**
 * "Add your email" prompt — shown to an account whose email is still synthetic
 * (strava_<id>@strava.local), i.e. a pure Strava-OAuth user who can't yet log in
 * by email or receive notifications (account unification).
 *
 * Submits to POST /auth/me/email, which mails a confirmation link to the NEW
 * address and finalizes only on click. So a success here means "check your
 * inbox", not "email changed". A 409 (address already owned by another account)
 * is surfaced as a distinct, non-scary message that points to the email login.
 */
export default function AddEmailPrompt() {
  const { t, locale } = useI18n();
  const [email, setEmail] = useState('');
  const [state, setState] = useState<'idle' | 'sending' | 'sent' | 'conflict' | 'error'>('idle');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    setState('sending');
    const res = await setAccountEmail(email.trim(), locale);
    if (res.ok) setState('sent');
    else if (res.status === 409) setState('conflict');
    else setState('error');
  };

  if (state === 'sent') {
    return (
      <div
        data-testid="add-email-sent"
        style={{
          background: 'rgba(46,204,113,0.1)', border: '1px solid rgba(46,204,113,0.2)',
          borderRadius: 12, padding: '14px 16px', marginBottom: 16, textAlign: 'center',
        }}
      >
        <p style={{ color: '#2ecc71', fontSize: 14, fontWeight: 700, margin: '0 0 4px' }}>
          {t('accountEmail.sentTitle')}
        </p>
        <p style={{ color: 'rgba(255,255,255,0.55)', fontSize: 12, lineHeight: 1.5, margin: 0 }}>
          {t('accountEmail.sentBody')}
        </p>
      </div>
    );
  }

  return (
    <div
      data-testid="add-email-prompt"
      style={{
        background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: 12, padding: '14px 16px', marginBottom: 16,
      }}
    >
      <p style={{ color: '#fff', fontSize: 14, fontWeight: 700, margin: '0 0 2px' }}>
        {t('accountEmail.bannerTitle')}
      </p>
      <p style={{ color: 'rgba(255,255,255,0.45)', fontSize: 12, lineHeight: 1.5, margin: '0 0 10px' }}>
        {t('accountEmail.bannerBody')}
      </p>
      <form onSubmit={submit} data-testid="add-email-form" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <input
          type="email"
          required
          data-testid="add-email-input"
          placeholder={t('accountEmail.placeholder')}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          style={{
            padding: '10px 12px', borderRadius: 8, fontSize: 13, outline: 'none',
            border: '1px solid rgba(255,255,255,0.15)', background: 'rgba(255,255,255,0.08)',
            color: '#fff',
          }}
        />
        {state === 'conflict' && (
          <p data-testid="add-email-conflict" style={{ color: '#ffb020', fontSize: 12, margin: 0, lineHeight: 1.4 }}>
            {t('accountEmail.conflict')}
          </p>
        )}
        {state === 'error' && (
          <p data-testid="add-email-error" style={{ color: '#ff6b6b', fontSize: 12, margin: 0 }}>
            {t('accountEmail.error')}
          </p>
        )}
        <button
          type="submit"
          disabled={state === 'sending'}
          data-testid="add-email-submit"
          style={{
            padding: '10px', border: 'none', borderRadius: 8,
            cursor: state === 'sending' ? 'not-allowed' : 'pointer',
            fontWeight: 700, fontSize: 13, opacity: state === 'sending' ? 0.7 : 1,
            background: '#fff', color: '#1a4731',
          }}
        >
          {state === 'sending' ? t('accountEmail.sending') : t('accountEmail.submit')}
        </button>
      </form>
    </div>
  );
}
