# Komoot-parity roadmap

Plan to close the gap between our routing UX and Komoot/Garmin's
"instant drag-drop" feel. Started 2026-05-01 after a Montpellier-only
end-to-end validation found that the motto test (Montpellier → Anduze
50km offroad) returns 0 km because the routing graph never loads in the
browser.

## Root cause from validation

| Symptom | Underlying cause |
|---|---|
| Motto test fails: distance = 0 km | No graph loaded in browser |
| `.fgraph` 24 MB → frontend skips it | Hardcoded 5 MB limit in `routing-worker-wasm.js` |
| Falls back to `area.pb` API | API takes 129 s for 21 MB → frontend aborts |
| Re-fetch on every reload | No client-side cache |

See [montpellier-validation-2026-05-01.md](montpellier-validation-2026-05-01.md)
for full perf audit and measurements.

## Phase 1 — Quick wins (in flight, parallel agents)

Three agents working concurrently, each on its own branch. Each PR ships
with a regression test. After all three land, we merge to a single PR
`perf/komoot-parity-phase1` and run the motto test as integration check.

| # | Task | Branch | Owner | Acceptance |
|---|---|---|---|---|
| 1 | `area.pb` ≤ 2 s (gzip + edge cap + stream + index check) | `perf/area-pb-speedup` | Agent | curl `area.pb?radius_km=50` < 2 s |
| 2 | Raise `.fgraph` 5 MB → 30 MB (it's already in a Worker) | `perf/fgraph-worker-parse` | Agent | 24 MB partition loads, > 100 K vertices |
| 3 | IndexedDB cache for `.fgraph` (ETag invalidation, 7-day TTL) | `feat/routing-graph-indexeddb` | Agent | Second visit served from cache, < 50 ms |

**Integration test (after all 3 land):**
```bash
LC_ALL=C npx playwright test motto-montpellier-anduze
# Expected: distance 50–150 km, engine RUST WASM, total < 30 s
```

## Phase 2 — Tile-on-demand (Garmin-style)

After Phase 1 unblocks the basic flow, switch the primary load path from
"download whole partition" to "load z14 tiles on demand as you pan/zoom".

The endpoint already exists: `/routing/graph/{sport}/{z}/{x}/{y}.pb` (with a `.json` variant for legacy clients).
Each tile is ~10–100 KB. This naturally scales to any region without
hitting a 5/30 MB limit.

| Step | What | Where |
|---|---|---|
| 2a | Make tile API the default load path in `routing-worker-client.ts` | `frontend/lib/routing-worker-client.ts` |
| 2b | Predictive prefetch (load adjacent tiles ahead of pan) | `frontend/hooks/useRouteEditor*` |
| 2c | Partition `.fgraph` becomes optional warmup, not required | worker logic |

## Phase 3 — Differential drag updates

The actual "Komoot magic". When a user drags a waypoint:
- Today: re-route start → drag → end fully (~50 ms with WASM CH — already fast)
- Komoot: only the segments connected to the dragged waypoint recompute (~5 ms)

Requires:
- Waypoint-keyed segment cache in `routing-worker.ts`
- Reused Dijkstra heap (Rust-side change in `wasm-router/src/`)
- Optimistic UI rendering during drag

This is a 1–2 week design + implementation, after Phase 1 and 2 prove
the pipeline is solid.

## Phase 4 — Polish

- Service Worker for full offline routing (graph cached → no network)
- Brotli + custom binary format (FlatBuffers, varint coords) — ~30 % size win
- Hierarchical CH for cross-region routes

## Why we're not just copying Komoot server-side

Komoot's drag-drop *feels* instant — almost certainly client-side too.
What differs is operational cost: their fleet is large, our budget is
~$10/month Cloud Run scale-to-zero. Client-side WASM lets us match the
UX while staying open-source and cheap.

The first three phases align our architecture with theirs (mostly
client-side, partition + tile hybrid, persistent cache). Phase 3 is
where we get to compete on substance, not just match.
