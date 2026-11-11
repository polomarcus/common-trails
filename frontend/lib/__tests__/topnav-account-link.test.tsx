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
});
