// @vitest-environment jsdom
/**
 * /auth/confirm-email — REAL page + I18nProvider, network faked (and next/link,
 * which needs the Next runtime). Confirming an email change opens no session
 * and ends the account's existing ones, so the page:
 *  - forgets the account in this browser when it is the one signed in here
 *    (another account signed in here is left alone);
 *  - asks to log in with the new address and shows the email login form;
 *  - on an invalid link shows the error and no login form.
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) =>
    React.createElement('a', { href, ...rest }, children),
}));
// The page does not navigate on its own any more; the router is faked so a
// page that did would still render here and be judged on what it shows.
const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));

import ConfirmEmailPage from '@/app/auth/confirm-email/page';
import { I18nProvider } from '@/lib/i18n';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe('ConfirmEmailPage', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    window.history.replaceState(null, '', '/auth/confirm-email?token=tok');
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
    vi.unstubAllGlobals();
    localStorage.clear();
    window.history.replaceState(null, '', '/');
  });

  async function renderWith(response: { ok: boolean; status: number; body?: unknown }) {
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: response.ok, status: response.status, json: async () => response.body ?? {},
    });
    vi.stubGlobal('fetch', fetchSpy);
    await act(async () => {
      root = createRoot(container);
      root.render(<I18nProvider><ConfirmEmailPage /></I18nProvider>);
    });
    await act(async () => { await Promise.resolve(); });
    return fetchSpy;
  }

  const status = () => container.querySelector('[data-testid="confirm-email-status"]')!.textContent;

  it('success: forgets the account signed in here and offers the email login', async () => {
    localStorage.setItem('user_id', 'u1');
    localStorage.setItem('strava_name', 'Rider');
    const fetchSpy = await renderWith({
      ok: true, status: 200, body: { ok: true, user_id: 'u1', email: 'new@example.com' },
    });

    expect(JSON.parse(fetchSpy.mock.calls[0][1].body)).toEqual({ token: 'tok' });
    expect(localStorage.getItem('user_id')).toBeNull();
    expect(localStorage.getItem('strava_name')).toBeNull();
    expect(status()).toContain('Connecte-toi avec ta nouvelle adresse');
    expect(container.querySelector('[data-testid="confirm-email-login"] [data-testid="email-login-input"]'))
      .not.toBeNull();
    await act(async () => { await new Promise((r) => setTimeout(r, 1500)); });
    expect(push).not.toHaveBeenCalled();
  });

  it('success for another account: the session signed in here is kept', async () => {
    localStorage.setItem('user_id', 'someone-else');
    await renderWith({
      ok: true, status: 200, body: { ok: true, user_id: 'u1', email: 'new@example.com' },
    });
    expect(localStorage.getItem('user_id')).toBe('someone-else');
    expect(status()).toContain('Email confirmé');
  });

  it('invalid link: error, back link, no login form, nothing forgotten', async () => {
    localStorage.setItem('user_id', 'u1');
    await renderWith({ ok: false, status: 401 });
    expect(status()).toContain('invalide ou a expiré');
    expect(container.querySelector('[data-testid="confirm-email-back"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="confirm-email-login"]')).toBeNull();
    expect(localStorage.getItem('user_id')).toBe('u1');
  });
});
