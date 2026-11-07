/**
 * Resumable (chunked) archive upload — driven against a FAKE GCS resumable
 * session that implements the real protocol: `Content-Range: bytes s-e/T`
 * chunks, `bytes *\/T` status queries, 308 + `Range: bytes=0-N` for what it
 * actually persisted, 200 once complete. Faults are scripted per call. The
 * fake REJECTS a chunk that starts past its persisted offset (as GCS does), so
 * a client that resumes from its own byte count instead of the server's fails.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import {
  uploadResumable,
  uploadArchive,
  parseRangeOffset,
  RESUMABLE_CHUNK_BYTES,
  RESUME_STORAGE_KEY,
  type ChunkRequest,
  type ChunkResponse,
  type ArchiveInitResult,
} from '../strava-archive-upload';

const API = 'http://api.test';
const KIB = 1024;
const CHUNK = 256 * KIB; // smallest legal GCS chunk — keeps fixtures small

type Fault =
  | { kind: 'net' }                         // connection drops, nothing persisted
  | { kind: 'netAfter'; persist: number }   // drops AFTER persisting `persist` bytes of the chunk
  | { kind: 'status'; code: number }        // e.g. 503 / 429 / 404
  | { kind: 'short'; persist: number };     // 308 but only `persist` bytes of the chunk kept

class FakeSession {
  persisted = 0;
  complete = false;
  data: Uint8Array;
  calls: { contentRange: string; bodySize: number | null }[] = [];
  faults = new Map<number, Fault>();
  failAllFrom = Infinity; // every call index >= this drops the connection

  constructor(public total: number) {
    this.data = new Uint8Array(total);
  }

  private reply(): ChunkResponse {
    if (this.complete) return { status: 200, range: null };
    return { status: 308, range: this.persisted > 0 ? `bytes=0-${this.persisted - 1}` : null };
  }

  private async keep(body: Blob, start: number, n: number) {
    const bytes = new Uint8Array(await body.arrayBuffer()).subarray(0, n);
    this.data.set(bytes, start);
    this.persisted = Math.max(this.persisted, start + bytes.length);
    if (this.persisted === this.total) this.complete = true;
  }

  transport = async (req: ChunkRequest): Promise<ChunkResponse> => {
    const idx = this.calls.length;
    const cr = req.headers['Content-Range'];
    this.calls.push({ contentRange: cr, bodySize: req.body ? req.body.size : null });
    if (idx >= this.failAllFrom) throw new Error('offline');
    const fault = this.faults.get(idx);
    if (fault?.kind === 'net') throw new Error('connection reset');
    if (fault?.kind === 'status') return { status: fault.code, range: null };

    const statusQuery = /^bytes \*\/(\d+)$/.exec(cr);
    if (statusQuery) {
      expect(req.body).toBeNull();
      return this.reply();
    }
    const m = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(cr);
    if (!m || !req.body) throw new Error(`bad request ${cr}`);
    const [start, end, total] = [Number(m[1]), Number(m[2]), Number(m[3])];
    expect(total).toBe(this.total);
    expect(end - start + 1).toBe(req.body.size);
    // GCS refuses a gap: a chunk must start at (or before) the persisted offset.
    if (start > this.persisted) return { status: 400, range: null };
    // Non-final chunks must be a multiple of 256 KiB.
    if (end + 1 < total) expect(req.body.size % CHUNK).toBe(0);
    req.onProgress?.(req.body.size); // the browser believes it sent it all
    if (fault?.kind === 'netAfter') {
      await this.keep(req.body, start, fault.persist);
      throw new Error('connection reset mid-chunk');
    }
    await this.keep(req.body, start, fault?.kind === 'short' ? fault.persist : req.body.size);
    return this.reply();
  };
}

function patterned(n: number): Uint8Array {
  const a = new Uint8Array(n);
  for (let i = 0; i < n; i++) a[i] = (i * 31 + 7) & 0xff;
  return a;
}

function zipFile(n: number, lastModified = 1_700_000_000_000, name = 'export_123.zip'): File {
  return new File([patterned(n)], name, { lastModified, type: 'application/zip' });
}

function expectMonotonic(values: number[]) {
  for (let i = 1; i < values.length; i++) expect(values[i]).toBeGreaterThanOrEqual(values[i - 1]);
}

const noSleep = vi.fn(async (_ms: number) => {});

describe('uploadResumable — chunk protocol against a fake GCS session', () => {
  beforeEach(() => noSleep.mockClear());

  it('chunk size is a multiple of 256 KiB (GCS requirement)', () => {
    expect(RESUMABLE_CHUNK_BYTES % (256 * KIB)).toBe(0);
  });

  it('parses the 308 Range header into the persisted offset', () => {
    expect(parseRangeOffset('bytes=0-1048575')).toBe(1048576);
    expect(parseRangeOffset('bytes=0-0')).toBe(1);
    expect(parseRangeOffset(null)).toBe(0); // no Range = nothing persisted yet
  });

  it('happy path: multi-chunk Content-Range sequence, final partial chunk, bytes intact', async () => {
    const total = 2 * CHUNK + 100 * KIB;
    const blob = zipFile(total);
    const s = new FakeSession(total);
    const progress: number[] = [];
    await uploadResumable('https://session', blob, {
      transport: s.transport, sleep: noSleep, chunkBytes: CHUNK, onProgress: (f) => progress.push(f),
    });
    expect(s.calls.map((c) => c.contentRange)).toEqual([
      `bytes 0-${CHUNK - 1}/${total}`,
      `bytes ${CHUNK}-${2 * CHUNK - 1}/${total}`,
      `bytes ${2 * CHUNK}-${total - 1}/${total}`, // final chunk: short, ends at total-1
    ]);
    expect(s.complete).toBe(true);
    expect(s.data).toEqual(patterned(total));
    expectMonotonic(progress);
    expect(progress.at(-1)).toBe(1);
    expect(noSleep).not.toHaveBeenCalled();
  });

  it('final chunk of an exact multiple ends at total-1 and the 200 completes', async () => {
    const total = 2 * CHUNK;
    const s = new FakeSession(total);
    await uploadResumable('https://session', zipFile(total), { transport: s.transport, sleep: noSleep, chunkBytes: CHUNK });
    expect(s.calls.map((c) => c.contentRange)).toEqual([
      `bytes 0-${CHUNK - 1}/${total}`,
      `bytes ${CHUNK}-${total - 1}/${total}`,
    ]);
    expect(s.data).toEqual(patterned(total));
  });

  it('308 with a SHORT Range: continues from what the server kept, not from what was sent', async () => {
    const total = 2 * CHUNK;
    const s = new FakeSession(total);
    s.faults.set(0, { kind: 'short', persist: 100 * KIB });
    await uploadResumable('https://session', zipFile(total), { transport: s.transport, sleep: noSleep, chunkBytes: CHUNK });
    expect(s.calls[1].contentRange).toBe(`bytes ${100 * KIB}-${100 * KIB + CHUNK - 1}/${total}`);
    expect(s.data).toEqual(patterned(total));
  });

  it('network failure mid-chunk: backs off, queries status, resumes from the SERVER offset', async () => {
    const total = 3 * CHUNK;
    const s = new FakeSession(total);
    // Chunk 2 dies after the server kept only 64 KiB of it; the browser had
    // already "sent" the whole chunk (progress said so).
    s.faults.set(1, { kind: 'netAfter', persist: 64 * KIB });
    const progress: number[] = [];
    await uploadResumable('https://session', zipFile(total), {
      transport: s.transport, sleep: noSleep, chunkBytes: CHUNK, onProgress: (f) => progress.push(f),
    });
    const resumeAt = CHUNK + 64 * KIB;
    expect(s.calls[2]).toEqual({ contentRange: `bytes */${total}`, bodySize: null }); // status query
    expect(s.calls[3].contentRange).toBe(`bytes ${resumeAt}-${resumeAt + CHUNK - 1}/${total}`);
    expect(noSleep).toHaveBeenCalledTimes(1);
    expect(s.data).toEqual(patterned(total));
    // The bar never goes backwards even though the server kept less than sent.
    expectMonotonic(progress);
    expect(progress.at(-1)).toBe(1);
  });

  it('5xx and 429 are retried with growing backoff, then resume via a status query', async () => {
    const total = 2 * CHUNK;
    const s = new FakeSession(total);
    s.faults.set(0, { kind: 'status', code: 503 });
    s.faults.set(1, { kind: 'status', code: 429 }); // the status query itself is throttled
    await uploadResumable('https://session', zipFile(total), { transport: s.transport, sleep: noSleep, chunkBytes: CHUNK });
    expect(s.calls[1].contentRange).toBe(`bytes */${total}`);
    expect(s.calls[2].contentRange).toBe(`bytes */${total}`);
    expect(s.calls[3].contentRange).toBe(`bytes 0-${CHUNK - 1}/${total}`);
    const delays = noSleep.mock.calls.map((c) => c[0]);
    expect(delays).toHaveLength(2);
    expect(delays[1]).toBeGreaterThan(delays[0] - 500); // exponential (jitter < 500 ms)
    expect(s.complete).toBe(true);
  });

  it('gives up with a network error after repeated failures without progress', async () => {
    const s = new FakeSession(CHUNK);
    s.failAllFrom = 0;
    const err = await uploadResumable('https://session', zipFile(CHUNK), {
      transport: s.transport, sleep: noSleep, chunkBytes: CHUNK,
    }).catch((e) => e);
    expect(err.code).toBe('network');
    expect(noSleep).toHaveBeenCalledTimes(8);
  });

  it('a non-retryable 4xx fails immediately', async () => {
    const s = new FakeSession(CHUNK);
    s.faults.set(0, { kind: 'status', code: 403 });
    const err = await uploadResumable('https://session', zipFile(CHUNK), {
      transport: s.transport, sleep: noSleep, chunkBytes: CHUNK,
    }).catch((e) => e);
    expect(err.code).toBe('upload_failed');
    expect(s.calls).toHaveLength(1);
  });
});

// ── Orchestration: init(size) → resumable → complete, + resume after reload ──

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 422): Response {
  return { ok, status, json: async () => body } as unknown as Response;
}

function resumableInit(id: string, session: string): ArchiveInitResult {
  return {
    archive_id: id,
    consent_id: `consent-${id}`,
    bucket_key: `archive-intake/user-1/${id}.zip`,
    upload_url: session,
    upload_method: 'PUT',
    content_type: 'application/zip',
    max_bytes: 2147483648,
    storage_backend: 'gcs',
    upload_mode: 'resumable',
    session_url: session,
  };
}

const completed = (id: string) => jsonResponse({ archive_id: id, status: 'uploaded', enqueue: 'triggered' });

const payload = { sport: 'road', consent: true, consent_version: 'v5', consent_text: 'ok', locale: 'fr' };

class MemoryStorage {
  m = new Map<string, string>();
  getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
  setItem(k: string, v: string) { this.m.set(k, String(v)); }
  removeItem(k: string) { this.m.delete(k); }
}

describe('uploadArchive — resumable mode + resume after reload', () => {
  let storage: MemoryStorage;
  beforeEach(() => {
    noSleep.mockClear();
    storage = new MemoryStorage();
    vi.stubGlobal('localStorage', storage);
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it('declares the size at init, uploads in chunks, remembers then forgets the session', async () => {
    const total = 2 * CHUNK + 1;
    const file = zipFile(total);
    const s = new FakeSession(total);
    let storedDuringUpload: string | null = null;
    const transport = async (req: ChunkRequest) => {
      storedDuringUpload ??= storage.getItem(RESUME_STORAGE_KEY);
      return s.transport(req);
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(resumableInit('a1', 'https://session/a1')))
      .mockResolvedValueOnce(completed('a1'));
    vi.stubGlobal('fetch', fetchMock);
    const put = vi.fn();
    const stages: string[] = [];

    const res = await uploadArchive({
      apiUrl: API, token: null, blob: file, payload, put, transport, sleep: noSleep, chunkBytes: CHUNK,
      onStage: (st) => stages.push(st),
    });

    expect(res.status).toBe('uploaded');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).size).toBe(total);
    expect(put).not.toHaveBeenCalled(); // resumable, not the single signed PUT
    expect(s.calls).toHaveLength(3);
    expect(s.data).toEqual(patterned(total));
    expect(JSON.parse(storedDuringUpload!)).toMatchObject({
      session_url: 'https://session/a1', archive_id: 'a1', name: file.name, size: total, lastModified: file.lastModified,
    });
    expect(storage.getItem(RESUME_STORAGE_KEY)).toBeNull(); // cleared on completion
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ archive_id: 'a1', key: 'archive-intake/user-1/a1.zip' });
    expect(stages).toEqual(['init', 'uploading', 'finalizing', 'done']);
  });

  it('init answering "put" (local / fallback) still uses the single PUT', async () => {
    const init = { ...resumableInit('p1', 'x'), upload_mode: 'put' as const, session_url: null, upload_url: 'https://signed' };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse(init)).mockResolvedValueOnce(completed('p1')));
    const put = vi.fn().mockResolvedValue(undefined);
    const transport = vi.fn();
    await uploadArchive({ apiUrl: API, token: null, blob: zipFile(10), payload, put, transport });
    expect(put).toHaveBeenCalledTimes(1);
    expect(transport).not.toHaveBeenCalled();
  });

  it('after a reload, the SAME file resumes the stored session from the server offset (no new init)', async () => {
    const total = 3 * CHUNK;
    const s = new FakeSession(total);
    s.failAllFrom = 1; // chunk 1 lands, then the connection dies for good
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(resumableInit('r1', 'https://session/r1')));
    vi.stubGlobal('fetch', fetchMock);
    const first = await uploadArchive({
      apiUrl: API, token: null, blob: zipFile(total), payload, transport: s.transport, sleep: noSleep, chunkBytes: CHUNK,
    }).catch((e) => e);
    expect(first.code).toBe('network');
    expect(storage.getItem(RESUME_STORAGE_KEY)).not.toBeNull(); // kept for the retry
    expect(s.persisted).toBe(CHUNK);

    // "Reload": a fresh File object for the same file on disk, network back.
    s.failAllFrom = Infinity;
    const callsBefore = s.calls.length;
    const fetch2 = vi.fn().mockResolvedValueOnce(completed('r1'));
    vi.stubGlobal('fetch', fetch2);
    const progress: number[] = [];
    const res = await uploadArchive({
      apiUrl: API, token: null, blob: zipFile(total), payload, transport: s.transport, sleep: noSleep,
      chunkBytes: CHUNK, onProgress: (f) => progress.push(f),
    });

    expect(res.archive_id).toBe('r1');
    expect(fetch2).toHaveBeenCalledTimes(1); // complete only — NO second init
    expect(fetch2.mock.calls[0][0]).toBe(`${API}/imports/strava-archive/complete`);
    const resumed = s.calls.slice(callsBefore).map((c) => c.contentRange);
    expect(resumed[0]).toBe(`bytes */${total}`); // ask first…
    expect(resumed[1]).toBe(`bytes ${CHUNK}-${2 * CHUNK - 1}/${total}`); // …then continue from the server offset
    expect(s.data).toEqual(patterned(total));
    expectMonotonic(progress);
    expect(storage.getItem(RESUME_STORAGE_KEY)).toBeNull();
  });

  it('a DIFFERENT file (same name, other lastModified) does not resume: fresh init + new session', async () => {
    const total = 2 * CHUNK;
    const old = new FakeSession(total);
    old.failAllFrom = 1;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse(resumableInit('o1', 'https://session/o1'))));
    await uploadArchive({
      apiUrl: API, token: null, blob: zipFile(total, 1000), payload, transport: old.transport, sleep: noSleep, chunkBytes: CHUNK,
    }).catch(() => {});
    expect(storage.getItem(RESUME_STORAGE_KEY)).not.toBeNull();

    const fresh = new FakeSession(total);
    const oldCalls = old.calls.length;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(resumableInit('n1', 'https://session/n1')))
      .mockResolvedValueOnce(completed('n1'));
    vi.stubGlobal('fetch', fetchMock);
    const urls: string[] = [];
    await uploadArchive({
      apiUrl: API, token: null, blob: zipFile(total, 2000), payload, sleep: noSleep, chunkBytes: CHUNK,
      transport: (req) => { urls.push(req.url); return fresh.transport(req); },
    });
    expect(fetchMock.mock.calls[0][0]).toBe(`${API}/imports/strava-archive/init`);
    expect(new Set(urls)).toEqual(new Set(['https://session/n1']));
    expect(old.calls).toHaveLength(oldCalls); // the stale session was never touched
    expect(fresh.calls[0].contentRange).toBe(`bytes 0-${CHUNK - 1}/${total}`);
  });

  it('an expired stored session (404) falls back to a fresh init', async () => {
    const total = CHUNK;
    const file = zipFile(total);
    storage.setItem(RESUME_STORAGE_KEY, JSON.stringify({
      name: file.name, size: total, lastModified: file.lastModified,
      session_url: 'https://session/dead', archive_id: 'dead', init: resumableInit('dead', 'https://session/dead'),
      saved_at: Date.now(),
    }));
    const fresh = new FakeSession(total);
    const transport = async (req: ChunkRequest) =>
      req.url === 'https://session/dead' ? { status: 404, range: null } : fresh.transport(req);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(resumableInit('n2', 'https://session/n2')))
      .mockResolvedValueOnce(completed('n2'));
    vi.stubGlobal('fetch', fetchMock);
    const res = await uploadArchive({ apiUrl: API, token: null, blob: file, payload, transport, sleep: noSleep, chunkBytes: CHUNK });
    expect(res.archive_id).toBe('n2');
    expect(fetchMock.mock.calls[0][0]).toBe(`${API}/imports/strava-archive/init`);
    expect(fresh.complete).toBe(true);
  });

  it('broken localStorage never breaks the upload', async () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('SecurityError'); },
      setItem: () => { throw new Error('QuotaExceeded'); },
      removeItem: () => { throw new Error('SecurityError'); },
    });
    const s = new FakeSession(CHUNK);
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(jsonResponse(resumableInit('b1', 'https://session/b1')))
      .mockResolvedValueOnce(completed('b1')));
    const res = await uploadArchive({ apiUrl: API, token: null, blob: zipFile(CHUNK), payload, transport: s.transport, sleep: noSleep, chunkBytes: CHUNK });
    expect(res.status).toBe('uploaded');
  });
});
