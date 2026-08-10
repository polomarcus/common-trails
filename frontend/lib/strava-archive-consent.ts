/**
 * Pure logic for the "import my OWN Strava archive to contribute to the
 * community heatmap" wizard.
 *
 * The wizard is a legally-distinct intake path: the user requests their own
 * data via Strava's official export ("Request Your Archive"), downloads the
 * emailed ZIP, and uploads it here WITH EXPLICIT CONSENT to contribute the
 * (anonymised) traces to the open ODbL community heatmap. This is a different
 * basis from Strava-API-extracted data (which the 2026 API Policy forbids
 * redistributing into a public heatmap).
 *
 * This module is PURE (no DOM / no fetch) so the consent-gating can be
 * unit-tested without a browser. The component wires it to the UI.
 */

// Strava's official help article on bulk data export — clearer + less scary
// than the raw settings URL (which is titled like account deletion). Present
// this as the "how-to" link.
export const STRAVA_BULK_EXPORT_HELP_URL =
  'https://support.strava.com/hc/en-us/articles/216918437-Exporting-your-Data-and-Bulk-Export';

// The real Strava settings page hosting "Download or Delete Your Account" →
// "Request Your Archive" (Strava then emails the ZIP). Its URL/title mention
// deletion, which scares users — always label it clearly as the DATA EXPORT
// page where nothing is deleted.
export const STRAVA_ARCHIVE_REQUEST_URL =
  'https://www.strava.com/athlete/delete_your_account';

// Bump this string whenever the legal wording materially changes; the backend
// stores it verbatim in contribution_consents as the audit trail.
// v2 (2026-07): source-neutral wording — the ONE checkbox now covers any
// self-sourced traces (Strava archive, loose GPX or FIT), not only "my own
// Strava archive" (MVP-audit gap #7).
// v3 (2026-07): raw-trace privacy model. The old "anonymisées" claim is dropped
// (the community map now publishes precise traces, protected by endpoint
// masking + a tunable distinct-user gate — NOT K-anonymised aggregation). The
// wording is mode-agnostic (true whether traces are internally matched or drawn
// raw) and states the honest protections: start/end masked, open ODbL licence,
// deletable at any time. Ship this WITH the raw-trace cutover
// (docs/raw-trace-cutover-runbook.md step (e)).
// v4 (2026-08-09): consent label trimmed to the essential grant (removed the
// "Je confirme déposer mes propres traces…" preamble). Same substance (ODbL
// contribution + endpoint masking) — bumped for a clean audit trail.
export const CONTRIBUTION_CONSENT_VERSION = 'contribution-2026-08-v4';

export interface ArchiveConsentState {
  consentChecked: boolean;
  fileCount: number;
  submitting: boolean;
}

/**
 * Whether the archive upload may proceed. Consent is REQUIRED and at least
 * one file must be selected; never while a submit is already in flight.
 */
export function canSubmitArchive(state: ArchiveConsentState): boolean {
  return state.consentChecked && state.fileCount > 0 && !state.submitting;
}

export interface ArchiveConsentPayload {
  consent: string;
  consent_version: string;
  consent_text: string;
  locale: string;
}

/**
 * Build the multipart form fields the backend `/imports/strava-archive`
 * endpoint requires. `consentText` is the exact wording the user saw, stored
 * verbatim as the audit trail.
 */
export function buildConsentPayload(
  consentText: string,
  locale: string,
): ArchiveConsentPayload {
  return {
    consent: 'true',
    consent_version: CONTRIBUTION_CONSENT_VERSION,
    consent_text: consentText,
    locale,
  };
}

/**
 * Multipart fields carrying the ODbL consent on the loose GPX/FIT path
 * (`POST /imports/files`) — MVP-audit gap #7.
 *
 * The dropzone loops ONE request per file, but the audit trail must be ONE
 * `contribution_consents` row per upload batch. So this is a consent_id
 * round-trip:
 *   - first request (`consentId === null`): send version + exact wording +
 *     locale → the backend records the row and returns its `consent_id`;
 *   - subsequent requests: send only that `consent_id` → the backend
 *     re-checks ownership and reuses the row (nothing new recorded).
 */
export function buildFilesConsentFields(
  consentText: string,
  locale: string,
  consentId: string | null,
): Record<string, string> {
  if (consentId) return { consent_id: consentId };
  return {
    consent_version: CONTRIBUTION_CONSENT_VERSION,
    consent_text: consentText,
    locale,
  };
}
