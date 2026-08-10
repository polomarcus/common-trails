/**
 * Unit tests for GPX export/parse utilities.
 */
import { describe, it, expect, beforeAll } from 'vitest';
import { waypointsToGpx, parseGpxFile, ANNOTATION_ICONS, ANNOTATION_EMOJI_MAP } from '@/lib/gpx-utils';

// JSDOM provides DOMParser for parseGpxFile
beforeAll(async () => {
  // @ts-expect-error jsdom has no types installed
  const { JSDOM } = await import('jsdom');
  const dom = new JSDOM();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (global as any).DOMParser = dom.window.DOMParser;
});

describe('waypointsToGpx', () => {
  it('produces valid GPX 1.1 with trkpts', () => {
    const pts: [number, number][] = [[2.35, 48.85], [2.36, 48.86]];
    const gpx = waypointsToGpx(pts, 'Test Route');
    expect(gpx).toContain('<?xml version="1.0"');
    expect(gpx).toContain('<gpx version="1.1"');
    expect(gpx).toContain('<name>Test Route</name>');
    expect(gpx).toContain('lat="48.850000" lon="2.350000"');
    expect(gpx).toContain('lat="48.860000" lon="2.360000"');
  });

  it('includes annotations as <wpt> elements with emojis', () => {
    const pts: [number, number][] = [[2.35, 48.85]];
    const annotations = [{ lon: 2.355, lat: 48.855, icon: 'water', text: 'Fontaine' }];
    const gpx = waypointsToGpx(pts, 'Test', annotations);
    expect(gpx).toContain('<wpt lat="48.855000" lon="2.355000">');
    expect(gpx).toContain('<type>water</type>');
    // Emoji prefix from ANNOTATION_EMOJI_MAP
    expect(gpx).toContain(ANNOTATION_EMOJI_MAP.water);
  });

  it('escapes XML special characters in annotation text', () => {
    const pts: [number, number][] = [[0, 0]];
    const annotations = [{ lon: 0, lat: 0, icon: 'info', text: 'A & B < C' }];
    const gpx = waypointsToGpx(pts, 'Test', annotations);
    expect(gpx).toContain('&amp;');
    expect(gpx).toContain('&lt;');
  });

  it('uses default name when not provided', () => {
    const gpx = waypointsToGpx([[0, 0]]);
    expect(gpx).toContain('<name>Mon itinéraire</name>');
  });
});

describe('parseGpxFile', () => {
  it('returns null for invalid XML', () => {
    expect(parseGpxFile('not xml')).toBeNull();
  });

  it('returns null for GPX with < 2 trkpts', () => {
    const xml = `<?xml version="1.0"?><gpx><trk><trkseg><trkpt lat="0" lon="0"/></trkseg></trk></gpx>`;
    expect(parseGpxFile(xml)).toBeNull();
  });

  it('parses valid GPX and computes distance', () => {
    const xml = `<?xml version="1.0"?>
      <gpx>
        <trk>
          <name>My Track</name>
          <trkseg>
            <trkpt lat="48.85" lon="2.35"/>
            <trkpt lat="48.86" lon="2.36"/>
          </trkseg>
        </trk>
      </gpx>`;
    const result = parseGpxFile(xml);
    expect(result).not.toBeNull();
    expect(result!.coords).toHaveLength(2);
    expect(result!.name).toBe('My Track');
    expect(result!.distanceKm).toBeGreaterThan(0);
    expect(result!.bbox).toEqual([2.35, 48.85, 2.36, 48.86]);
  });

  it('extracts elevation when <ele> present', () => {
    const xml = `<?xml version="1.0"?>
      <gpx>
        <trk><trkseg>
          <trkpt lat="0" lon="0"><ele>100</ele></trkpt>
          <trkpt lat="0.001" lon="0"><ele>110</ele></trkpt>
          <trkpt lat="0.002" lon="0"><ele>105</ele></trkpt>
        </trkseg></trk>
      </gpx>`;
    const result = parseGpxFile(xml);
    expect(result).not.toBeNull();
    expect(result!.coords[0][2]).toBe(100);
    expect(result!.elevGain).toBe(10);  // 100→110
    expect(result!.elevLoss).toBe(5);   // 110→105
  });

  it('uses default name when <name> missing', () => {
    const xml = `<?xml version="1.0"?>
      <gpx><trk><trkseg>
        <trkpt lat="0" lon="0"/><trkpt lat="0.001" lon="0"/>
      </trkseg></trk></gpx>`;
    const result = parseGpxFile(xml);
    expect(result!.name).toBe('GPX Preview');
  });
});

describe('ANNOTATION_ICONS', () => {
  it('exports 8 standard cycling annotation types', () => {
    expect(ANNOTATION_ICONS).toHaveLength(8);
    const keys = ANNOTATION_ICONS.map(i => i.key);
    expect(keys).toContain('water');
    expect(keys).toContain('food');
    expect(keys).toContain('viewpoint');
    expect(keys).toContain('danger');
  });

  it('ANNOTATION_EMOJI_MAP has emoji for every icon', () => {
    for (const icon of ANNOTATION_ICONS) {
      expect(ANNOTATION_EMOJI_MAP[icon.key]).toBe(icon.emoji);
    }
  });
});
