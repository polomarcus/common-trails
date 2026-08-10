import { describe, it, expect } from 'vitest';
import { buildUploadSummary } from '../strava-upload-summary';

// Fake translator: echoes "key(count,s)" so assertions stay readable and the
// test drives the REAL formatter (no inline mirror of its logic).
const t = (key: string, vars?: Record<string, string | number>): string => {
  if (key === 'strava.heatNote') return ' + heat';
  if (!vars) return key;
  return `${key}[${vars.count}${vars.s ?? ''}]`;
};

describe('buildUploadSummary', () => {
  it('joins present buckets with a middot and appends the heat note when something imported', () => {
    expect(buildUploadSummary({ imported: 3, skipped: 1, failed: 0 }, t)).toBe(
      'strava.resultImported[3s] · strava.resultSkipped[1] + heat',
    );
  });

  it('omits zero buckets', () => {
    expect(buildUploadSummary({ imported: 1, skipped: 0, failed: 0 }, t)).toBe(
      'strava.resultImported[1] + heat',
    );
  });

  it('singular vs plural s flag', () => {
    expect(buildUploadSummary({ imported: 2, skipped: 0, failed: 0 }, t)).toContain('[2s]');
    expect(buildUploadSummary({ imported: 1, skipped: 0, failed: 0 }, t)).toContain('[1]');
  });

  it('no heat note when nothing imported (only failures/skips)', () => {
    const out = buildUploadSummary({ imported: 0, skipped: 0, failed: 2 }, t);
    expect(out).toBe('strava.resultFailed[2s]');
    expect(out).not.toContain('heat');
  });

  it('empty result is an empty string', () => {
    expect(buildUploadSummary({ imported: 0, skipped: 0, failed: 0 }, t)).toBe('');
  });
});
