// @vitest-environment jsdom
/**
 * Logged-out visitors used to fire `GET /me/notifications` on every page → 401
 * + a red console line. The REAL NotificationToasts now checks auth after
 * mount and skips the call when anonymous.
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';

const h = vi.hoisted(() => ({ authed: false, fetchSafe: vi.fn(async (_path: string) => ({ items: [], unread_count: 0 })) }));
vi.mock('@/lib/auth', () => ({ isAuthenticated: () => h.authed, getToken: () => (h.authed ? 'cookie-auth' : null) }));
vi.mock('@/lib/api-client', () => ({ apiFetchSafe: h.fetchSafe, apiFetch: vi.fn() }));

import NotificationToasts from '@/components/NotificationToasts';
import { I18nProvider } from '@/lib/i18n';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe('NotificationToasts — no API call when logged out', () => {
  let root: Root | null = null;
  const container = document.createElement('div');
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    h.fetchSafe.mockClear();
  });
  const mount = async () => {
    await act(async () => {
      root = createRoot(container);
      root.render(<I18nProvider><NotificationToasts /></I18nProvider>);
    });
  };

  it('does not call /me/notifications for an anonymous visitor', async () => {
    h.authed = false;
    await mount();
    expect(h.fetchSafe).not.toHaveBeenCalled();
  });

  it('polls once for a signed-in rider', async () => {
    h.authed = true;
    await mount();
    expect(h.fetchSafe).toHaveBeenCalledTimes(1);
    expect(h.fetchSafe.mock.calls[0][0]).toContain('/me/notifications');
  });
});
