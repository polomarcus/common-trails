// @vitest-environment jsdom
/**
 * useUserMenu — a session ended server-side (signed out on another device,
 * email changed) makes /auth/me answer 401: the browser forgets the account
 * and reloads, instead of showing a signed-in UI whose every call fails.
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useUserMenu } from '@/hooks/useUserMenu';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function Probe() {
  const { userEmail } = useUserMenu();
  return React.createElement('span', { 'data-testid': 'email' }, userEmail ?? '');
}

describe('useUserMenu session reconciliation', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;
  const reload = vi.fn();

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    localStorage.setItem('user_id', 'u-1');
    reload.mockReset();
    Object.defineProperty(window, 'location', { configurable: true, value: { ...window.location, reload } });
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  const mount = async () => {
    root = createRoot(container);
    await act(async () => { root!.render(React.createElement(Probe)); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  };

  it('a 401 from /auth/me forgets the account and reloads', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"Invalid or expired token"}', { status: 401 })));
    await mount();
    expect(localStorage.getItem('user_id')).toBeNull();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('a valid session keeps the account and shows the email', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ email: 'a@b.c', is_admin: false }), { status: 200 })));
    await mount();
    expect(localStorage.getItem('user_id')).toBe('u-1');
    expect(reload).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="email"]')!.textContent).toBe('a@b.c');
  });

  it('a server error (5xx) is not treated as a signed-out session', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('boom', { status: 503 })));
    await mount();
    expect(localStorage.getItem('user_id')).toBe('u-1');
    expect(reload).not.toHaveBeenCalled();
  });
});
