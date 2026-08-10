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
