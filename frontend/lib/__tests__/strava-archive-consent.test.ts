import { describe, it, expect } from 'vitest';

import {
  CONTRIBUTION_CONSENT_VERSION,
  STRAVA_ARCHIVE_REQUEST_URL,
  STRAVA_BULK_EXPORT_HELP_URL,
  buildConsentPayload,
  buildFilesConsentFields,
  canSubmitArchive,
} from '../strava-archive-consent';

describe('canSubmitArchive — consent is required', () => {
  it('blocks when consent is not checked (even with files)', () => {
    expect(canSubmitArchive({ consentChecked: false, fileCount: 5, submitting: false })).toBe(false);
  });

  it('blocks when no files are selected (even with consent)', () => {
    expect(canSubmitArchive({ consentChecked: true, fileCount: 0, submitting: false })).toBe(false);
  });

  it('blocks while a submit is already in flight', () => {
    expect(canSubmitArchive({ consentChecked: true, fileCount: 3, submitting: true })).toBe(false);
  });

  it('allows only with consent + files + not submitting', () => {
    expect(canSubmitArchive({ consentChecked: true, fileCount: 3, submitting: false })).toBe(true);
  });
});

describe('buildConsentPayload — audit fields', () => {
  it('carries consent=true, the version constant and the verbatim text', () => {
    const text = 'I consent to contribute my traces (ODbL).';
    const p = buildConsentPayload(text, 'en');
    expect(p.consent).toBe('true');
    expect(p.consent_version).toBe(CONTRIBUTION_CONSENT_VERSION);
    expect(p.consent_text).toBe(text);
    expect(p.locale).toBe('en');
  });
});

describe('buildFilesConsentFields — /imports/files consent round-trip (audit gap #7)', () => {
  it('first request (no consent_id yet) carries version + verbatim text + locale', () => {
    const text = 'I consent to contribute my traces (ODbL).';
    const fields = buildFilesConsentFields(text, 'fr', null);
    expect(fields).toEqual({
      consent_version: CONTRIBUTION_CONSENT_VERSION,
      consent_text: text,
      locale: 'fr',
    });
    // The one thing that must NEVER happen on the first request: sending a
    // consent_id we don't have (the backend would 422 the whole upload).
    expect(fields.consent_id).toBeUndefined();
  });

  it('subsequent requests reuse the batch consent row via consent_id ONLY', () => {
    const fields = buildFilesConsentFields('whatever wording', 'en', 'consent-123');
    expect(fields).toEqual({ consent_id: 'consent-123' });
  });

  it('the consent version is bumped whenever the wording changes (v5)', () => {
    // Each material wording change bumps the audit version. v5 (2026-09-05)
    // added the dual-licensing grant: the contributor also grants the project
    // a non-exclusive right to license the contribution commercially. A
    // MATERIAL new grant → new version; v1-v4 rows remain ODbL-only.
    expect(CONTRIBUTION_CONSENT_VERSION).toBe('contribution-2026-09-v5');
  });
});

describe('STRAVA_ARCHIVE_REQUEST_URL points at the real Strava archive request page', () => {
  it('is the strava.com account/archive URL', () => {
    expect(STRAVA_ARCHIVE_REQUEST_URL).toMatch(/^https:\/\/www\.strava\.com\//);
  });
});

describe('STRAVA_BULK_EXPORT_HELP_URL points at the official (less scary) help article', () => {
  it('is the Strava support bulk-export article', () => {
    expect(STRAVA_BULK_EXPORT_HELP_URL).toMatch(/^https:\/\/support\.strava\.com\//);
    expect(STRAVA_BULK_EXPORT_HELP_URL).toContain('Bulk-Export');
  });
});
