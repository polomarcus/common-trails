// @vitest-environment jsdom
/**
 * « Partager ma heatmap » (docs/ma-heatmap-fichier-prive.md §10): opt-in panel
 * in the Calques menu. Driven through the REAL component + I18nProvider with
 * the API module mocked (no network).
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const api = vi.hoisted(() => ({
  status: { available: true, active: false } as Record<string, unknown>,
  create: vi.fn(),
  revoke: vi.fn(),
}));
vi.mock('@/lib/my-heatmap-share', async (orig) => {
  const real = await orig<typeof import('@/lib/my-heatmap-share')>();
  return {
    ...real,
    fetchShareStatus: () => Promise.resolve(api.status),
    createOrRegenerateShare: api.create,
    revokeShare: api.revoke,
  };
});

import MyHeatmapShare from '@/components/map/MyHeatmapShare';
import { I18nProvider } from '@/lib/i18n';
import { formatShareDate } from '@/lib/my-heatmap-share';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const URLS = {
  style: 'https://example.org/s/TOKEN_A/style.json',
  raster: 'https://example.org/s/TOKEN_A/{z}/{x}/{y}.png',
  vector: 'https://example.org/s/TOKEN_A/{z}/{x}/{y}.pbf',
  pmtiles: 'https://example.org/s/TOKEN_A/heatmap.pmtiles',
};

function q(c: HTMLElement, id: string): HTMLElement | null {
  return c.querySelector(`[data-testid="${id}"]`);
}

async function click(el: HTMLElement | null) {
  if (!el) throw new Error('missing element');
  await act(async () => { el.click(); });
}

describe('MyHeatmapShare', () => {
  let container: HTMLDivElement;
  let root: Root;

  async function mount() {
    await act(async () => {
      root = createRoot(container);
      root.render(<I18nProvider><MyHeatmapShare dateLocale="fr-FR" /></I18nProvider>);
    });
  }

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    api.status = { available: true, active: false };
    api.create.mockReset();
    api.revoke.mockReset();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it('renders nothing when the feature is off for the account', async () => {
    api.status = { available: false, active: false };
    await mount();
    expect(q(container, 'my-heatmap-share')).toBeNull();
  });

  it('off by default: one collapsed line, then the create button with the privacy note', async () => {
    await mount();
    expect(q(container, 'my-heatmap-share-toggle')?.textContent).toContain('Partager ma heatmap');
    expect(q(container, 'my-heatmap-share-create')).toBeNull();
    await click(q(container, 'my-heatmap-share-toggle'));
    expect(container.textContent).toContain('gpx.studio');
    expect(container.textContent).toContain('Strava');
    expect(api.create).not.toHaveBeenCalled();
  });

  it('create → shows the copyable style + raster URLs and the gpx.studio hint, once', async () => {
    api.create.mockResolvedValue({ active: true, created_at: '2026-11-10T19:00:00Z', urls: URLS });
    await mount();
    await click(q(container, 'my-heatmap-share-toggle'));
    await click(q(container, 'my-heatmap-share-create'));
    expect(api.create).toHaveBeenCalledTimes(1);
    expect((q(container, 'my-heatmap-share-style') as HTMLInputElement).value).toBe(URLS.style);
    expect((q(container, 'my-heatmap-share-raster') as HTMLInputElement).value).toBe(URLS.raster);
    expect(container.textContent).toContain('lien créé le 10 novembre 2026');
    expect(container.textContent).toContain('calques personnalisés');
  });

  // Non-régression (Paul, 2026-11-11) : la création prend ~30 s et le bouton
  // ne donnait aucun retour.
  it('while the link is being created the button shows a spinner and is disabled', async () => {
    let finish!: (v: unknown) => void;
    api.create.mockReturnValue(new Promise((r) => { finish = r; }));
    await mount();
    await click(q(container, 'my-heatmap-share-toggle'));
    await click(q(container, 'my-heatmap-share-create'));
    const b = q(container, 'my-heatmap-share-create') as HTMLButtonElement;
    expect(b.disabled).toBe(true);
    expect(b.getAttribute('aria-busy')).toBe('true');
    expect(q(container, 'my-heatmap-share-spinner')).not.toBeNull();
    expect(b.textContent).toContain('Création du lien');
    await act(async () => { finish({ active: true, created_at: '2026-11-10T19:00:00Z', urls: URLS }); });
    expect(q(container, 'my-heatmap-share-spinner')).toBeNull();
    expect((q(container, 'my-heatmap-share-style') as HTMLInputElement).value).toBe(URLS.style);
  });

  it('active on a later visit: date shown, no link (only its hash is stored), regenerate reveals a new one', async () => {
    api.status = { available: true, active: true, created_at: '2026-11-01T10:00:00Z', ready: true, activity_count: 12 };
    api.create.mockResolvedValue({ active: true, created_at: '2026-11-10T19:00:00Z', urls: URLS });
    await mount();
    expect(q(container, 'my-heatmap-share-toggle')?.textContent).toContain('Partage actif');
    await click(q(container, 'my-heatmap-share-toggle'));
    expect(container.textContent).toContain('1 novembre 2026');
    expect(container.textContent).toContain('12');
    expect(q(container, 'my-heatmap-share-style')).toBeNull();
    await click(q(container, 'my-heatmap-share-regenerate'));
    expect(window.confirm).toHaveBeenCalled();
    expect((q(container, 'my-heatmap-share-style') as HTMLInputElement).value).toBe(URLS.style);
  });

  it('revoke → back to the off state; cancelled confirm does nothing', async () => {
    api.status = { available: true, active: true, created_at: '2026-11-01T10:00:00Z', ready: true };
    api.revoke.mockResolvedValue(undefined);
    await mount();
    await click(q(container, 'my-heatmap-share-toggle'));
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await click(q(container, 'my-heatmap-share-revoke'));
    expect(api.revoke).not.toHaveBeenCalled();
    await click(q(container, 'my-heatmap-share-revoke'));
    expect(api.revoke).toHaveBeenCalledTimes(1);
    expect(q(container, 'my-heatmap-share-toggle')?.textContent).toContain('Partager ma heatmap');
  });

  it('an API failure shows an error and keeps the state', async () => {
    api.create.mockRejectedValue(new Error('HTTP 500'));
    await mount();
    await click(q(container, 'my-heatmap-share-toggle'));
    await click(q(container, 'my-heatmap-share-create'));
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(q(container, 'my-heatmap-share-create')).not.toBeNull();
  });
});

describe('formatShareDate', () => {
  it('formats a long French date and tolerates junk', () => {
    expect(formatShareDate('2026-11-10T12:00:00Z', 'fr-FR')).toBe('10 novembre 2026');
    expect(formatShareDate(undefined, 'fr-FR')).toBe('');
    expect(formatShareDate('nope', 'fr-FR')).toBe('');
  });
});
