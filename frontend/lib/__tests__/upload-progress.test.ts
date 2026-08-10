import { describe, it, expect } from 'vitest';

import {
  describeUpload,
  phaseForStage,
  toPct,
  type UploadPhase,
} from '../upload-progress';

describe('toPct — clamp + round a 0..1 fraction', () => {
  it('rounds to an integer percentage', () => {
    expect(toPct(0.5)).toBe(50);
    expect(toPct(0.333)).toBe(33);
    expect(toPct(0.999)).toBe(100);
  });
  it('clamps out-of-range and non-finite input to a safe percentage', () => {
    expect(toPct(-0.2)).toBe(0);
    expect(toPct(1.5)).toBe(100);
    expect(toPct(NaN)).toBe(0);
    expect(toPct(Infinity)).toBe(100);
  });
});

describe('describeUpload — the single UI mapping', () => {
  it('idle shows nothing and is not busy', () => {
    const v = describeUpload('idle');
    expect(v.showBar).toBe(false);
    expect(v.labelKey).toBeNull();
    expect(v.busy).toBe(false);
    expect(v.tone).toBe('neutral');
  });

  it('preparing shows an indeterminate active bar and locks the pickers', () => {
    const v = describeUpload('preparing');
    expect(v.showBar).toBe(true);
    expect(v.indeterminate).toBe(true);
    expect(v.tone).toBe('active');
    expect(v.labelKey).toBe('strava.archive.preparing');
    expect(v.busy).toBe(true);
  });

  it('uploading reflects byte fraction as an integer pct with interpolation vars', () => {
    const v = describeUpload('uploading', 0.42);
    expect(v.showBar).toBe(true);
    expect(v.indeterminate).toBe(false);
    expect(v.pct).toBe(42);
    expect(v.labelKey).toBe('strava.archive.uploadingPct');
    expect(v.labelVars).toEqual({ pct: 42 });
    expect(v.busy).toBe(true);
  });

  it('finalizing pins the bar at 100 and stays busy', () => {
    const v = describeUpload('finalizing');
    expect(v.pct).toBe(100);
    expect(v.indeterminate).toBe(true);
    expect(v.labelKey).toBe('strava.archive.finalizing');
    expect(v.busy).toBe(true);
  });

  it('done is a success state, no bar, not busy, reassuring label', () => {
    const v = describeUpload('done');
    expect(v.showBar).toBe(false);
    expect(v.tone).toBe('success');
    expect(v.busy).toBe(false);
    expect(v.labelKey).toBe('strava.archive.acceptedQueued');
  });

  it('error defers the label to the component (concrete message) and unlocks', () => {
    const v = describeUpload('error');
    expect(v.tone).toBe('error');
    expect(v.labelKey).toBeNull();
    expect(v.busy).toBe(false);
    expect(v.showBar).toBe(false);
  });

  it('busy is true only while a submission is in flight', () => {
    const busyPhases: UploadPhase[] = ['preparing', 'uploading', 'finalizing'];
    const idlePhases: UploadPhase[] = ['idle', 'done', 'error'];
    for (const p of busyPhases) expect(describeUpload(p).busy, p).toBe(true);
    for (const p of idlePhases) expect(describeUpload(p).busy, p).toBe(false);
  });
});

describe('phaseForStage — uploadArchive stage → UI phase', () => {
  it('init and uploading both read as uploading (bar visible from 0%)', () => {
    expect(phaseForStage('init')).toBe('uploading');
    expect(phaseForStage('uploading')).toBe('uploading');
  });
  it('finalizing and done map through', () => {
    expect(phaseForStage('finalizing')).toBe('finalizing');
    expect(phaseForStage('done')).toBe('done');
  });
});
