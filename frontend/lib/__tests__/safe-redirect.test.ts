/**
 * Post-login `?redirect=` handling — drives the REAL sanitizer
 * (lib/safe-redirect.ts) and the REAL requestMagicLink (lib/email-auth.ts).
 * The map's login buttons send `/?redirect=/map`; the emailed link must bring
 * the user back there, never to another origin.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { sanitizeRedirect, postLoginTarget, DEFAULT_POST_LOGIN_PATH } from '@/lib/safe-redirect';
import { requestMagicLink } from '@/lib/email-auth';

describe('sanitizeRedirect', () => {
  it.each(['/map', '/strava', '/map?activity=abc&lat=1', '/stats#records'])(
    'accepts the same-origin relative path %s', (p) => {
      expect(sanitizeRedirect(p)).toBe(p);
    },
  );

  it.each([
    '//evil.com', '///evil.com', 'https://evil.com', 'https:evil.com', 'http://localhost/x',
    'javascript:alert(1)', 'evil.com', 'map', '/\\evil.com', '\\\\evil.com',
    '/map\nSet-Cookie: x', '', null, undefined,
  ])('rejects %j and falls back to the default', (bad) => {
    expect(sanitizeRedirect(bad as string | null | undefined)).toBe(DEFAULT_POST_LOGIN_PATH);
  });

  it('the default stays /strava (current post-login landing)', () => {
    expect(DEFAULT_POST_LOGIN_PATH).toBe('/strava');
  });
});

describe('postLoginTarget (verify page)', () => {
  it("prefers the link's safe redirect", () => {
    expect(postLoginTarget('/map', '/strava')).toBe('/map');
  });
  it('ignores an unsafe link redirect and uses the server default', () => {
    expect(postLoginTarget('//evil.com', '/strava')).toBe('/strava');
    expect(postLoginTarget('https://evil.com', undefined)).toBe('/strava');
  });
  it('never trusts an unsafe server value either', () => {
    expect(postLoginTarget(null, 'https://evil.com')).toBe('/strava');
  });
});

describe('requestMagicLink — redirect + 429', () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  const ok = { ok: true, status: 200, json: async () => ({ ok: true }) } as Response;

  it('sends a safe redirect along with the email', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(ok);
    vi.stubGlobal('fetch', fetchSpy);
    await requestMagicLink('a@b.com', 'fr', '/map');
    expect(JSON.parse(fetchSpy.mock.calls[0][1].body)).toEqual({ email: 'a@b.com', locale: 'fr', redirect: '/map' });
  });

  it.each(['//evil.com', 'https://evil.com'])('drops the unsafe redirect %s', async (evil) => {
    const fetchSpy = vi.fn().mockResolvedValue(ok);
    vi.stubGlobal('fetch', fetchSpy);
    await requestMagicLink('a@b.com', 'fr', evil);
    expect(JSON.parse(fetchSpy.mock.calls[0][1].body)).toEqual({ email: 'a@b.com', locale: 'fr' });
  });

  it('flags a 429 as rate-limited (distinct from a generic failure)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 429, json: async () => ({}) }));
    expect(await requestMagicLink('a@b.com')).toEqual({ ok: false, rateLimited: true });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }));
    expect(await requestMagicLink('a@b.com')).toEqual({ ok: false });
  });
});
