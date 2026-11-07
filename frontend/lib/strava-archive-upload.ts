/**
 * Signed-URL WHOLE-archive upload flow (browser → storage direct → importer).
 *
 * A REAL Strava export is ~70 MB zipped / ~400 MB unzipped — it cannot route
 * through Cloud Run (~32 MB request cap) and must never be buffered on the web
 * instance. So instead of POSTing the .zip, the browser:
 *
 *   1. POST /imports/strava-archive/init      → a signed PUT URL + a key
 *   2. PUT the .zip STRAIGHT to that URL       (GCS in prod; a backend
 *      stand-in path in local/dev) — bypassing Cloud Run entirely
 *   3. POST /imports/strava-archive/complete   → marks the upload ready; a
 *      scheduled 2 Gi importer job ingests the members progressively.
 *
 * The network calls (init/complete) use `fetch`; the PUT uses XMLHttpRequest
 * so we can surface real byte-level upload progress. `uploadArchive` takes an
 * injectable `put` so the orchestration is unit-testable with a mocked fetch.
 *
 * RESUMABLE mode (gcs, `upload_mode: "resumable"`): step 2 becomes a GCS
 * resumable session opened by init and bound to the declared size. The .zip
 * goes up in 16 MiB `Content-Range` chunks; a blip / 5xx / 429 backs off,
 * asks the session how far it got (`bytes *\/total`) and resumes from THAT
 * offset. The session is remembered in localStorage, so re-selecting the SAME
 * file after a reload resumes instead of restarting a 2 GB upload. The local
 * dev backend (and any init that answers `upload_mode: "put"`) keeps the
 * single PUT above.
 */

import { getCurrentUserId } from './auth';

export interface ArchiveInitPayload {
  /** Only when the user EXPLICITLY picked a sport (never a default). Backend
   *  last resort for members with no activities.csv row and no FIT/GPX sport
   *  of their own; absent → such members are skipped. */
  sport?: string;
  consent: boolean;
  consent_version: string;
  consent_text: string;
  locale: string;
  filename?: string;
  /** Declared .zip size — set by `uploadArchive`; unlocks the resumable mode. */
  size?: number;
  /** Resumable→PUT fallback: re-issue this still-'awaiting_upload' archive
   *  (same row, key, consent) as a single PUT instead of creating a new one. */
  reuse_archive_id?: string;
}

export interface ArchiveInitResult {
  archive_id: string;
  consent_id: string;
  bucket_key: string;
  upload_url: string;
  upload_method: string;
  content_type: string;
  max_bytes: number;
  storage_backend: 'gcs' | 'local';
  /** Absent on an older backend → treated as "put". */
  upload_mode?: 'put' | 'resumable';
  session_url?: string | null;
}

export interface ArchiveCompleteResult {
  archive_id: string;
  status: string;
  enqueue: string;
}

/**
 * Max archive size accepted by the backend — mirrors ``MAX_ARCHIVE_BYTES`` set
 * on the API service + importer job in ``scripts/deploy-prod.sh`` (2 GiB,
 * 2147483648). Keep the two in sync: the browser pre-checks against this so a
 * too-big export is refused BEFORE a multi-GB upload, not after it.
 */
export const MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024;

/** Machine-readable failure class, mapped to a translated message in the UI
 *  (`archiveErrorKey`) — never show the raw English server/XHR text. */
export type ArchiveErrorCode =
  | 'too_large'
  | 'init_failed'
  | 'upload_failed'
  | 'network'
  | 'complete_failed';

export class ArchiveUploadError extends Error {
  code: ArchiveErrorCode;
  /** HTTP status when the failure came from a response (undefined = network). */
  status?: number;
  constructor(message: string, code: ArchiveErrorCode = 'upload_failed', status?: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

/** True when the backend's detail is its "archive too large" reason
 *  (`archive_intake.too_large_message`) or the status is 413. */
function isTooLarge(status: number, detail: unknown): boolean {
  return status === 413 || (typeof detail === 'string' && /archive too large/i.test(detail));
}

/** Pre-upload guard: returns an error for a file over MAX_ARCHIVE_BYTES, else null. */
export function checkArchiveSize(file: { size: number }): ArchiveUploadError | null {
  return file.size > MAX_ARCHIVE_BYTES
    ? new ArchiveUploadError(`archive too large (${file.size} bytes)`, 'too_large')
    : null;
}

/** Translation key for an upload failure (FR/EN in lib/translations). */
export function archiveErrorKey(err: unknown): string {
  const code = err instanceof ArchiveUploadError ? err.code : null;
  switch (code) {
    case 'too_large': return 'strava.archive.err.tooLarge';
    case 'network': return 'strava.archive.err.network';
    case 'init_failed':
    case 'upload_failed':
    case 'complete_failed': return 'strava.archive.err.server';
    default: return 'strava.archive.failed';
  }
}

interface AuthCtx {
  apiUrl: string;
  token: string | null;
}

interface PutOpts extends AuthCtx {
  onProgress?: (fraction: number) => void;
}

export type PutFn = (init: ArchiveInitResult, blob: Blob, opts: PutOpts) => Promise<void>;

/**
 * In this cookie-based app the real JWT lives in an httpOnly cookie; `getToken()`
 * (lib/auth.ts) only ever returns the literal placeholder `'cookie-auth'` when a
 * user is signed in. Sending `Authorization: Bearer cookie-auth` is actively
 * HARMFUL: the backend's `get_current_user` prefers the bearer token over the
 * cookie (`token or request.cookies.get(...)`), so `_decode_token('cookie-auth')`
 * throws and every archive upload 401s ("Invalid or expired token"). Only ever
 * emit a REAL bearer; otherwise send nothing and rely on `credentials:'include'`
 * (the httpOnly cookie) as the sole credential. Exported for the non-regression
 * test that pins this placeholder guard.
 */
export function authHeaders(token: string | null): Record<string, string> {
  return token && token !== 'cookie-auth' ? { Authorization: `Bearer ${token}` } : {};
}

export async function initArchiveUpload(
  { apiUrl, token }: AuthCtx,
  payload: ArchiveInitPayload,
): Promise<ArchiveInitResult> {
  const resp = await fetch(`${apiUrl}/imports/strava-archive/init`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify(payload),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new ArchiveUploadError(
      data.detail || `init failed (${resp.status})`,
      isTooLarge(resp.status, data.detail) ? 'too_large' : 'init_failed',
    );
  }
  return data as ArchiveInitResult;
}

/**
 * PUT the archive to storage. For the `gcs` backend the URL is an absolute
 * signed URL and carries ONLY the bound Content-Type (no auth header). For the
 * `local` dev stand-in the URL is a backend path, so prefix it with apiUrl and
 * send the auth header.
 */
export const putArchive: PutFn = (init, blob, { apiUrl, token, onProgress }) =>
  new Promise<void>((resolve, reject) => {
    const isLocal = init.storage_backend === 'local';
    const url = isLocal ? `${apiUrl}${init.upload_url}` : init.upload_url;
    const xhr = new XMLHttpRequest();
    xhr.open(init.upload_method || 'PUT', url);
    xhr.setRequestHeader('Content-Type', init.content_type);
    if (isLocal) {
      // Local dev backend: send the httpOnly auth cookie (getToken() only ever
      // yields the 'cookie-auth' placeholder here — never a real JWT), and add a
      // Bearer ONLY when we actually hold a real token. Sending `Bearer
      // cookie-auth` would make the backend reject a perfectly valid cookie.
      xhr.withCredentials = true;
      const bearer = authHeaders(token).Authorization;
      if (bearer) xhr.setRequestHeader('Authorization', bearer);
    }
    if (xhr.upload) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      };
    }
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new ArchiveUploadError(
          `upload failed (${xhr.status})`,
          xhr.status === 413 ? 'too_large' : 'upload_failed',
        ));
    xhr.onerror = () => reject(new ArchiveUploadError('upload network error', 'network'));
    xhr.send(blob);
  });

export async function completeArchiveUpload(
  { apiUrl, token }: AuthCtx,
  init: ArchiveInitResult,
): Promise<ArchiveCompleteResult> {
  const resp = await fetch(`${apiUrl}/imports/strava-archive/complete`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify({ archive_id: init.archive_id, key: init.bucket_key }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new ArchiveUploadError(
      data.detail || `complete failed (${resp.status})`,
      isTooLarge(resp.status, data.detail) ? 'too_large' : 'complete_failed',
      resp.status,
    );
  }
  return data as ArchiveCompleteResult;
}

// ── Resumable (chunked) upload to a GCS resumable session ───────────────────

/** Chunk size: 16 MiB. GCS requires every non-final chunk to be a multiple of
 *  256 KiB; 16 MiB keeps a lost chunk cheap to resend on a phone connection. */
export const RESUMABLE_CHUNK_BYTES = 16 * 1024 * 1024;

/** Consecutive failed attempts (no byte persisted in between) before giving up. */
const MAX_RESUMABLE_RETRIES = 8;

export interface ChunkRequest {
  url: string;
  headers: Record<string, string>;
  /** null = empty body (status query). */
  body: Blob | null;
  /** Bytes of THIS request's body sent so far. */
  onProgress?: (loaded: number) => void;
}

export interface ChunkResponse {
  status: number;
  /** The session's `Range` response header (persisted bytes), if any. */
  range: string | null;
}

/** One PUT to the session URL. Resolves with any HTTP status; REJECTS only on
 *  a transport failure (offline, reset, aborted) — the caller then retries. */
export type ChunkTransport = (req: ChunkRequest) => Promise<ChunkResponse>;

/** XHR (not fetch) so a chunk reports byte-level upload progress. No auth
 *  header: the session URL itself is the credential, and GCS answers CORS for
 *  the origin bound at session creation. */
export const xhrChunkTransport: ChunkTransport = ({ url, headers, body, onProgress }) =>
  new Promise<ChunkResponse>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    if (xhr.upload && onProgress) {
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded); };
    }
    xhr.onload = () => resolve({ status: xhr.status, range: xhr.getResponseHeader('Range') });
    const fail = () => reject(new ArchiveUploadError('upload network error', 'network'));
    xhr.onerror = fail;
    xhr.onabort = fail;
    xhr.ontimeout = fail;
    xhr.send(body);
  });

/** Bytes the session has persisted, from a 308's `Range: bytes=0-N` → N+1.
 *  No header = nothing persisted yet (GCS semantics) → 0. */
export function parseRangeOffset(range: string | null): number {
  const m = range ? /bytes=0-(\d+)/.exec(range) : null;
  return m ? Number(m[1]) + 1 : 0;
}

/** The session URL is gone (expired ~1 week, or cancelled) — the caller must
 *  start over with a fresh init. */
class SessionGoneError extends ArchiveUploadError {
  constructor(status: number) {
    super(`upload session gone (${status})`, 'upload_failed', status);
  }
}

/** The session URL couldn't be reached and the server never confirmed a
 *  single byte (typically CORS on the session URL) — `uploadArchive` then
 *  falls back to the single signed PUT. */
export class ResumableUnreachableError extends ArchiveUploadError {
  constructor() {
    super('resumable session unreachable before any byte was persisted', 'network');
  }
}

export interface ResumableOpts {
  onProgress?: (fraction: number) => void;
  transport?: ChunkTransport;
  /** Injectable for tests (default: real setTimeout). */
  sleep?: (ms: number) => Promise<void>;
  chunkBytes?: number;
  /** Ask the session for its offset before sending (resume after reload). */
  queryFirst?: boolean;
  /** Throw `ResumableUnreachableError` (instead of retrying for minutes) when
   *  a chunk fails at the transport level before the server ever confirmed a
   *  byte AND a status probe also fails or reports 0. */
  failFastIfUnreachable?: boolean;
}

const realSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** 1 s, 2 s, 4 s … capped at 32 s, plus jitter (GCS retry guidance). */
function backoffMs(attempt: number): number {
  return Math.min(32_000, 1000 * 2 ** (attempt - 1)) + Math.floor(Math.random() * 500);
}

/**
 * Upload `blob` to a GCS resumable session in `Content-Range` chunks.
 *
 * - 308 → read `Range` for what the server actually persisted (it may keep
 *   less than we sent) and continue from THERE, never from the client's count.
 * - transport error / 5xx / 429 → exponential backoff, then a status query
 *   (`Content-Range: bytes *\/total`, empty body) to learn the real offset.
 * - 200/201 → the object is complete. 404/410 → `SessionGoneError`.
 * Progress is the persisted+in-flight fraction, clamped to be monotonic (a
 * resend after a blip never makes the bar go backwards).
 */
export async function uploadResumable(
  sessionUrl: string,
  blob: Blob,
  opts: ResumableOpts = {},
): Promise<void> {
  const {
    onProgress, transport = xhrChunkTransport, sleep = realSleep,
    chunkBytes = RESUMABLE_CHUNK_BYTES, queryFirst = false, failFastIfUnreachable = false,
  } = opts;
  const total = blob.size;
  let best = -1;
  const report = (sent: number) => {
    const f = total > 0 ? Math.min(1, sent / total) : 1;
    if (f > best) { best = f; onProgress?.(f); }
  };

  let offset = 0;
  let needStatus = queryFirst;
  let failures = 0;
  let confirmed = 0; // highest offset the SERVER has acknowledged
  report(0);
  for (;;) {
    let resp: ChunkResponse;
    let sentEnd = offset;
    try {
      if (needStatus || total === 0) {
        resp = await transport({
          url: sessionUrl, headers: { 'Content-Range': `bytes */${total}` }, body: null,
        });
      } else {
        const start = offset;
        const end = Math.min(start + chunkBytes, total);
        sentEnd = end;
        resp = await transport({
          url: sessionUrl,
          headers: { 'Content-Range': `bytes ${start}-${end - 1}/${total}` },
          body: blob.slice(start, end),
          onProgress: (loaded) => report(start + loaded),
        });
      }
    } catch (err) {
      // Transport failure — the chunk may be partly persisted: back off, then
      // ask the session where it stands.
      failures += 1;
      if (failFastIfUnreachable && confirmed === 0) {
        // Nothing ever acknowledged: one status probe decides whether the
        // session is reachable at all (a CORS block looks exactly like this).
        await sleep(backoffMs(1));
        let probe: ChunkResponse | null = null;
        try {
          probe = await transport({ url: sessionUrl, headers: { 'Content-Range': `bytes */${total}` }, body: null });
        } catch { /* still unreachable */ }
        if (!probe || (probe.status === 308 && parseRangeOffset(probe.range) === 0)) {
          throw new ResumableUnreachableError();
        }
      }
      if (failures > MAX_RESUMABLE_RETRIES) {
        throw err instanceof ArchiveUploadError ? err : new ArchiveUploadError('upload network error', 'network');
      }
      await sleep(backoffMs(failures));
      needStatus = true;
      continue;
    }

    const { status } = resp;
    if (status === 200 || status === 201) { report(total); return; }
    if (status === 308) confirmed = Math.max(confirmed, parseRangeOffset(resp.range));
    if (status === 404 || status === 410) throw new SessionGoneError(status);
    if (status === 308) {
      const persisted = parseRangeOffset(resp.range);
      const wasStatus = needStatus;
      needStatus = false;
      if (persisted > offset || wasStatus) {
        if (persisted > offset) failures = 0;
        offset = persisted;
        report(offset);
        continue;
      }
      // A chunk PUT that persisted nothing (sentEnd > offset but Range didn't
      // move): count it, so an unreadable Range header can't loop forever.
      failures += 1;
      // Nothing EVER acknowledged and a second full chunk "persisted nothing":
      // that is the signature of a `Range` header the browser can't read (CORS
      // exposure on the session URL) — every zip > 1 chunk would stall. Hand
      // over to the single-PUT fallback instead of resending 16 MiB 8 times.
      if (failFastIfUnreachable && confirmed === 0 && failures >= 2) throw new ResumableUnreachableError();
      if (sentEnd > offset && failures <= MAX_RESUMABLE_RETRIES) { await sleep(backoffMs(failures)); continue; }
      throw new ArchiveUploadError('upload stalled (no bytes persisted)', 'upload_failed', status);
    }
    if (status >= 500 || status === 429) {
      failures += 1;
      if (failures > MAX_RESUMABLE_RETRIES) {
        throw new ArchiveUploadError(`upload failed (${status})`, 'upload_failed', status);
      }
      await sleep(backoffMs(failures));
      needStatus = true;
      continue;
    }
    throw new ArchiveUploadError(
      `upload failed (${status})`, status === 413 ? 'too_large' : 'upload_failed', status,
    );
  }
}

// ── Resume-after-reload: remember the open session for the SAME file ────────

/** localStorage key holding the one in-flight resumable session. */
export const RESUME_STORAGE_KEY = 'ct.archiveUpload.resumable.v1';

/** GCS sessions live ~1 week, but the drain job's reaper FAILS a still-
 *  'awaiting_upload' archive after 48 h (`_AWAITING_UPLOAD_TTL_HOURS` in
 *  backend/app/jobs/ingest_pending_archives.py). Stay well below it so a
 *  resume never lands on a reaped row. */
const RESUME_MAX_AGE_MS = 40 * 3600 * 1000;

interface FileFingerprint { name: string; size: number; lastModified: number }

interface PersistedSession extends FileFingerprint {
  /** Owner: another account on this browser never resumes this session. */
  user_id: string;
  session_url: string;
  archive_id: string;
  init: ArchiveInitResult;
  saved_at: number;
}

/** name+size+lastModified of a picked File (a bare Blob can't be re-identified). */
function fingerprint(blob: Blob): FileFingerprint | null {
  const f = blob as Partial<File>;
  return typeof f.name === 'string' && typeof f.lastModified === 'number'
    ? { name: f.name, size: blob.size, lastModified: f.lastModified }
    : null;
}

function currentUserId(): string | null {
  try { return getCurrentUserId(); } catch { return null; }
}

// Every storage access is try/catch: private mode / blocked storage / SSR must
// degrade to "no resume", never break the upload itself.
function loadSession(fp: FileFingerprint, userId: string): PersistedSession | null {
  try {
    const raw = globalThis.localStorage?.getItem(RESUME_STORAGE_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as PersistedSession;
    const same = s.user_id === userId
      && s.name === fp.name && s.size === fp.size && s.lastModified === fp.lastModified;
    const fresh = Date.now() - s.saved_at < RESUME_MAX_AGE_MS;
    return same && fresh && s.session_url && s.init ? s : null;
  } catch {
    return null;
  }
}

function saveSession(fp: FileFingerprint, userId: string, init: ArchiveInitResult): void {
  try {
    const s: PersistedSession = {
      ...fp, user_id: userId, session_url: init.session_url as string, archive_id: init.archive_id, init, saved_at: Date.now(),
    };
    globalThis.localStorage?.setItem(RESUME_STORAGE_KEY, JSON.stringify(s));
  } catch { /* storage unavailable — upload still works, just not across reloads */ }
}

function clearSession(): void {
  try { globalThis.localStorage?.removeItem(RESUME_STORAGE_KEY); } catch { /* idem */ }
}

/** A chunk answer that is final for this session (403, 400, 413…): retrying
 *  the same session would just repeat it, so the next attempt must re-init.
 *  429 (throttled, retried until exhausted) is NOT final. */
function isFinalClientError(err: unknown): boolean {
  const st = err instanceof ArchiveUploadError ? err.status : undefined;
  return st !== undefined && st >= 400 && st < 500 && st !== 429;
}

export type UploadStage = 'init' | 'uploading' | 'finalizing' | 'done';

export interface UploadArchiveArgs extends AuthCtx {
  blob: Blob;
  payload: ArchiveInitPayload;
  onProgress?: (fraction: number) => void;
  onStage?: (stage: UploadStage) => void;
  put?: PutFn;
  /** Injectable chunk transport + sleep for the resumable mode (tests). */
  transport?: ChunkTransport;
  sleep?: (ms: number) => Promise<void>;
  chunkBytes?: number;
}

/**
 * Full 3-step orchestration: init → direct upload → complete. `put` (single
 * PUT) and `transport`/`sleep` (resumable chunks) are injectable so tests can
 * drive the sequence with a mocked fetch + a fake storage server.
 *
 * If the SAME file (name+size+lastModified) has a remembered resumable
 * session, init is skipped: the upload resumes from the session's offset and
 * completes the ORIGINAL archive (consent was recorded by that first init). A
 * gone session (expired) falls back to a fresh init.
 */
export async function uploadArchive(args: UploadArchiveArgs): Promise<ArchiveCompleteResult> {
  const {
    apiUrl, token, blob, payload, onProgress, onStage, put = putArchive, transport, sleep, chunkBytes,
  } = args;
  const chunkOpts = { onProgress, transport, sleep, chunkBytes };
  const ctx: AuthCtx = { apiUrl, token };
  const tooBig = checkArchiveSize(blob);
  if (tooBig) throw tooBig;
  // Resume slot is keyed by file AND account; the id is read NOW (upload
  // time), never captured at render. No user id → no resume.
  const userId = currentUserId();
  const fp = userId ? fingerprint(blob) : null;
  onStage?.('init');

  let init: ArchiveInitResult | null = null;
  const stored = fp && userId ? loadSession(fp, userId) : null;
  if (stored) {
    onStage?.('uploading');
    try {
      await uploadResumable(stored.session_url, blob, { ...chunkOpts, queryFirst: true });
      init = stored.init;
    } catch (err) {
      if (!(err instanceof SessionGoneError)) {
        if (isFinalClientError(err)) clearSession();
        throw err; // network / 5xx: keep it, a retry resumes again
      }
      clearSession();
    }
  }

  if (!init) {
    init = await initArchiveUpload(ctx, { ...payload, size: blob.size });
    onStage?.('uploading');
    if (init.upload_mode === 'resumable' && init.session_url) {
      if (fp && userId) saveSession(fp, userId, init);
      try {
        await uploadResumable(init.session_url, blob, { ...chunkOpts, failFastIfUnreachable: true });
      } catch (err) {
        if (err instanceof SessionGoneError || isFinalClientError(err)) clearSession();
        if (!(err instanceof ResumableUnreachableError)) throw err;
        // Safety net: the browser can't talk to the session URL and not one
        // byte landed — fall back to the single signed PUT for the SAME
        // archive (reuse_archive_id: no second, orphaned pending row).
        console.warn('[archive-upload] resumable session unreachable before any byte was persisted; falling back to a single signed PUT', err);
        clearSession();
        const { size: _ignored, ...noSize } = payload;
        init = await initArchiveUpload(ctx, { ...noSize, reuse_archive_id: init.archive_id });
        if (init.upload_mode === 'resumable') throw err; // never loop
        await put(init, blob, { apiUrl, token, onProgress });
      }
    } else {
      await put(init, blob, { apiUrl, token, onProgress });
    }
  }

  onStage?.('finalizing');
  let result: ArchiveCompleteResult;
  try {
    result = await completeArchiveUpload(ctx, init);
  } catch (err) {
    // A 4xx is final for this archive (oversize, not ours, …) — don't make
    // the next attempt resume into it. 5xx/network: keep it, a retry resumes.
    const st = err instanceof ArchiveUploadError ? err.status : undefined;
    if (st !== undefined && st >= 400 && st < 500) clearSession();
    throw err;
  }
  clearSession();
  onStage?.('done');
  return result;
}
