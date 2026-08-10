---
title: 'Story 1.8: Web Worker Routing Engine — Build Fix & Integration'
slug: 'web-worker-routing-build-fix'
created: '2026-03-15'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Next.js 16 (Turbopack)', 'TypeScript', 'Web Workers API', 'esbuild']
files_to_modify: ['frontend/lib/routing-worker-client.ts', 'frontend/package.json', '.gitignore']
code_patterns: ['pre-bundled worker via esbuild', 'Promise-based Worker wrapper', 'main-thread fallback']
test_patterns: ['npm run build', 'Playwright E2E (existing)']
---

# Tech-Spec: Story 1.8 — Web Worker Routing Engine — Build Fix & Integration

**Created:** 2026-03-15

## Overview

### Problem Statement

The Web Worker routing infrastructure is **already fully written** — Worker script, Promise-based client, async drag router, and full page.tsx integration all exist. However, **the build fails** because Next.js 16 (Turbopack) copies the Worker `.ts` file to `out/_next/static/media/routing-worker.*.ts` without bundling its imports. The `import ... from './client-graph'` inside the copied Worker file doesn't resolve from the output location.

This is the only blocking error. All async handler conversions in page.tsx are already done.

### Solution

Pre-bundle the Worker as a self-contained JS file using `esbuild` before the Next.js build. Load it from `public/` instead of using the `new URL()` pattern that Turbopack can't handle. This is the standard approach for Web Workers in static-export Next.js apps.

### Scope

**In Scope:**
- Add `esbuild` prebuild step to bundle `routing-worker.ts` → `public/routing-worker.js`
- Update `RoutingWorkerClient` to load from `/routing-worker.js` instead of `new URL()`
- Handle `basePath` for GCS deployment (build-time replacement via `NEXT_PUBLIC_BASE_PATH`)
- Implement main-thread fallback in `RoutingWorkerClient` (the `useWorker: false` flag exists but methods hang forever when Worker fails)
- Verify `npm run build` passes
- Verify existing E2E tests pass

**Out of Scope:**
- Rewriting Worker or client code (already done and correct)
- New Worker-specific unit tests (follow-up)
- Binary graph format / typed arrays
- IndexedDB tile cache (plan Chunk 5)

## Context for Development

### Codebase Patterns

**Existing Worker infrastructure (all written, all correct):**

| File | Lines | Status |
|------|-------|--------|
| `frontend/lib/routing-worker.ts` | 98 | Worker script: init, mergeEdges, route, snap, geojson, graphSize |
| `frontend/lib/routing-worker-client.ts` | 236 | Promise wrapper with tile loading, reinit, destroy |
| `frontend/lib/snap-to-road.ts` | 188 | Has both sync `createDragRouter()` and async `createAsyncDragRouter()` |
| `frontend/app/map/page.tsx` | ~8000 | Fully migrated: uses `workerRef`, `asyncDragRouterRef`, all async |
| `frontend/lib/client-graph.ts` | 840 | All functions exported, perf instrumentation done |

**Build error (the only issue):**
```
./out/_next/static/media/routing-worker.79bb1885.ts:8:61
Type error: Cannot find module './client-graph' or its corresponding type declarations.
```

Turbopack recognizes the `new URL('./routing-worker.ts', import.meta.url)` pattern and copies the file, but doesn't bundle its imports. This is a known Turbopack limitation with `type: 'module'` workers.

**Main-thread fallback gap (CRITICAL — F1/F4 from adversarial review):**
When `useWorker === false`:
- `route()`, `snap()`, `getGeoJSON()`, `getGraphSize()` call `this.request()` which registers Promises in `this.pending` — but no Worker responds, so **Promises hang forever**.
- `mergeEdges()` registers at hardcoded `id: -1` — also hangs.
- `reinit()` creates a new `_ready` Promise waiting for a Worker 'ready' message — also hangs.
- `ensureTilesLoaded()` calls `this.mergeEdges()` at line 177, which hangs — so **tile loading hangs too**.

**Pre-existing bug (F2):** `mergeEdges()` always uses `id: -1` for pending Promise. Concurrent calls clobber each other. Not in scope to fix but dev should be aware.

### Files to Reference

| File | Purpose |
|------|---------|
| `frontend/lib/routing-worker.ts` | Worker script — will be bundled by esbuild |
| `frontend/lib/routing-worker-client.ts` | Needs Worker URL change + fallback implementation |
| `frontend/package.json` | Add esbuild dep + prebuild script |
| `frontend/next.config.js` | Reference: `basePath` / `DEPLOY_TARGET`, `NEXT_PUBLIC_BASE_PATH` |
| `frontend/public/` | Target for pre-bundled worker |
| `.gitignore` | Root gitignore (no `frontend/.gitignore` exists) |

### Technical Decisions

1. **esbuild prebuild** — Standard, fast (~50ms), produces a single self-contained JS file. No Turbopack config needed.
2. **Load from `/routing-worker.js`** — Static file in `public/`, works with `output: 'export'`, supports `basePath`.
3. **`--format=iife`** — Required because the Worker is loaded with `new Worker(url)` (no `{ type: 'module' }`). ESM syntax (`import`/`export`) would fail at parse time. `self.onmessage` works because `self` resolves via the scope chain to `DedicatedWorkerGlobalScope`.
4. **`--tree-shaking=true`** — Explicit flag to minimize bundle size. Without it, esbuild includes all of `client-graph.ts` + `tile-cache.ts` (~950 lines of dead code).
5. **`NEXT_PUBLIC_BASE_PATH`** — This is a **build-time** constant, replaced by Next.js during `next build`. Not a runtime env var. This is consistent with existing usage across the codebase (`layout.tsx`, `page.tsx`, `activities/page.tsx`).
6. **Main-thread fallback** — Maintain a local `ClientGraph` instance on the class. Fallback methods return `Promise.resolve(result)` for API compatibility. The `_ready` Promise must be resolved immediately in fallback mode.

## Implementation Plan

### Tasks

- [ ] **Task 1: Add esbuild and prebuild script**
  - File: `frontend/package.json`
  - Action: Add `esbuild` as devDependency. Add prebuild script and chain into build:
    ```json
    "prebuild:worker": "esbuild lib/routing-worker.ts --bundle --format=iife --tree-shaking=true --outfile=public/routing-worker.js",
    "build": "npm run prebuild:worker && next build"
    ```
  - Notes: `--tree-shaking=true` prevents bundling unused functions from `client-graph.ts` (e.g., `loadTile`, `fetchTileEdges`, `tile-cache.ts`). The Worker only imports 6 functions.

- [ ] **Task 2: Update RoutingWorkerClient to load pre-bundled worker**
  - File: `frontend/lib/routing-worker-client.ts`
  - Action: Replace lines 35-38:
    ```typescript
    // Before:
    this.worker = new Worker(
      new URL('./routing-worker.ts', import.meta.url),
      { type: 'module' },
    );
    // After:
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '';
    this.worker = new Worker(`${basePath}/routing-worker.js`);
    ```
  - Notes: `NEXT_PUBLIC_BASE_PATH` is replaced at build time by Next.js. Empty string for local dev, `/common-trails-frontend` for GCS. No `{ type: 'module' }` needed (iife format).

- [ ] **Task 3: Implement main-thread fallback**
  - File: `frontend/lib/routing-worker-client.ts`
  - Action: When `useWorker === false`, maintain a local `ClientGraph` on the class and implement synchronous fallback for each method. **Critical details:**

    **3a. Add local graph property:**
    ```typescript
    private localGraph: ClientGraph | null = null;
    ```
    Import needed functions at top of file:
    ```typescript
    import { createGraph, mergeEdges as mergeEdgesLocal, bridgeNearbyVertices,
             clientRoute, snapToRoad, graphToGeoJSON } from './client-graph';
    ```

    **3b. Constructor fallback (lines 78-82):** When Worker init fails, create local graph and resolve `_ready` immediately:
    ```typescript
    catch {
      this.useWorker = false;
      this.localGraph = createGraph(sport);
      resolveReady!(); // resolve _ready immediately — don't leave it hanging
    }
    ```

    **3c. `mergeEdges()` fallback:** Check `useWorker` first. If false, call local functions and return immediately:
    ```typescript
    mergeEdges(edges: RawEdge[], bridge = true): Promise<number> {
      if (!this.useWorker && this.localGraph) {
        const verticesBefore = new Set(this.localGraph.adj.keys());
        mergeEdgesLocal(this.localGraph, edges);
        if (bridge) {
          const newVerts = new Set<string>();
          for (const k of this.localGraph.adj.keys()) {
            if (!verticesBefore.has(k)) newVerts.add(k);
          }
          if (newVerts.size > 0) bridgeNearbyVertices(this.localGraph, newVerts);
        }
        this._graphSize = this.localGraph.adj.size;
        return Promise.resolve(this._graphSize);
      }
      // existing Worker path...
    }
    ```

    **3d. `route()` fallback:**
    ```typescript
    if (!this.useWorker && this.localGraph) {
      return Promise.resolve(clientRoute(this.localGraph, from, to, maxSnapM ?? 500, intentStrength ?? 0));
    }
    ```

    **3e. `snap()` fallback:**
    ```typescript
    if (!this.useWorker && this.localGraph) {
      return Promise.resolve(snapToRoad(this.localGraph, lon, lat, maxM ?? 200));
    }
    ```

    **3f. `getGeoJSON()` fallback:**
    ```typescript
    if (!this.useWorker && this.localGraph) {
      return Promise.resolve(graphToGeoJSON(this.localGraph));
    }
    ```

    **3g. `getGraphSize()` fallback:**
    ```typescript
    if (!this.useWorker && this.localGraph) {
      return Promise.resolve(this.localGraph.adj.size);
    }
    ```

    **3h. `reinit()` fallback:** Recreate graph, clear tiles, resolve `_ready` immediately:
    ```typescript
    async reinit(sport: string): Promise<void> {
      this.loadedTiles.clear();
      this.tilePending.clear();
      this._graphSize = 0;
      if (!this.useWorker) {
        this.localGraph = createGraph(sport);
        return; // _ready is already resolved, no need to create new Promise
      }
      // existing Worker reinit path...
    }
    ```

    **3i. `destroy()` fallback:** Null out local graph:
    ```typescript
    destroy(): void {
      // ... existing cleanup ...
      this.localGraph = null;
    }
    ```

  - Notes: `ensureTilesLoaded()` on the class does NOT need changes — it fetches tiles via `fetchTileEdges()` on the main thread already. The only call that needs redirection is `this.mergeEdges()` at line 177, which is handled by the fallback in 3c above.

- [ ] **Task 4: Add `public/routing-worker.js` to `.gitignore`**
  - File: `.gitignore` (root — no `frontend/.gitignore` exists)
  - Action: Add `frontend/public/routing-worker.js` — it's a build artifact.

- [ ] **Task 5: Verify build and tests**
  - Action: Run `cd frontend && npm install && npm run build` — must pass with zero errors.
  - Action: Verify `frontend/public/routing-worker.js` exists and is self-contained (no `import` statements in the output).
  - Action: Run full E2E suite locally or via CI push.
  - Notes: The Worker integration is already wired in page.tsx — no changes needed there. Existing E2E tests exercise routing through the UI, which now goes through the Worker.

### Acceptance Criteria

- [ ] **AC 1**: Given a fresh `npm install` and `npm run build`, when the build runs, then it completes with zero TypeScript errors and produces `out/` with the static export.

- [ ] **AC 2**: Given a browser that supports Web Workers, when the user enters route mode, then the `RoutingWorkerClient` spawns a Worker that handles all routing off the main thread (verifiable via DevTools → Sources → Workers).

- [ ] **AC 3**: Given a browser where Worker initialization fails (e.g., CSP restriction), when the user enters route mode, then routing still works via the main-thread fallback — `localGraph` is created, `route()`/`snap()`/`getGeoJSON()` return results synchronously (wrapped in `Promise.resolve()`), and no Promises hang.

- [ ] **AC 4**: Given the app is deployed with `DEPLOY_TARGET=gcs` and `basePath=/common-trails-frontend`, when the Worker is loaded, then it resolves from `/common-trails-frontend/routing-worker.js`.

- [ ] **AC 5**: Given the user places 2 waypoints and drags one, when the drag preview updates, then the main thread stays responsive (no long tasks >50ms in Performance tab) because routing runs in the Worker.

## Additional Context

### Dependencies

- `esbuild` — zero-config bundler. Add as devDependency (~0.24.x).
- No other new dependencies needed.
- Depends on: Story 1.7 (performance instrumentation) — already done in `client-graph.ts`.

### Testing Strategy

**Build verification:**
- `npm run build` must pass (zero errors)
- `frontend/public/routing-worker.js` must exist after prebuild
- The built worker JS must be self-contained (grep for `import ` should find zero matches)

**E2E (existing tests):**
- All routing E2E tests exercise the Worker path (page.tsx uses `workerRef` exclusively)
- `client-routing.spec.ts`: graph tiles, server fallback, overlay
- `core.spec.ts`: sport change, waypoint operations
- `routing-heatmap-fixture.spec.ts`: quality tests with real data

**Manual smoke test:**
- DevTools → Sources → Workers: verify `routing-worker.js` appears when route mode is entered
- DevTools → Performance tab during drag: verify no main-thread long tasks
- To test fallback: temporarily add `throw new Error('test')` before `new Worker(...)` in the constructor, verify routing still works

### Notes

**Pre-existing bug (not in scope):** `mergeEdges()` uses hardcoded `id: -1` for its pending Promise. Concurrent `mergeEdges()` calls clobber each other's Promise — the first caller hangs. This is mitigated by `ensureTilesLoaded` batching all tile edges into a single `mergeEdges()` call, but it's technically a race condition. Fix in a follow-up.

**Bundle size:** With `--tree-shaking=true`, the Worker bundle should be ~20-30KB (6 functions from client-graph.ts + their dependencies). Without it, ~60-80KB (entire client-graph.ts + tile-cache.ts). Both are acceptable, but smaller is better.

**Future optimization:**
- `Transferable` for edge arrays (zero-copy transfer instead of structured clone)
- Binary graph format (typed arrays instead of JSON objects)
- `SharedArrayBuffer` for truly shared graph state (requires COOP/COEP headers)
