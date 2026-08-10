/**
 * Client-side folder → ZIP packer for the "import a Strava export folder" UX.
 *
 * The backend `/imports/files` ZIP path (`parse_zip_of_gpx` in
 * `backend/app/services/gpx.py`) is the robust, CSV-aware ingest path:
 * it reads `activities.csv` at the archive root and classifies each
 * member's sport from the CSV Activity Type + Name, keyed by the CSV
 * `Filename` column (e.g. `activities/100.gpx`) matched against ZIP
 * member paths.
 *
 * Rather than re-implement CSV classification client-side, the folder
 * picker zips the chosen folder CLIENT-SIDE preserving the Strava export
 * layout (`activities.csv` at root + `activities/<id>.gpx` members) and
 * POSTs that single zip to the existing ZIP endpoint.
 *
 * This module is a PURE helper (File[] → caps check + path layout) so it
 * can be unit-tested without a browser. The actual zip blob is produced
 * by `buildFolderZip` which lazily imports JSZip (keeps it out of the
 * main bundle until a folder is actually selected).
 */

// Mirror of the backend caps in `backend/app/services/gpx.py`
// (MAX_ZIP_MEMBERS / MAX_ZIP_TOTAL_UNCOMPRESSED / MAX_ZIP_MEMBER_UNCOMPRESSED).
// We pre-flight against these so a too-big folder fails with a friendly
// message instead of an opaque 413/500 after a long upload.
export const MAX_ZIP_MEMBERS = 5000;
export const MAX_ZIP_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024; // 500 MB
export const MAX_ZIP_MEMBER_UNCOMPRESSED = 10 * 1024 * 1024; // 10 MB

// Members the backend ZIP parser actually consumes. Everything else in a
// Strava export folder (media/, the giant `*.fit` originals aside, README,
// etc.) is dead weight — we drop it so we stay under the caps and don't
// upload private junk. `activities.csv` is the sport-classification key.
const GPX_FIT_RE = /\.(gpx|fit)(\.gz)?$/i;

export interface FolderZipMember {
  /** Path inside the produced zip, e.g. `activities/100.gpx` or `activities.csv`. */
  path: string;
  file: File;
}

export interface FolderZipPlan {
  members: FolderZipMember[];
  totalBytes: number;
  hasCsv: boolean;
}

export class FolderZipError extends Error {}

/**
 * Strip the leading top-level folder segment from a `webkitRelativePath`.
 *
 * A folder picker yields paths like `MyExport/activities.csv` and
 * `MyExport/activities/100.gpx`. The backend matches `activities.csv` at
 * the archive ROOT (exact lowercase `activities.csv`, no path) and keys
 * the CSV `Filename` column against member paths via a normalize that
 * lowercases + strips `.gz`. So we drop the first segment to land
 * `activities.csv` at root and keep `activities/100.gpx` as-is — exactly
 * the real Strava export layout the parser expects.
 *
 * Falls back to the bare filename if there is no relative path.
 */
export function stripTopFolder(relativePath: string, fallbackName: string): string {
  const rel = (relativePath || '').replace(/\\/g, '/').replace(/^\.?\//, '');
  if (!rel) return fallbackName;
  const slash = rel.indexOf('/');
  if (slash === -1) return rel; // already at root (no top folder)
  const stripped = rel.slice(slash + 1);
  return stripped || fallbackName;
}

function isActivitiesCsv(path: string): boolean {
  return path.toLowerCase() === 'activities.csv';
}

/**
 * Build the zip member plan from a folder picker FileList.
 *
 * - Keeps only `activities.csv` (the sport key) + `.gpx/.fit` members
 *   (optionally `.gz`), which are all the backend parser reads.
 * - Strips the top-level folder so the layout mirrors a real Strava
 *   export (`activities.csv` at root, `activities/*.gpx`).
 * - Validates the kept set against the backend caps and throws a
 *   `FolderZipError` with a human message if exceeded.
 *
 * Pure + synchronous → unit-testable with synthetic File objects.
 */
export function planFolderZip(files: File[]): FolderZipPlan {
  const members: FolderZipMember[] = [];
  let totalBytes = 0;
  let hasCsv = false;
  let gpxFitCount = 0;

  for (const file of files) {
    // `webkitRelativePath` is set by `<input webkitdirectory>`; in tests
    // we allow passing a plain object shaped like File.
    const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath ?? '';
    const path = stripTopFolder(rel, file.name);

    const csv = isActivitiesCsv(path);
    const keep = csv || GPX_FIT_RE.test(path);
    if (!keep) continue;

    if (file.size > MAX_ZIP_MEMBER_UNCOMPRESSED) {
      throw new FolderZipError(
        `« ${path} » fait ${(file.size / (1024 * 1024)).toFixed(1)} Mo — la limite par fichier est de ${MAX_ZIP_MEMBER_UNCOMPRESSED / (1024 * 1024)} Mo.`,
      );
    }

    if (csv) hasCsv = true;
    else gpxFitCount++;
    members.push({ path, file });
    totalBytes += file.size;
  }

  if (gpxFitCount === 0) {
    throw new FolderZipError(
      "Aucun fichier .gpx / .fit trouvé dans ce dossier. Choisissez le dossier racine de votre export Strava (celui qui contient activities.csv).",
    );
  }

  if (members.length > MAX_ZIP_MEMBERS) {
    throw new FolderZipError(
      `Ce dossier contient ${members.length} fichiers — la limite est de ${MAX_ZIP_MEMBERS}. Importez par lots plus petits.`,
    );
  }

  if (totalBytes > MAX_ZIP_TOTAL_UNCOMPRESSED) {
    throw new FolderZipError(
      `Ce dossier pèse ${(totalBytes / (1024 * 1024)).toFixed(0)} Mo — la limite est de ${MAX_ZIP_TOTAL_UNCOMPRESSED / (1024 * 1024)} Mo. Importez par lots plus petits.`,
    );
  }

  return { members, totalBytes, hasCsv };
}

/**
 * Pack a folder picker FileList into a single ZIP Blob mirroring the
 * Strava export layout. Throws `FolderZipError` (caps) on a bad folder.
 *
 * JSZip is imported lazily so it stays out of the initial bundle.
 */
export async function buildFolderZip(files: File[]): Promise<{ blob: Blob; plan: FolderZipPlan }> {
  const plan = planFolderZip(files);
  const { default: JSZip } = await import('jszip');
  const zip = new JSZip();
  for (const { path, file } of plan.members) {
    zip.file(path, file);
  }
  const blob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' });
  return { blob, plan };
}
