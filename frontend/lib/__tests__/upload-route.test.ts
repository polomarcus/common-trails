import { describe, it, expect } from 'vitest';

import { chooseUploadPath, isZipName } from '../upload-route';

describe('isZipName', () => {
  it('matches .zip case-insensitively', () => {
    expect(isZipName('export.zip')).toBe(true);
    expect(isZipName('EXPORT.ZIP')).toBe(true);
    expect(isZipName('  strava-archive.zip  ')).toBe(true);
  });
  it('does not match gpx/fit or a .zip in the middle', () => {
    expect(isZipName('ride.gpx')).toBe(false);
    expect(isZipName('ride.fit')).toBe(false);
    expect(isZipName('archive.zip.gpx')).toBe(false);
  });
});

describe('chooseUploadPath', () => {
  it('empty / null → { kind: "empty" }', () => {
    expect(chooseUploadPath(null)).toEqual({ kind: 'empty' });
    expect(chooseUploadPath(undefined)).toEqual({ kind: 'empty' });
    expect(chooseUploadPath([])).toEqual({ kind: 'empty' });
  });

  it('a single .zip → archive path with that file', () => {
    const zip = { name: 'strava_export.zip' };
    expect(chooseUploadPath([zip])).toEqual({ kind: 'archive', file: zip });
  });

  it('a big Strava .zip mixed with loose files → archive path (first .zip wins)', () => {
    const zip1 = { name: 'export.zip' };
    const gpx = { name: 'ride.gpx' };
    const zip2 = { name: 'second.zip' };
    expect(chooseUploadPath([gpx, zip1, zip2])).toEqual({ kind: 'archive', file: zip1 });
  });

  it('GPX / FIT only → files path with all of them', () => {
    const files = [{ name: 'a.gpx' }, { name: 'b.FIT' }, { name: 'c.gpx' }];
    expect(chooseUploadPath(files)).toEqual({ kind: 'files', files });
  });

  it('accepts an ArrayLike (FileList-shaped) input', () => {
    const arrayLike = { 0: { name: 'ride.gpx' }, length: 1 } as ArrayLike<{ name: string }>;
    expect(chooseUploadPath(arrayLike)).toEqual({ kind: 'files', files: [{ name: 'ride.gpx' }] });
  });
});
