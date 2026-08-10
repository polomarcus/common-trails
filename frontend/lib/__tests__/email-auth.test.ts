/**
 * Unit tests for the passwordless magic-link network logic (lib/email-auth.ts).
 * Drives the REAL exported functions with a mocked global fetch.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  requestMagicLink,
  verifyMagicLink,
  setAccountEmail,
  confirmEmailChange,
} from '@/lib/email-auth';

const okResponse = (body: unknown = {}) => ({
  ok: true,
  status: 200,
  json: async () => body,
}) as Response;

const errResponse = (status = 401) => ({
  ok: false,
  status,
  json: async () => ({ detail: 'nope' }),
}) as Response;

describe('requestMagicLink', () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it('POSTs the email + locale and resolves ok on 2xx', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(okResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchSpy);

    const res = await requestMagicLink('a@b.com', 'fr');
    expect(res.ok).toBe(true);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toMatch(/\/auth\/email\/request$/);
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(JSON.parse(init.body)).toEqual({ email: 'a@b.com', locale: 'fr' });
  });

  it('resolves ok:false on a network error (no throw)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    const res = await requestMagicLink('a@b.com');
    expect(res.ok).toBe(false);
  });

  it('resolves ok:false on a non-2xx response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(errResponse(429)));
    const res = await requestMagicLink('a@b.com');
    expect(res.ok).toBe(false);
  });
});

describe('verifyMagicLink', () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it('returns the user + redirect on success', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      okResponse({ ok: true, user_id: 'u1', email: 'a@b.com', redirect: '/map' }),
    );
    vi.stubGlobal('fetch', fetchSpy);

    const res = await verifyMagicLink('tok123');
    expect(res).toEqual({ ok: true, userId: 'u1', email: 'a@b.com', redirect: '/map' });
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toMatch(/\/auth\/email\/verify$/);
    expect(JSON.parse(init.body)).toEqual({ token: 'tok123' });
  });

  it('defaults redirect to /strava when the backend omits it', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      okResponse({ ok: true, user_id: 'u1', email: 'a@b.com' }),
    ));
    const res = await verifyMagicLink('tok');
    expect(res.redirect).toBe('/strava');
  });

  it('returns ok:false on a 401 (invalid / reused token)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(errResponse(401)));
    const res = await verifyMagicLink('bad');
    expect(res.ok).toBe(false);
    expect(res.userId).toBeUndefined();
  });

  it('returns ok:false on a network error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    const res = await verifyMagicLink('tok');
    expect(res.ok).toBe(false);
  });
});

describe('setAccountEmail', () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it('POSTs the email + locale and returns pending on 200', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      okResponse({ ok: true, pending: true, email: 'new@b.com' }),
    );
    vi.stubGlobal('fetch', fetchSpy);

    const res = await setAccountEmail('new@b.com', 'fr');
    expect(res).toEqual({ ok: true, status: 200, pending: true, email: 'new@b.com' });
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toMatch(/\/auth\/me\/email$/);
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(JSON.parse(init.body)).toEqual({ email: 'new@b.com', locale: 'fr' });
  });

  it('surfaces the 409 status (email owned by another account)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(errResponse(409)));
    const res = await setAccountEmail('taken@b.com');
    expect(res.ok).toBe(false);
    expect(res.status).toBe(409);
  });

  it('returns ok:false, status 0 on a network error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    const res = await setAccountEmail('a@b.com');
    expect(res).toEqual({ ok: false, status: 0 });
  });
});

describe('confirmEmailChange', () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it('returns the finalized email on success', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      okResponse({ ok: true, user_id: 'u1', email: 'new@b.com' }),
    );
    vi.stubGlobal('fetch', fetchSpy);

    const res = await confirmEmailChange('tok');
    expect(res).toEqual({ ok: true, status: 200, email: 'new@b.com' });
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toMatch(/\/auth\/me\/email\/confirm$/);
    expect(JSON.parse(init.body)).toEqual({ token: 'tok' });
  });

  it('returns ok:false on a 401 (invalid / reused link)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(errResponse(401)));
    const res = await confirmEmailChange('bad');
    expect(res.ok).toBe(false);
    expect(res.status).toBe(401);
  });
});
