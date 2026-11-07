// @vitest-environment jsdom
/**
 * Export modal keyboard contract (frontend audit 2026-11-07, P0 #5): focus
 * stayed on <body>, Tab walked the map behind the aria-modal dialog, Escape did
 * nothing. Drives the REAL ExportHeatmapModal (signed-in and anonymous views).
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const auth = vi.hoisted(() => ({ userId: 'u1' as string | null }));
vi.mock('@/lib/auth', () => ({
  getCurrentUserId: () => auth.userId,
  getToken: () => (auth.userId ? 'cookie-auth' : null),
  isAuthenticated: () => !!auth.userId,
}));
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) =>
    React.createElement('a', { href, ...rest }, children),
}));

import ExportHeatmapModal from '@/components/ExportHeatmapModal';
import { I18nProvider } from '@/lib/i18n';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const press = (key: string, shiftKey = false) => {
  act(() => {
    document.activeElement?.dispatchEvent(
      new KeyboardEvent('keydown', { key, shiftKey, bubbles: true, cancelable: true }),
    );
  });
};

describe('ExportHeatmapModal — focus management', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;
  let opener: HTMLButtonElement;
  let layersBtn: HTMLButtonElement;

  beforeEach(() => {
    auth.userId = 'u1';
    opener = document.createElement('button');
    opener.textContent = 'opener';
    layersBtn = document.createElement('button');
    layersBtn.textContent = 'Calques';
    document.body.append(opener, layersBtn);
    container = document.createElement('div');
    document.body.appendChild(container);
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
    opener.remove();
    layersBtn.remove();
  });

  const open = async (onClose = vi.fn()) => {
    await act(async () => {
      root = createRoot(container);
      root.render(
        <I18nProvider>
          <ExportHeatmapModal onClose={onClose} returnFocusTo={() => layersBtn} />
        </I18nProvider>,
      );
    });
    return onClose;
  };
  const dialog = () => container.querySelector('[role="dialog"]') as HTMLElement;
  const focusables = () =>
    Array.from(dialog().querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled])'));

  it('moves focus into the dialog on open', async () => {
    opener.focus();
    await open();
    expect(dialog().contains(document.activeElement)).toBe(true);
    expect(document.activeElement).toBe(focusables()[0]);
  });

  it('traps Tab and Shift+Tab inside the dialog', async () => {
    await open();
    const items = focusables();
    const last = items[items.length - 1];
    last.focus();
    press('Tab');
    expect(document.activeElement).toBe(items[0]);
    press('Tab', true);
    expect(document.activeElement).toBe(last);
  });

  it('pulls focus back in when Tab is pressed from outside the dialog', async () => {
    await open();
    opener.focus();
    press('Tab');
    expect(dialog().contains(document.activeElement)).toBe(true);
  });

  it('closes on Escape', async () => {
    const onClose = await open();
    press('Escape');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('returns focus to the opener on close', async () => {
    opener.focus();
    await open();
    act(() => { root?.unmount(); root = null; });
    expect(document.activeElement).toBe(opener);
  });

  it('falls back to returnFocusTo when the opener is gone (menu item unmounted)', async () => {
    opener.focus();
    await open();
    opener.remove();
    act(() => { root?.unmount(); root = null; });
    expect(document.activeElement).toBe(layersBtn);
  });

  it('applies to the anonymous login-gate view too', async () => {
    auth.userId = null;
    const onClose = await open();
    expect(dialog().contains(document.activeElement)).toBe(true);
    press('Escape');
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
