# Drag-edit E2E testing strategy

**Status:** planning doc, 2026-05-31. Companion to [`docs/drag-edit-design.md`](drag-edit-design.md) (the UX contract). Triggered by Paul's question: "how would the perfect E2E test work? It's difficult because you have to know the heatmap on an area (like Clapiers)."

The current state-of-the-art is PR #371's `e2e/tests/drag-edit-quality.spec.ts` — a **Layer 1** test. It pins geometric invariants (no U-turn, no straight-line, waypoint inserted at the right index, route passes near the drag target). It catches CRASHES but not QUALITY: a route that lands on a random OSM road instead of a popular heatmap trail still passes.

To assert "smart" routing — the route follows visible heatmap edges between the drag-target waypoints — we need **heatmap-aware** tests. This is the real challenge.

---

## 1. The core problem

A "smart" drag-edit means:

> The user dragged a waypoint NEAR a clearly-visible heatmap trail. The routed line should USE that trail to connect the adjacent waypoints.

To validate that, the test needs:
- Knowledge of which heatmap edges exist between W_{i-1} and W_{i+1} in the area where the new waypoint W_i was placed.
- An assertion that the routed segment USED a meaningful portion of those high-`user_count` edges.

That's heatmap-aware. Three approaches, each catching different failure modes.

---

## 2. Four layers of testing

### Layer 1 — Smoke: geometric invariants (ALREADY SHIPPED, PR #371)

| Property | Assertion |
|---|---|
| No crash | Test completes |
| No U-turn | `maxReverseTurnDeg < 150°` |
| No straight-line fallback | `segmentMeta[i].state !== 'failed'` |
| Waypoint at right index | `waypoints[1]` is the dragged point (PR #366 contract) |
| Route passes drag target | `nearestDistanceM(target, routeLine) < 50m` |

**Catches:** crashes, the PR #366/#370 bug classes, the structural drag-edit failures.
**Misses:** whether the route is "smart". A route via the highway that ignores the visible singletrack passes.
**Speed:** ~10-30 seconds per test. Runs locally.

### Layer 2 — Heatmap-aware property test (TO BUILD)

Discovers the heatmap state at test time. Asserts the route USES it.

**Pre-test setup (within the test):**

1. Query the DB: pick a test bbox (Clapiers). Find a "candidate corridor" — a chain of heat_edges with `user_count >= K` (K=2 in prod, 1 in dev) that forms a connected path from start-of-bbox to end-of-bbox.
2. From that chain, pick two endpoints far enough apart (~1-2 km) AND a midpoint that's NOT directly on the chain (say 100-200 m off).
3. Use those three points as the test waypoints (A = start of chain, B = end of chain, drag target = the off-chain midpoint).

**Test action:**

1. Place waypoints A and B via clicks.
2. Wait for the initial A → B route to compute.
3. Drag a point on the line to the drag target.
4. Read the final route geometry.

**Assertions:**

- **Smart-routing assertion**: the routed segments (A → W) ∪ (W → B) traverse AT LEAST 50% of the candidate corridor edges (measure by total length of overlap with the chain).
- **Coverage assertion**: AT LEAST 70% of the final route line is within 30 m of SOME heat_edge with `user_count >= K`.
- Layer 1 invariants hold (no U-turn, no fail, etc.).

**Catches:** "route ignores the heatmap and takes a random OSM path", cost-function regressions that under-weight `user_count`, fgraph builds that lose connectivity to the high-density area.

**Misses:** subtle quality differences when MULTIPLE candidate corridors exist with similar `user_count`. The test passes if ANY corridor is used.

**Speed:** ~30-60 seconds per test (DB query + map load + drag + assertion).

**Determinism:** the candidate corridor is RE-DISCOVERED on each run. So the test is robust to heatmap evolution — adds users → finds the new densest corridor.

### Layer 3 — Fixture-based golden snapshot (TO BUILD)

Freeze a small bbox + a known synthetic activity set + assert EXACT routing decisions.

**Setup (one-shot, run before the test suite):**

1. Define a small bbox (~1 km², e.g. a sub-area of Clapiers).
2. Hand-craft 5-10 synthetic GPX traces that create a known heatmap pattern: e.g. 7 activities along a specific singletrack, 3 along a parallel road.
3. Ingest them into a test-only DB or into a tagged-as-fixture set in the dev DB.
4. Capture the resulting `heat_edges` rows + their `user_count` values as a golden fixture (JSON in `e2e/fixtures/clapiers-heatmap-golden.json`).

**Test:**

1. Verify the fixture state matches the golden snapshot (else: the synthetic ingest changed; update fixture).
2. Drag a waypoint into the singletrack corridor.
3. Assert the routed geometry matches a golden snapshot of the EXPECTED route line (within some tolerance, e.g. all coords within 20 m of the expected).

**Catches:** any change in cost-function behaviour, any fgraph build regression, snap-radius regressions. EXTREMELY decisive — a 1 % cost-function weight change shows up as a route shift.

**Misses:** edge cases outside the small bbox; doesn't generalise.

**Speed:** ~20-40 seconds.

**Maintenance cost:** any intentional cost-function tuning or fgraph change requires regenerating the golden fixture. This is FINE — it forces the change to be explicit + reviewable.

**The cost of NOT having this**: PR #368 shipped fgraph changes that nobody could prove broke routing until Paul manually drag-tested at Clapiers. With Layer 3, the broken-routing would have been caught in CI / pre-merge.

### Layer 4 — Synthetic isolated unit test (Rust, in `wasm-router/`)

Pure-algorithmic test. Build a tiny graph in Rust, run the routing algorithm, assert the algorithm picks the right path.

**Setup:**

```rust
// 10 vertices, 12 edges, known popularity values
let g = test_graph!(
    A(0,0) -- B(1,0) [pop=5],
    A     -- C(0,1) [pop=1],
    B     -- D(2,0) [pop=10],
    ...
);
let route = g.find_route(A, D, intent_strength=0.7);
assert_eq!(route, vec![A, B, D]);  // pop-weighted, not direct
```

**Catches:** pure cost-function bugs (e.g. heat-bonus sign inverted, slope penalty doubled, surface multiplier off). Catches them in MILLISECONDS, not minutes.

**Misses:** integration issues with the WASM bridge, real-data quirks, the geographic complexity of a real area.

**Speed:** ~10 ms per test. Runs in `cargo test`.

**Maintenance:** changes when the cost-function design changes. That's intentional — forces design changes through tests.

---

## 3. The Clapiers-specific test plan

Paul reported the original bug at `http://localhost:3787/map?lat=43.65734&lon=3.87867&zoom=13.6` — Clapiers/Montpellier. That's a heatmap-dense area in prod (many activities).

For Clapiers, **Layer 2 is the right primary test**. The bbox is fixed, the heatmap is dense, the candidate corridor query is reliable.

### Clapiers Layer-2 test design

```ts
test('Clapiers drag-edit follows the visible heatmap corridor', async ({ page }) => {
  // 1. Query DB for the candidate corridor
  const corridor = await fetchCandidateCorridor({
    bbox: [3.85, 43.63, 3.92, 43.68],
    minUserCount: 2,
    minLengthKm: 1.0,
    sport: 'offroad',
  });
  // corridor = { edges: HeatEdge[], start: [lon, lat], end: [lon, lat], midpoint: [lon, lat] }

  // 2. Pick drag target = corridor midpoint, offset 150 m perpendicular
  const dragTarget = offsetPerpendicular(corridor.midpoint, corridor.bearing, 150);

  // 3. Load map, enter edit mode, place A + B
  await openEditMode(page, corridor.start);
  await clickWaypoint(page, corridor.start);
  await clickWaypoint(page, corridor.end);
  await waitForRoutingIdle(page);

  // 4. Drag line midpoint to drag target
  await dragLineSegment(page, /* segIdx */ 1, dragTarget);
  await waitForRoutingIdle(page);

  // 5. Read final route geometry
  const finalLine = await readRouteSource(page);

  // 6. ASSERT: at least 50% of the routed length overlaps with the corridor edges
  const overlap = computeOverlapLength(finalLine, corridor.edges);
  const totalLength = computeLineLength(finalLine);
  expect(overlap / totalLength).toBeGreaterThan(0.5);

  // 7. ASSERT: Layer 1 invariants (defensive)
  expect(maxReverseTurnDeg(finalLine)).toBeLessThan(150);
});
```

### Helper: `fetchCandidateCorridor`

Implemented as a Playwright HTTP fetch to a NEW backend endpoint `/test/candidate-corridor?bbox=...&min_user_count=...&sport=...&min_length_km=...` — or to a temporary test-only CLI that emits JSON.

Server-side query:

```sql
WITH chain AS (
  SELECT edge_key, geometry, user_count
  FROM heat_edges
  WHERE sport = :sport
    AND user_count >= :min_user_count
    AND ST_Intersects(geometry, ST_MakeEnvelope(:lon1, :lat1, :lon2, :lat2, 4326))
)
SELECT
  ARRAY_AGG(edge_key) as edge_keys,
  ST_AsGeoJSON(ST_LineMerge(ST_Collect(geometry))) as joined,
  SUM(ST_Length(geometry::geography)) / 1000.0 as length_km
FROM chain
WHERE length_km >= :min_length_km
LIMIT 1;
```

(The actual query needs more refinement — graph connectivity check, longest connected sub-path, etc. — but the shape is this.)

### Setup for the test to run locally

- Local DB must have `heat_edges` populated for the Clapiers bbox (today's local has 1.7 M, includes Clapiers area — usable).
- Local DB must have OSM road edges for the area (we imported 53.4 M earlier today — fine).
- Test runs locally only, skipped in CI (same as motto and drag-edit-quality.spec.ts).

### Setup for the test to run in CI

This is the hard part. To run in CI:
- CI needs a populated DB. Currently `heat_edges` is empty in CI (created from migrations, never ingested).
- Option A: ship a `tests/fixtures/clapiers-heat-edges.sql` dump that the test loads on setup. ~500 KB for a small bbox.
- Option B: re-ingest a known set of synthetic GPX during test setup. ~5-10 s per test.
- Option C: keep the test local-only; CI runs only Layer 1 + Layer 4.

Recommend **Option B** for Layer 2 (slow but cheap to maintain) and **Option A** for Layer 3 (fast, fixture-controlled).

---

## 4. CI vs local runs

| Layer | CI | Local |
|---|---|---|
| 1 — geometric invariants | YES if motto-style fgraph fixture in CI | YES |
| 2 — heatmap-aware | Option B (re-ingest synthetic GPX in setup) | YES |
| 3 — fixture golden | YES (fixture in repo) | YES |
| 4 — Rust unit | YES (already in `cargo test`) | YES |

Current state: only Layer 1 (PR #371) + Layer 4 (some existing pure-Rust tests). Layers 2 and 3 are TO BUILD.

---

## 5. Maintenance + debugging

### When tests fail

| Layer | Failure means | First debug step |
|---|---|---|
| 1 | Algorithm or splice bug | Read `failedSegmentIdxs`, look at the `lineCoords` in the test log |
| 2 | Route ignored the heatmap | Compare `corridor.edge_keys` to `routeLine` overlap; is the corridor query returning a sensible chain? |
| 3 | Cost-function or fgraph regression | Diff the route geometry against the golden fixture; the diff itself is informative |
| 4 | Cost-function or algorithm bug | Standard Rust test debugging |

### When intentional changes break the tests

- Layer 1: re-derive expected coords (rare; usually means the route fundamentally changed)
- Layer 2: usually NO update needed (corridor is re-queried at test time)
- Layer 3: regenerate the golden fixture deliberately, include in the PR as `regenerate-fixtures.sh` output
- Layer 4: update expected paths in the Rust test

### When the heatmap evolves

- Layer 1: no impact (geometric invariants)
- Layer 2: re-queries on each run, so naturally adapts
- Layer 3: fixture isolated from main heatmap (synthetic activities ingested into a tagged bbox) — no impact
- Layer 4: no impact (synthetic graph)

---

## 6. Effort estimate + sequencing

| Layer | Effort | When |
|---|---|---|
| 1 — geometric | DONE (PR #371) | Today |
| 4 — Rust unit | Mostly existing; could augment for cost-function tuning | Anytime |
| 2 — Clapiers heatmap-aware | 1-2 days (corridor query + helper + test) | After surgical-fix lands |
| 3 — fixture golden | 1-2 days (fixture setup + snapshot + assertion) | After Layer 2 proves the heatmap-aware approach works |

**Recommend sequencing**: Layer 2 next, after the surgical-fix sub-agent's PR lands (so we have a clean baseline). Layer 3 once Layer 2 reveals the rough edges.

---

## 7. Open design questions

### Q1 — Where does the candidate-corridor query live?

Options:
- (a) New backend endpoint `/test/candidate-corridor?bbox=...` — clean API, easy to call from Playwright. But adds a test-only route to the prod codebase.
- (b) Standalone Python script run as a test-setup step — `python -m app.cli.discover_test_corridor --bbox=... --out=/tmp/corridor.json`. Test reads the JSON. Cleaner separation but more setup boilerplate.
- (c) Direct DB query from the test via `pg` client. Brittle (auth, query duplication).

Recommendation: **(b)** — keeps the test-only logic out of the prod API surface.

### Q2 — What does "50% overlap" mean exactly?

- (a) **Length-weighted**: sum of `length(routeLine ∩ corridor.geometry)` divided by sum of `length(routeLine)`. Measures how much of the ROUTED line lies on the corridor.
- (b) **Edge-key-weighted**: count of corridor edges traversed / count of corridor edges available. Measures whether the route follows the whole corridor or just part.
- (c) **User-count-weighted**: sum of (user_count × overlap_length) / sum of (user_count × corridor_length). Weights popular edges more.

Recommendation: **(a)** for the primary assertion, **(c)** as a secondary diagnostic on failure.

### Q3 — How many candidate corridors should the test consider?

A bbox might have multiple candidate chains (e.g. parallel paths). The test could:
- (a) Pick ONE corridor and assert the route uses it
- (b) Pick the BEST corridor (highest cumulative user_count) and assert
- (c) Pick ALL corridors and assert the route uses one of them

Recommendation: **(b)**. If the user-perceived "main" trail isn't used, that's the failure we want to catch.

### Q4 — How tolerant should the threshold be (50%)?

50 % overlap is a reasonable starting point. The actual ratio depends on:
- How much the drag target is OFF the corridor (close → high overlap, far → lower)
- How direct the corridor connects A → drag target → B
- Cost-function preferences

Tune empirically on the first 5 runs locally. If 50 % is too tight, drop to 30 %. If routes routinely score > 70 %, tighten the threshold.

### Q5 — Should Layer 3's golden fixture be in version control?

- YES if small (< 500 KB) — review-friendly, change history visible.
- NO if large — store in S3/GCS, fetch on test setup.

Recommend YES for the first iteration. The golden fixture for a 1 km² bbox should be < 100 KB.

---

## 8. Beyond drag-edit

The same layered approach applies to ANY route-quality concern:

- **Initial route A → B quality** (the motto test today is Layer 1 only). Layer 2 could assert "the proposed route uses the high-density corridor between Montpellier and Anduze".
- **Waypoint deletion**: drop a waypoint, assert the route bridges cleanly without falling to straight-line.
- **Sport switching**: change from offroad to road, assert the route shifts toward asphalt edges.
- **Cost-function intent slider**: at `intent_strength=0` vs `=1`, assert the routes meaningfully differ.

Layer 4 (Rust unit tests) covers the algorithm; Layer 2 / Layer 3 cover the integration.

---

## See also

- [`docs/drag-edit-design.md`](drag-edit-design.md) — the UX contract for drag-edit
- [`docs/heat-edges-osm-coordinate-mismatch.md`](heat-edges-osm-coordinate-mismatch.md) — the architectural finding that motivated PR #371
- `e2e/tests/drag-edit-quality.spec.ts` (PR #371) — the Layer 1 reference implementation
- `e2e/tests/motto-montpellier-anduze.spec.ts` — the Layer-1 routing smoke test
- `frontend/lib/route-geometry-invariants.ts` — the `maxReverseTurnDeg` + `verifyMonotonicWaypointVisit` pure functions (shipped in PR #366) — reusable for any Layer N test
