// @vitest-environment jsdom
/**
 * EmailLoginForm — real component + I18nProvider, network faked:
 *  - the "sent" state names the address and says the link lasts 15 min (the
 *    account is auto-created — no "if an account exists" hedge);
 *  - a 429 shows "Trop de demandes…" instead of a fake "link sent";
 *  - the page's ?redirect= is forwarded to the request.
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import EmailLoginForm from '@/components/EmailLoginForm';
import { I18nProvider } from '@/lib/i18n';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe('EmailLoginForm', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(async () => {
    container = document.createElement('div');
    document.body.appendChild(container);
    await act(async () => {
      root = createRoot(container);
      root.render(
        <I18nProvider>
          <EmailLoginForm intro={<p data-testid="intro">Créer un compte ou se connecter</p>} />
        </I18nProvider>,
      );
    });
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
    vi.unstubAllGlobals();
    window.history.replaceState(null, '', '/');
  });

  async function submit(email: string) {
    const input = container.querySelector('[data-testid="email-login-input"]') as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, email);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const form = container.querySelector('[data-testid="email-login-form"]') as HTMLFormElement;
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
  }

  // Non-régression (Paul, 2026-11-11) : une fois le lien envoyé, le titre et le
  // pitch de la carte restaient affichés au-dessus de la confirmation.
  it('the host intro shows above the form and disappears once the link is sent', async () => {
    expect(container.querySelector('[data-testid="intro"]')).not.toBeNull();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) }));
    await submit('rider@example.com');
    expect(container.querySelector('[data-testid="email-login-sent"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="intro"]')).toBeNull();
    expect(container.textContent).not.toContain('Créer un compte ou se connecter');
  });

  it('sent state shows the address, the 15 min validity and the spam hint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) }));
    await submit('rider@example.com');
    const sent = container.querySelector('[data-testid="email-login-sent"]')!.textContent!;
    expect(sent).toContain('Lien envoyé à rider@example.com');
    expect(sent).toContain('15 min');
    expect(sent).toContain('spams');
    expect(sent).not.toContain('Si un compte existe');
  });

  it('429 → "Trop de demandes", not a fake "link sent"', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 429, json: async () => ({}) }));
    await submit('rider@example.com');
    expect(container.querySelector('[data-testid="email-login-sent"]')).toBeNull();
    expect(container.querySelector('[data-testid="email-login-error"]')!.textContent)
      .toContain('Trop de demandes, réessayez dans quelques minutes');
  });

  it("forwards the page's ?redirect= to the magic-link request", async () => {
    window.history.replaceState(null, '', '/?redirect=/map');
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    vi.stubGlobal('fetch', fetchSpy);
    await submit('rider@example.com');
    expect(JSON.parse(fetchSpy.mock.calls[0][1].body).redirect).toBe('/map');
  });
});
