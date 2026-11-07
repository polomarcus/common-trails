// @vitest-environment jsdom
/**
 * ContributionDropzone — onboarding-friction guards, driven through the REAL
 * component + I18nProvider (only auth, next/link and the network are faked):
 *
 *  - loose GPX/FIT need an explicit sport: no preselected "VTT", the picker is
 *    disabled and a drop uploads NOTHING until a sport chip is chosen;
 *  - a too-big archive (> 2 GiB) is refused BEFORE any network call, with a
 *    translated message (not the raw English server text);
 *  - unsupported / extra dropped files are reported, never silently ignored;
 *  - a `beforeunload` guard exists only while an upload is in flight.
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('@/lib/auth', () => ({ getToken: () => 'cookie-auth' }));
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) =>
    React.createElement('a', { href, ...rest }, children),
}));

import ContributionDropzone from '@/components/ContributionDropzone';
import { I18nProvider } from '@/lib/i18n';
import { MAX_ARCHIVE_BYTES } from '@/lib/strava-archive-upload';
import { isArchivePending } from '@/lib/archive-pending';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

/** A file-like object: the dropzone only reads name/size before uploading. */
function fakeFile(name: string, size = 1000): File {
  const f = new File(['<gpx/>'], name);
  Object.defineProperty(f, 'size', { value: size });
  return f;
}

async function drop(target: Element, files: File[]) {
  const ev = new Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(ev, 'dataTransfer', { value: { files, types: ['Files'] } });
  await act(async () => { target.dispatchEvent(ev); });
}

const q = (c: HTMLElement, id: string) => c.querySelector(`[data-testid="${id}"]`);

describe('ContributionDropzone', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    fetchSpy = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({ imported: 1, skipped: 0, consent_id: 'c1' }),
    });
    vi.stubGlobal('fetch', fetchSpy);
    container = document.createElement('div');
    document.body.appendChild(container);
    await act(async () => {
      root = createRoot(container);
      root.render(<I18nProvider><ContributionDropzone /></I18nProvider>);
    });
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
    vi.unstubAllGlobals();
  });

  it('has no preselected sport: every chip is unpressed and the GPX picker is disabled with a hint', () => {
    for (const s of ['road', 'gravel', 'mtb', 'offroad', 'running']) {
      expect(q(container, `sport-chip-${s}`)!.getAttribute('aria-pressed')).toBe('false');
    }
    expect((q(container, 'files-input') as HTMLInputElement).disabled).toBe(true);
    expect(q(container, 'files-sport-hint')!.textContent).toContain('Choisissez');
  });

  it('a GPX drop without a sport uploads nothing and tells the user to pick one', async () => {
    await drop(q(container, 'files-block')!, [fakeFile('ride.gpx')]);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(q(container, 'hero-upload-error')!.textContent).toContain('sport');
  });

  it('after choosing a sport, the drop uploads with THAT sport', async () => {
    await act(async () => { (q(container, 'sport-chip-road') as HTMLButtonElement).click(); });
    expect((q(container, 'files-input') as HTMLInputElement).disabled).toBe(false);
    await drop(q(container, 'files-block')!, [fakeFile('ride.gpx')]);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const body = fetchSpy.mock.calls[0][1].body as FormData;
    expect(body.get('sport')).toBe('road');
  });

  it('reports ignored files (unsupported formats) instead of dropping them silently', async () => {
    await act(async () => { (q(container, 'sport-chip-gravel') as HTMLButtonElement).click(); });
    await drop(q(container, 'files-block')!, [fakeFile('ride.gpx'), fakeFile('notes.txt')]);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const note = q(container, 'upload-ignored-files')!.textContent!;
    expect(note).toContain('1 fichier');
    expect(note).toContain('notes.txt');
  });

  it('shows the 2 Go archive limit', () => {
    expect(q(container, 'archive-max-size')!.textContent).toContain('2 Go');
  });

  it('refuses a > 2 GiB archive BEFORE any network call, with a translated message', async () => {
    await drop(q(container, 'archive-block')!, [fakeFile('export.zip', MAX_ARCHIVE_BYTES + 1)]);
    expect(fetchSpy).not.toHaveBeenCalled();
    const err = q(container, 'hero-upload-error')!.textContent!;
    expect(err).toContain('2 Go');
    expect(err).not.toMatch(/too large/i);
  });

  it('maps a server failure to a translated message (no raw English)', async () => {
    fetchSpy.mockResolvedValue({ ok: false, status: 500, json: async () => ({ detail: 'init failed boom' }) });
    await drop(q(container, 'archive-block')!, [fakeFile('export.zip', 10_000)]);
    const err = q(container, 'hero-upload-error')!.textContent!;
    expect(err).not.toContain('boom');
    expect(err).toContain('serveur');
  });

  it('an accepted archive stamps the "processing" marker read by /map', async () => {
    localStorage.clear();
    class OkXHR {
      status = 200;
      upload = {};
      onload: null | (() => void) = null;
      onerror: null | (() => void) = null;
      open() {}
      setRequestHeader() {}
      send() { queueMicrotask(() => this.onload?.()); }
    }
    vi.stubGlobal('XMLHttpRequest', OkXHR as unknown as typeof XMLHttpRequest);
    fetchSpy
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({
        archive_id: 'a1', consent_id: 'c1', bucket_key: 'k', upload_url: 'https://gcs/put',
        upload_method: 'PUT', content_type: 'application/zip', max_bytes: 1, storage_backend: 'gcs',
      }) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ archive_id: 'a1', status: 'uploaded', enqueue: 'ok' }) });
    expect(isArchivePending()).toBe(false);
    await drop(q(container, 'archive-block')!, [fakeFile('export.zip', 10_000)]);
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(q(container, 'archive-see-map')).not.toBeNull();
    expect(isArchivePending()).toBe(true);
  });

  it('an archive init never carries a blanket sport (not even a chosen chip)', async () => {
    fetchSpy.mockResolvedValue({ ok: false, status: 500, json: async () => ({}) });
    // No chip: the old client silently sent sport='mtb' for the whole archive.
    await drop(q(container, 'archive-block')!, [fakeFile('export.zip', 10_000)]);
    // A chip chosen for loose files must not leak into an archive either.
    await act(async () => { (q(container, 'sport-chip-road') as HTMLButtonElement).click(); });
    await drop(q(container, 'archive-block')!, [fakeFile('export.zip', 10_000)]);
    const inits = fetchSpy.mock.calls.filter(([url]) => String(url).endsWith('/imports/strava-archive/init'));
    expect(inits.length).toBe(2);
    for (const [, init] of inits) {
      expect(JSON.parse(String(init.body))).not.toHaveProperty('sport');
    }
  });

  it('warns on beforeunload only while an upload is in flight', async () => {
    const unload = () => {
      const ev = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(ev);
      return ev.defaultPrevented;
    };
    expect(unload()).toBe(false);

    let release: (v: unknown) => void = () => {};
    fetchSpy.mockReturnValue(new Promise((r) => { release = r; }));
    await drop(q(container, 'archive-block')!, [fakeFile('export.zip', 10_000)]);
    expect(unload()).toBe(true); // init request pending → upload in flight

    await act(async () => {
      release({ ok: false, status: 500, json: async () => ({}) });
    });
    expect(unload()).toBe(false); // listener removed once the upload ended
  });
});
