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
 */

export interface ArchiveInitPayload {
  sport: string;
  consent: boolean;
  consent_version: string;
  consent_text: string;
  locale: string;
  filename?: string;
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
}

export interface ArchiveCompleteResult {
  archive_id: string;
  status: string;
  enqueue: string;
}

export class ArchiveUploadError extends Error {}

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
  if (!resp.ok) throw new ArchiveUploadError(data.detail || `init failed (${resp.status})`);
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
        : reject(new ArchiveUploadError(`upload failed (${xhr.status})`));
    xhr.onerror = () => reject(new ArchiveUploadError('upload network error'));
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
  if (!resp.ok) throw new ArchiveUploadError(data.detail || `complete failed (${resp.status})`);
  return data as ArchiveCompleteResult;
}

export type UploadStage = 'init' | 'uploading' | 'finalizing' | 'done';

export interface UploadArchiveArgs extends AuthCtx {
  blob: Blob;
  payload: ArchiveInitPayload;
  onProgress?: (fraction: number) => void;
  onStage?: (stage: UploadStage) => void;
  put?: PutFn;
}

/**
 * Full 3-step orchestration: init → direct PUT → complete. `put` is injectable
 * so tests can drive the sequence with a mocked fetch + a stub PUT.
 */
export async function uploadArchive(args: UploadArchiveArgs): Promise<ArchiveCompleteResult> {
  const { apiUrl, token, blob, payload, onProgress, onStage, put = putArchive } = args;
  const ctx: AuthCtx = { apiUrl, token };
  onStage?.('init');
  const init = await initArchiveUpload(ctx, payload);
  onStage?.('uploading');
  await put(init, blob, { apiUrl, token, onProgress });
  onStage?.('finalizing');
  const result = await completeArchiveUpload(ctx, init);
  onStage?.('done');
  return result;
}
