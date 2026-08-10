/**
 * Unit tests for the client-side folder → ZIP packer (pure parts).
 *
 * These cover the layout + caps logic that decides what goes into the
 * zip POSTed to the CSV-aware `/imports/files` backend path. The actual
 * blob generation (buildFolderZip) needs JSZip + a browser Blob and is
 * covered by the manual-smoke note in the PR.
 */
import { describe, it, expect } from 'vitest';
import {
  stripTopFolder,
  planFolderZip,
  FolderZipError,
  MAX_ZIP_MEMBERS,
  MAX_ZIP_MEMBER_UNCOMPRESSED,
} from '@/lib/folder-zip';

// Minimal File-like stub. `planFolderZip` only reads `name`, `size`,
// `webkitRelativePath` — no real bytes needed.
function fakeFile(relativePath: string, size = 1024): File {
  const name = relativePath.split('/').pop() || relativePath;
  return {
    name,
    size,
    webkitRelativePath: relativePath,
  } as unknown as File;
}

describe('stripTopFolder', () => {
  it('drops the top-level folder so activities.csv lands at root', () => {
    expect(stripTopFolder('MyExport/activities.csv', 'activities.csv')).toBe('activities.csv');
  });
  it('keeps the activities/ subpath for GPX members', () => {
    expect(stripTopFolder('MyExport/activities/100.gpx', '100.gpx')).toBe('activities/100.gpx');
  });
  it('normalizes backslashes and leading ./', () => {
    expect(stripTopFolder('./Export\\activities\\100.gpx', '100.gpx')).toBe('activities/100.gpx');
  });
  it('falls back to filename when there is no relative path', () => {
    expect(stripTopFolder('', '100.gpx')).toBe('100.gpx');
  });
  it('returns the path unchanged when already at root (no top folder)', () => {
    expect(stripTopFolder('activities.csv', 'activities.csv')).toBe('activities.csv');
  });
});

describe('planFolderZip', () => {
  it('keeps activities.csv + gpx/fit and drops everything else', () => {
    const plan = planFolderZip([
      fakeFile('Export/activities.csv'),
      fakeFile('Export/activities/100.gpx'),
      fakeFile('Export/activities/200.fit'),
      fakeFile('Export/activities/300.gpx.gz'),
      fakeFile('Export/media/photo.jpg'),
      fakeFile('Export/README.md'),
    ]);
    expect(plan.hasCsv).toBe(true);
    const paths = plan.members.map((m) => m.path).sort();
    expect(paths).toEqual([
      'activities.csv',
      'activities/100.gpx',
      'activities/200.fit',
      'activities/300.gpx.gz',
    ]);
  });

  it('mirrors the Strava layout: csv at root, gpx under activities/', () => {
    const plan = planFolderZip([
      fakeFile('strava_export_42/activities.csv'),
      fakeFile('strava_export_42/activities/100.gpx'),
    ]);
    const byName = Object.fromEntries(plan.members.map((m) => [m.file.name, m.path]));
    expect(byName['activities.csv']).toBe('activities.csv');
    expect(byName['100.gpx']).toBe('activities/100.gpx');
  });

  it('works without a CSV (hasCsv false) but still keeps gpx', () => {
    const plan = planFolderZip([
      fakeFile('folder/ride1.gpx'),
      fakeFile('folder/ride2.gpx'),
    ]);
    expect(plan.hasCsv).toBe(false);
    expect(plan.members).toHaveLength(2);
  });

  it('throws when no gpx/fit members are present', () => {
    expect(() => planFolderZip([
      fakeFile('folder/activities.csv'),
      fakeFile('folder/notes.txt'),
    ])).toThrow(FolderZipError);
  });

  it('throws when a single member exceeds the per-file cap', () => {
    expect(() => planFolderZip([
      fakeFile('folder/big.gpx', MAX_ZIP_MEMBER_UNCOMPRESSED + 1),
    ])).toThrow(FolderZipError);
  });

  it('throws when the member count exceeds the cap', () => {
    const many = Array.from({ length: MAX_ZIP_MEMBERS + 1 }, (_, i) =>
      fakeFile(`folder/activities/${i}.gpx`, 10),
    );
    expect(() => planFolderZip(many)).toThrow(FolderZipError);
  });

  it('sums totalBytes only over kept members', () => {
    const plan = planFolderZip([
      fakeFile('folder/activities/100.gpx', 500),
      fakeFile('folder/media/big.jpg', 9_000_000), // dropped, not counted
      fakeFile('folder/activities.csv', 200),
    ]);
    expect(plan.totalBytes).toBe(700);
  });
});
