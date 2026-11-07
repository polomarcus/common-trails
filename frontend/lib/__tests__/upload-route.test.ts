import { describe, it, expect } from 'vitest';

import { chooseUploadPath, countUndeclaredSport, gpxDeclaresSport, isZipName } from '../upload-route';

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

describe('gpxDeclaresSport (pre-send recap wording only)', () => {
  it('a specific <type> declares a sport', () => {
    for (const t of ['mountain_biking', 'GravelRide', 'running', 'Hike', '9']) {
      expect(gpxDeclaresSport(`<gpx><trk><type>${t}</type></trk></gpx>`)).toBe(true);
    }
  });
  it('missing, empty or generic <type> does not', () => {
    expect(gpxDeclaresSport('<gpx><trk><name>x</name></trk></gpx>')).toBe(false);
    for (const t of ['', 'cycling', 'Ride', 'biking', '1', 'E-Biking']) {
      expect(gpxDeclaresSport(`<gpx><trk><type>${t}</type></trk></gpx>`)).toBe(false);
    }
  });
});

describe('countUndeclaredSport', () => {
  it('counts GPX without a usable sport; FIT carries its device sport', async () => {
    const files = [
      new File(['<gpx><trk><type>cycling</type></trk></gpx>'], 'a.gpx'),
      new File(['<gpx><trk></trk></gpx>'], 'b.GPX'),
      new File(['<gpx><trk><type>trail_running</type></trk></gpx>'], 'c.gpx'),
      new File([new Uint8Array([14, 16])], 'd.fit'),
    ];
    expect(await countUndeclaredSport(files)).toBe(2);
  });
});
