// @vitest-environment jsdom
/**
 * Non-régression (Paul, 2026-11-11) : depuis l'accueil, « Mon compte » envoyait
 * un utilisateur connecté sur la page d'import (/strava). Il doit arriver sur
 * sa carte (/map) ; déconnecté, le bouton « Se connecter » reste sur l'accueil.
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) =>
    React.createElement('a', { href, ...rest }, children),
}));
vi.mock('next/navigation', () => ({ usePathname: () => '/' }));

import TopNav from '@/components/TopNav';
import { I18nProvider } from '@/lib/i18n';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe('TopNav — lien compte', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(() => {
    localStorage.clear();
    window.matchMedia = vi.fn().mockReturnValue({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
      addListener: vi.fn(), removeListener: vi.fn(),
    }) as unknown as typeof window.matchMedia;
    container = document.createElement('div');
    document.body.appendChild(container);
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
  });

  const accountHref = () => {
    act(() => {
      root = createRoot(container);
      root.render(<I18nProvider><TopNav /></I18nProvider>);
    });
    return container.querySelector('[data-testid="topnav-account"]')?.getAttribute('href');
  };

  it('connecté → /map (jamais la page d\'import)', () => {
    localStorage.setItem('user_id', 'u-1');
    expect(accountHref()).toBe('/map');
  });

  it('déconnecté → accueil (formulaire de connexion)', () => {
    expect(accountHref()).toBe('/');
  });

  // Non-régression (Paul, 2026-11-11) : sur l'accueil, « Se connecter » pointait
  // vers la page courante et ne faisait rien. Il doit amener au champ email.
  it('déconnecté, formulaire présent sur la page → le clic place le curseur dans le champ email', () => {
    const input = document.createElement('input');
    input.setAttribute('data-testid', 'email-login-input');
    document.body.appendChild(input);
    accountHref();
    const link = container.querySelector('[data-testid="topnav-account"]') as HTMLAnchorElement;
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    act(() => { link.dispatchEvent(click); });
    expect(click.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(input);
    input.remove();
  });

  it('déconnecté, pas de formulaire sur la page → le lien vers l\'accueil est suivi', () => {
    accountHref();
    const link = container.querySelector('[data-testid="topnav-account"]') as HTMLAnchorElement;
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    act(() => { link.dispatchEvent(click); });
    expect(click.defaultPrevented).toBe(false);
  });
});
