/**
 * Pure decision: given the files a user dropped / picked in the ONE contribution
 * dropzone, which upload path do they take?
 *
 * There are two backends with very different limits, and picking the wrong one
 * is the UX trap this module exists to prevent:
 *
 *   - **archive** — a Strava/Garmin/Komoot export `.zip` (can be GB-scale). It
 *     MUST go through the signed-URL, direct-to-storage flow
 *     (`lib/strava-archive-upload.ts`: init → PUT straight to GCS → complete),
 *     which bypasses the ~32–50 MB Cloud Run request cap. A `.zip` must NEVER
 *     be POSTed to `/imports/files`.
 *   - **files** — one or many loose GPX / FIT traces (small). These stream
 *     through `/imports/files`.
 *
 * Rule: if the picked set contains ANY `.zip`, take the archive path with the
 * first `.zip` (a real export is a single archive); otherwise the files path.
 *
 * PURE (no DOM / no fetch / no React) so the routing decision is unit-testable
 * without a browser — the component just dispatches on the result.
 */

export interface NamedFile {
  name: string;
}

export type UploadRoute<F extends NamedFile = File> =
  | { kind: 'archive'; file: F }
  | { kind: 'files'; files: F[] }
  | { kind: 'empty' };

/** True when a filename ends in `.zip` (case-insensitive, trimmed). */
export function isZipName(name: string): boolean {
  return /\.zip$/i.test(name.trim());
}

/**
 * Decide the upload path for a picked/dropped set. Generic over the file shape
 * so tests can pass plain `{ name }` objects; the component passes a real
 * `FileList` (an `ArrayLike<File>`).
 */
export function chooseUploadPath<F extends NamedFile>(
  files: ArrayLike<F> | null | undefined,
): UploadRoute<F> {
  const arr: F[] = files ? Array.from(files) : [];
  if (arr.length === 0) return { kind: 'empty' };
  const zip = arr.find((f) => isZipName(f.name));
  if (zip) return { kind: 'archive', file: zip };
  return { kind: 'files', files: arr };
}

/** Loose-trace extensions `/imports/files` accepts (the files-block picker's `accept`). */
export function isTraceName(name: string): boolean {
  return /\.(gpx|fit)$/i.test(name.trim());
}

/**
 * Split a picked/dropped set into what will actually be uploaded and what is
 * IGNORED, so the UI can say so instead of dropping files silently:
 *   - archive path: every file but the chosen `.zip` is ignored;
 *   - files path: anything that is not a `.gpx` / `.fit` is ignored.
 */
export function partitionUpload<F extends NamedFile>(
  files: ArrayLike<F> | null | undefined,
): { route: UploadRoute<F>; ignored: F[] } {
  const route = chooseUploadPath(files);
  const all: F[] = files ? Array.from(files) : [];
  if (route.kind === 'archive') {
    return { route, ignored: all.filter((f) => f !== route.file) };
  }
  if (route.kind === 'files') {
    const kept = route.files.filter((f) => isTraceName(f.name));
    const ignored = route.files.filter((f) => !isTraceName(f.name));
    return { route: kept.length ? { kind: 'files', files: kept } : { kind: 'empty' }, ignored };
  }
  return { route, ignored: [] };
}

// ── Pre-send sport recap ─────────────────────────────────────────────────────
//
// Generic `<type>` values that carry no road/gravel/MTB signal (Strava GPX
// exports collapse every cycling variant to "cycling" / "1"). Mirrors the
// backend's generic entries in `GPX_TRACK_TYPE_TO_SPORT`; only used to WORD
// the recap — the server still decides (and skips what it can't classify).
const GENERIC_GPX_TYPES = new Set(['cycling', 'ride', 'biking', 'bike', 'ebiking', '1']);

/** True when a GPX text declares a usable sport in its first `<type>`. */
export function gpxDeclaresSport(text: string): boolean {
  const m = /<type>\s*([^<]*?)\s*<\/type>/i.exec(text);
  if (!m || !m[1]) return false;
  const key = m[1].toLowerCase().normalize('NFKD').replace(/[^a-z0-9]/g, '');
  return key.length > 0 && !GENERIC_GPX_TYPES.has(key);
}

/** Bytes read per GPX to find `<type>` (it sits in `<trk>`, near the top). */
const GPX_HEAD_BYTES = 64 * 1024;

/**
 * How many loose traces don't declare their own sport (→ they will take the
 * chosen chip). A FIT carries its device sport, so it counts as declared; a
 * GPX counts when its `<type>` is missing or generic. Unreadable → undeclared.
 */
export async function countUndeclaredSport(files: File[]): Promise<number> {
  let n = 0;
  for (const f of files) {
    if (!/\.gpx$/i.test(f.name.trim())) continue;
    try {
      const head = await f.slice(0, GPX_HEAD_BYTES).text();
      if (!gpxDeclaresSport(head)) n++;
    } catch {
      n++;
    }
  }
  return n;
}
