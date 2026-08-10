/**
 * Pure state model for the community-contribution archive upload wizard
 * (StravaArchiveImport). Extracted so the mapping from the raw upload stages
 * (`uploadArchive`'s onStage/onProgress callbacks + the client-side ZIP-build
 * step) to what the UI renders — a progress bar, a label and a semantic tone —
 * is unit-testable without a browser (CLAUDE.md: non-trivial UI logic → pure fn
 * + vitest).
 *
 * No DOM, no fetch, no React. The component owns the `UploadPhase` state; this
 * module answers "given the phase (+ upload fraction), what should the UI
 * show?" — the single source of truth for the bar/label/tone so the visual and
 * the tests can never drift.
 */

export type UploadPhase =
  | 'idle'        // nothing in flight
  | 'preparing'   // zipping a picked folder client-side (no byte progress yet)
  | 'uploading'   // PUTting the .zip straight to storage (byte progress)
  | 'finalizing'  // storage PUT done, calling /complete
  | 'done'        // accepted + queued for progressive ingest
  | 'error';      // something failed

export type UploadTone = 'neutral' | 'active' | 'success' | 'error';

export interface UploadView {
  /** Integer 0..100. Meaningful while uploading; pinned to 100 at finalizing/done. */
  pct: number;
  /** Whether to render the progress bar at all. */
  showBar: boolean;
  /**
   * Bar renders as an indeterminate shimmer (no reliable fraction yet), used
   * for `preparing` and `finalizing` where there is no byte-level signal.
   */
  indeterminate: boolean;
  /** Semantic tone for colour selection in the component. */
  tone: UploadTone;
  /**
   * i18n key for the status label, or null when nothing should be shown (idle).
   * `error` returns null so the component can surface the concrete error string
   * it captured instead of a generic key.
   */
  labelKey: string | null;
  /** Interpolation vars for `labelKey` (e.g. the upload percentage). */
  labelVars?: Record<string, string | number>;
  /** True while a submission is in flight — the pickers/dropzone must be locked. */
  busy: boolean;
}

/** Clamp + round a 0..1 fraction to an integer 0..100 percentage. */
export function toPct(fraction: number): number {
  // NaN is meaningless → 0. ±Infinity clamps naturally through min/max.
  if (Number.isNaN(fraction)) return 0;
  return Math.max(0, Math.min(100, Math.round(fraction * 100)));
}

/**
 * The single mapping the component (and the tests) rely on. `fraction` is the
 * 0..1 byte-progress of the direct-to-storage PUT; it is only consulted while
 * `uploading`.
 */
export function describeUpload(phase: UploadPhase, fraction = 0): UploadView {
  switch (phase) {
    case 'preparing':
      return {
        pct: 0, showBar: true, indeterminate: true, tone: 'active',
        labelKey: 'strava.archive.preparing', busy: true,
      };
    case 'uploading': {
      const pct = toPct(fraction);
      return {
        pct, showBar: true, indeterminate: false, tone: 'active',
        labelKey: 'strava.archive.uploadingPct', labelVars: { pct }, busy: true,
      };
    }
    case 'finalizing':
      return {
        pct: 100, showBar: true, indeterminate: true, tone: 'active',
        labelKey: 'strava.archive.finalizing', busy: true,
      };
    case 'done':
      return {
        pct: 100, showBar: false, indeterminate: false, tone: 'success',
        labelKey: 'strava.archive.acceptedQueued', busy: false,
      };
    case 'error':
      return {
        pct: 0, showBar: false, indeterminate: false, tone: 'error',
        labelKey: null, busy: false,
      };
    case 'idle':
    default:
      return {
        pct: 0, showBar: false, indeterminate: false, tone: 'neutral',
        labelKey: null, busy: false,
      };
  }
}

/**
 * Map an `uploadArchive` stage (`init` | `uploading` | `finalizing` | `done`)
 * to our `UploadPhase`. `init` reads as the start of uploading (the bar appears
 * at 0%); the byte-level `uploading` fraction then drives it.
 */
export function phaseForStage(stage: 'init' | 'uploading' | 'finalizing' | 'done'): UploadPhase {
  switch (stage) {
    case 'init':
    case 'uploading':
      return 'uploading';
    case 'finalizing':
      return 'finalizing';
    case 'done':
      return 'done';
  }
}
