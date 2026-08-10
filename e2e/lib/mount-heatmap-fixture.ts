/**
 * Mount a Layer-3 heatmap fixture into the dev DB.
 *
 * Companion to `backend/app/cli/{dump,load}_heatmap_fixture.py`. The
 * Playwright spec calls `mountHeatmapFixture(absPath)` before placing
 * waypoints — the loader TRUNCATEs the affected bbox + sport and
 * INSERTs the fixture rows, leaving the DB in a known state for the
 * test gesture.
 *
 * See `docs/drag-edit-e2e-testing-strategy.md` (Layer 3) for the
 * motivation. The loader is idempotent (per its tests) so re-mounting
 * is safe.
 *
 * Usage:
 *   import { mountHeatmapFixture } from '../lib/mount-heatmap-fixture';
 *   await mountHeatmapFixture(path.resolve(__dirname, '../fixtures/clapiers-layer-3.json'));
 */
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';

export interface MountResult {
  loaded: true;
  heat_edges_deleted: number;
  osm_road_edges_deleted: number;
  heat_edges_inserted: number;
  osm_road_edges_inserted: number;
}

export interface FixtureMetadata {
  version: number;
  bbox: [number, number, number, number];
  sport: string;
  min_user_count: number;
  heat_edge_count: number;
  osm_road_edge_count: number;
}

/**
 * Read the fixture file and return its metadata (bbox, sport, row
 * counts). Pure JSON parse — no DB access. Used by the spec to know
 * where to centre the map without re-querying the DB.
 */
export function readFixtureMetadata(fixturePath: string): FixtureMetadata {
  if (!existsSync(fixturePath)) {
    throw new Error(`fixture not found: ${fixturePath}`);
  }
  const raw = readFileSync(fixturePath, 'utf-8');
  const json = JSON.parse(raw) as {
    version: number;
    bbox: [number, number, number, number];
    sport: string;
    min_user_count: number;
    heat_edges: unknown[];
    osm_road_edges: unknown[];
  };
  return {
    version: json.version,
    bbox: json.bbox,
    sport: json.sport,
    min_user_count: json.min_user_count,
    heat_edge_count: json.heat_edges.length,
    osm_road_edge_count: json.osm_road_edges.length,
  };
}

/**
 * Pipe the fixture into `python -m app.cli.load_heatmap_fixture` inside
 * the `backend` container. Returns the row counts.
 *
 * Container name defaults to `common-trails-backend-1` (docker-compose
 * default). Override via `BACKEND_CONTAINER` env if your compose
 * project uses a different prefix.
 */
export function mountHeatmapFixture(fixturePath: string): MountResult {
  if (!existsSync(fixturePath)) {
    throw new Error(`fixture not found: ${fixturePath}`);
  }
  const container = process.env.BACKEND_CONTAINER || 'common-trails-backend-1';
  const raw = readFileSync(fixturePath, 'utf-8');

  // Pipe the fixture JSON into the container's stdin. `docker exec -i`
  // keeps stdin open. The Python CLI reads from stdin when no
  // --fixture arg is passed.
  const out = execFileSync(
    'docker',
    ['exec', '-i', container, 'python', '-m', 'app.cli.load_heatmap_fixture'],
    {
      input: raw,
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'pipe'],
      // Generous timeout: a 1 MB fixture with ~5k rows takes seconds,
      // not minutes. 60 s headroom covers cold-start DB pool warmup.
      timeout: 60_000,
    },
  );

  // The Python CLI logs a human line to stderr and emits one JSON line
  // to stdout. Parse the last non-empty stdout line as JSON.
  const lines = out.split('\n').map((l) => l.trim()).filter(Boolean);
  if (lines.length === 0) {
    throw new Error('load_heatmap_fixture produced no stdout');
  }
  const parsed = JSON.parse(lines[lines.length - 1]) as MountResult;
  if (parsed.loaded !== true) {
    throw new Error(`load_heatmap_fixture did not report loaded: ${JSON.stringify(parsed)}`);
  }
  return parsed;
}
