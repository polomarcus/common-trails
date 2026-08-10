import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import {
  authHeaders,
  initArchiveUpload,
  completeArchiveUpload,
  putArchive,
  uploadArchive,
  ArchiveUploadError,
  type ArchiveInitResult,
} from '../strava-archive-upload';

const API = 'http://api.test';

// ── Regression: the 'cookie-auth' placeholder must NEVER be sent as a Bearer ──
// In this cookie-based app getToken() returns the literal string 'cookie-auth'
// when signed in (the real JWT lives in an httpOnly cookie). Sending
// `Authorization: Bearer cookie-auth` makes the backend prefer the bogus token
// over the valid cookie → every archive upload 401s. authHeaders() must emit a
// header ONLY for a real token.
describe('authHeaders — cookie-auth placeholder guard', () => {
  it("returns {} for the 'cookie-auth' placeholder (rely on the cookie)", () => {
    expect(authHeaders('cookie-auth')).toEqual({});
  });
  it('returns a Bearer header for a real JWT', () => {
    expect(authHeaders('real.jwt.here')).toEqual({ Authorization: 'Bearer real.jwt.here' });
  });
  it('returns {} for null', () => {
    expect(authHeaders(null)).toEqual({});
  });
});

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 422): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

const gcsInit: ArchiveInitResult = {
  archive_id: 'arch-1',
  consent_id: 'consent-1',
  bucket_key: 'archive-intake/user-1/abc.zip',
  upload_url: 'https://storage.googleapis.com/bucket/archive-intake/user-1/abc.zip?sig=deadbeef',
  upload_method: 'PUT',
  content_type: 'application/zip',
  max_bytes: 2147483648,
  storage_backend: 'gcs',
};

describe('initArchiveUpload', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('POSTs the consent payload and returns the init result', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(gcsInit));
    vi.stubGlobal('fetch', fetchMock);

    const res = await initArchiveUpload(
      { apiUrl: API, token: 'tok' },
      { sport: 'road', consent: true, consent_version: 'v1', consent_text: 'ok', locale: 'fr' },
    );

    expect(res).toEqual(gcsInit);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API}/imports/strava-archive/init`);
    expect(opts.method).toBe('POST');
    expect(opts.headers.Authorization).toBe('Bearer tok');
    expect(JSON.parse(opts.body).consent).toBe(true);
  });

  it('throws ArchiveUploadError with the server detail on 422', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ detail: 'Consent is required' }, false, 422),
    ));
    await expect(
      initArchiveUpload({ apiUrl: API, token: null },
        { sport: 'road', consent: false, consent_version: 'v1', consent_text: 'x', locale: 'fr' }),
    ).rejects.toThrow(ArchiveUploadError);
  });
});

describe('completeArchiveUpload', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('POSTs archive_id + key (from bucket_key)', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ archive_id: 'arch-1', status: 'uploaded', enqueue: 'scheduled' }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const res = await completeArchiveUpload({ apiUrl: API, token: 'tok' }, gcsInit);
    expect(res.status).toBe('uploaded');
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API}/imports/strava-archive/complete`);
    expect(JSON.parse(opts.body)).toEqual({ archive_id: 'arch-1', key: 'archive-intake/user-1/abc.zip' });
  });
});

describe('uploadArchive orchestration', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('runs init → put(signed url) → complete and reports stages', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(gcsInit)) // init
      .mockResolvedValueOnce(jsonResponse({ archive_id: 'arch-1', status: 'uploaded', enqueue: 'scheduled' })); // complete
    vi.stubGlobal('fetch', fetchMock);

    const put = vi.fn().mockResolvedValue(undefined);
    const stages: string[] = [];

    const res = await uploadArchive({
      apiUrl: API,
      token: 'tok',
      blob: new Blob(['zip']),
      payload: { sport: 'road', consent: true, consent_version: 'v1', consent_text: 'ok', locale: 'fr' },
      onStage: (s) => stages.push(s),
      put,
    });

    expect(res.status).toBe('uploaded');
    // The direct PUT received the init result (the signed URL), not the API.
    expect(put).toHaveBeenCalledTimes(1);
    expect(put.mock.calls[0][0]).toEqual(gcsInit);
    expect(stages).toEqual(['init', 'uploading', 'finalizing', 'done']);
    // init + complete = 2 fetch calls; the PUT never touches the API.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('aborts before PUT/complete when init rejects (consent 422)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ detail: 'Consent is required' }, false, 422),
    ));
    const put = vi.fn();
    await expect(uploadArchive({
      apiUrl: API, token: null, blob: new Blob(['z']),
      payload: { sport: 'road', consent: false, consent_version: 'v1', consent_text: 'x', locale: 'fr' },
      put,
    })).rejects.toThrow(ArchiveUploadError);
    expect(put).not.toHaveBeenCalled();
  });
});

describe('putArchive URL + header selection', () => {
  let sent: { method?: string; url?: string; headers: Record<string, string>; body?: unknown; withCredentials?: boolean };

  beforeEach(() => {
    sent = { headers: {} };
    class FakeXHR {
      status = 200;
      withCredentials = false;
      upload = { onprogress: null as null | ((e: ProgressEvent) => void) };
      onload: null | (() => void) = null;
      onerror: null | (() => void) = null;
      open(method: string, url: string) { sent.method = method; sent.url = url; }
      setRequestHeader(k: string, v: string) { sent.headers[k] = v; }
      send(body: unknown) { sent.body = body; sent.withCredentials = this.withCredentials; queueMicrotask(() => this.onload?.()); }
    }
    vi.stubGlobal('XMLHttpRequest', FakeXHR as unknown as typeof XMLHttpRequest);
  });
  afterEach(() => vi.restoreAllMocks());

  it('gcs → PUTs to the absolute signed URL with only Content-Type', async () => {
    await putArchive(gcsInit, new Blob(['z']), { apiUrl: API, token: 'tok' });
    expect(sent.url).toBe(gcsInit.upload_url);
    expect(sent.headers['Content-Type']).toBe('application/zip');
    expect(sent.headers.Authorization).toBeUndefined();
  });

  it('local → PUTs to the apiUrl-prefixed path with the auth header + credentials', async () => {
    const localInit: ArchiveInitResult = {
      ...gcsInit,
      storage_backend: 'local',
      upload_url: '/imports/strava-archive/upload/arch-1',
    };
    await putArchive(localInit, new Blob(['z']), { apiUrl: API, token: 'tok' });
    expect(sent.url).toBe(`${API}/imports/strava-archive/upload/arch-1`);
    expect(sent.headers.Authorization).toBe('Bearer tok');
    expect(sent.withCredentials).toBe(true);
  });

  it("local + 'cookie-auth' placeholder → NO Authorization, relies on the cookie", async () => {
    const localInit: ArchiveInitResult = {
      ...gcsInit,
      storage_backend: 'local',
      upload_url: '/imports/strava-archive/upload/arch-1',
    };
    await putArchive(localInit, new Blob(['z']), { apiUrl: API, token: 'cookie-auth' });
    // The placeholder must never leak into a Bearer; the httpOnly cookie
    // (withCredentials) is the sole credential.
    expect(sent.headers.Authorization).toBeUndefined();
    expect(sent.withCredentials).toBe(true);
  });
});
