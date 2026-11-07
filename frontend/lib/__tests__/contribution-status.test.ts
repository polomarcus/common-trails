import { describe, it, expect, vi, afterEach } from 'vitest';

import { fetchContributionStatus } from '../contribution-status';

const API = 'http://api.test';

function okJson(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// Regression: /strava sent `Authorization: Bearer cookie-auth` for magic-link
// (cookie) sessions → the backend preferred the bogus bearer → 401 → the
// "already contributed" panel never showed.
describe('fetchContributionStatus', () => {
  it("cookie session ('cookie-auth' placeholder) → NO Authorization header, cookie included", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      okJson({ contributed: true, count: 3, last_contribution_at: '2026-11-01T10:00:00Z' }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const s = await fetchContributionStatus(API, 'cookie-auth');

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API}/me/contribution-status`);
    expect(opts.credentials).toBe('include');
    expect(opts.headers.Authorization).toBeUndefined();
    expect(s).toEqual({ contributed: true, count: 3, last: '2026-11-01T10:00:00Z' });
  });

  it('real token → Bearer header', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson({ contributed: false, count: 0, last_contribution_at: null }));
    vi.stubGlobal('fetch', fetchMock);

    await fetchContributionStatus(API, 'real.jwt');

    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer real.jwt');
  });

  it('non-2xx or network error → null (panel stays hidden, never throws)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 } as Response));
    expect(await fetchContributionStatus(API, 'cookie-auth')).toBeNull();

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    expect(await fetchContributionStatus(API, 'cookie-auth')).toBeNull();
  });
});
