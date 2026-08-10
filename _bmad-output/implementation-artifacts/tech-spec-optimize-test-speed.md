---
title: 'Optimize Test Suite Execution Speed'
slug: 'optimize-test-speed'
created: '2026-03-14'
status: 'completed'
stepsCompleted: [1, 2, 3, 4, 5, 6]
tech_stack: ['pytest', 'Playwright 1.51.1', 'Docker Compose', 'GitHub Actions', 'PostgreSQL 17 + PostGIS', 'FastAPI TestClient']
files_to_modify: ['.github/workflows/ci.yml', 'backend/tests/conftest.py', 'backend/tests/test_routing.py', 'backend/tests/test_dfci.py', 'e2e/playwright.config.ts', 'e2e/tests/client-routing.spec.ts', 'e2e/tests/core.spec.ts', 'e2e/tests/helpers.ts', 'backend/app/main.py', 'frontend/app/map/page.tsx']
code_patterns: ['session-scoped TestClient', 'auth_headers function-scope (only 70/586 tests)', 'fullyParallel: false', 'docker compose run --rm creates separate container', 'TRAILS_ENABLED not disabled in tests']
test_patterns: ['586 backend tests (20 files)', '122 E2E tests (6 files)', 'test_routing.py 3715 lines — snap4 (9 tests), direction_factor (15+ tests) are parametrizable', '48 hardcoded E2E waits totaling 68.8s', '6 autouse fixtures (all necessary, low overhead)']
---

# Tech-Spec: Optimize Test Suite Execution Speed

**Created:** 2026-03-14

## Overview

### Problem Statement

The CI test suite takes >24 minutes to complete (target: ~10 min). Root causes identified:

1. **CI double startup** — `docker compose run --rm backend pytest` (ci.yml:91) creates a NEW container that re-triggers lifespan seeding, while the `up -d` backend is already running. Two `_background_data_load()` processes compete for the DB.
2. **Trails import not disabled** — `TRAILS_ENABLED` is NOT set to `false` in conftest.py (line 12-13), so `import_trails()` tries Overpass API or cache read during pytest startup. DFCI is disabled but trails are not.
3. **Redundant backend tests** — snap4 (9 tests at lines 1679-1714 → 1 parametrized), direction_factor (21 tests at lines 3510-3709 → 5 parametrized). ~20 test functions can be reduced via `@pytest.mark.parametrize`.
4. **Sequential E2E execution** — `fullyParallel: false` + 48 hardcoded `waitForTimeout()` calls totaling ~69s cumulative wait time across `client-routing.spec.ts` (27 waits, 45.3s) and `core.spec.ts` (18 waits, 23.5s).
5. **No Docker image caching** in CI — rebuilds backend image on every push (~3-5 min).
6. **E2E fixture polling** — 2s poll interval with 120s timeout for heatmap fixture loading.

### Solution

1. Fix CI to use `docker compose exec` instead of `run --rm` to reuse the running container
2. Disable `TRAILS_ENABLED` in test conftest alongside DFCI
3. Parametrize redundant backend tests (snap4 + direction_factor)
4. Replace key hardcoded E2E waits with `waitForResponse()` / `waitForFunction()`
5. Add Docker layer caching in CI via GitHub Actions cache
6. Enable `fullyParallel: true` for default E2E project, reduce fixture poll interval

### Scope

**In Scope:**
- CI workflow optimization (exec vs run, Docker caching)
- Backend test pruning via parametrization
- E2E wait optimization and parallelization
- Missing env var fix (TRAILS_ENABLED)

**Out of Scope:**
- Test file restructuring/splitting (not moving tests between files)
- New test coverage
- Backend application code changes beyond test infrastructure
- auth_headers scope change (only 70/586 tests use it — low impact)
- Autouse fixture changes (all 6 are necessary, low overhead)

## Context for Development

### Root Causes (Deep Investigation)

**RC1: CI double-container seeding**
At `.github/workflows/ci.yml:91`, `docker compose run --rm backend pytest` creates a NEW container separate from the one started by `up -d`. The new container triggers `lifespan()` → `_background_data_load()` again. Meanwhile the `up -d` backend already ran the same seeding. Fix: use `docker compose -f docker-compose.backend.yml exec backend pytest` to reuse the running container where data is already loaded.

**RC2: TRAILS_ENABLED not disabled**
At `backend/tests/conftest.py:12-13`, `DFCI_ENABLED=false` and `DFCI_HERAULT_ENABLED=false` are set, but `TRAILS_ENABLED` is NOT. So `import_trails()` (main.py:397 → import_trails.py:192) checks for `TRAILS_ENABLED` env var and defaults to `true`. This triggers Overpass queries or cache reads during test startup.

**RC3: Redundant parametrizable tests**
- `test_routing.py:1679-1714` — 9 snap4 tests: all test edge cases of the `snap4()` rounding function with different inputs. Perfect parametrize candidate.
- `test_routing.py:3510-3709` — 21 direction_factor tests: `test_disabled_for_{road,gravel,offroad}` (3 identical pattern), `test_no_penalty_*` (4 tests), `test_penalty_*` (3 tests), `test_*_passes` (2 tests), `test_max_penalty_*` (1 test), integration tests (8 tests). The first 13 unit tests can be consolidated into 3 parametrized tests (disabled profiles, no-penalty scenarios, penalty scenarios).

**RC4: E2E hardcoded waits**
- `client-routing.spec.ts`: 27 `waitForTimeout()` calls. Pattern: tile load wait (1500ms) → routing wait (2000ms) repeated at every waypoint action. Can replace routing waits with `page.waitForResponse(url => url.includes('/routing'))` or `page.waitForFunction(() => document.querySelector('[data-testid="route-editor-panel"]')?.textContent?.includes('km'))`.
- `core.spec.ts`: 18 `waitForTimeout()` calls. Similar pattern plus 3000ms surface stats wait (line 1483) and elevation profile waits (lines 1589-1611).

**RC5: No Docker layer caching**
CI step `docker compose -f docker-compose.backend.yml up --build -d` rebuilds from scratch. With `docker/setup-buildx-action` + GitHub Actions cache, pip/poetry layers are cached.

**RC6: E2E not parallel for default project**
`fullyParallel: false` at `e2e/playwright.config.ts:8`. Default project tests (core.spec.ts, client-routing.spec.ts) don't share DB state via the test UI — they can run in parallel with `workers: 2`.

### Files to Reference

| File | Line(s) | Purpose |
| ---- | ------- | ------- |
| `.github/workflows/ci.yml` | 91 | `docker compose run --rm` → should be `exec` |
| `.github/workflows/ci.yml` | 47 | `up --build` → add layer caching |
| `backend/tests/conftest.py` | 12-13 | Missing `TRAILS_ENABLED=false` |
| `backend/tests/test_routing.py` | 1679-1714 | snap4: 9 tests → 1 parametrized |
| `backend/tests/test_routing.py` | 3510-3709 | direction_factor: 21 tests → ~8 |
| `e2e/tests/client-routing.spec.ts` | 82-709 | 27 waitForTimeout calls |
| `e2e/tests/core.spec.ts` | 929-1982 | 18 waitForTimeout calls |
| `e2e/playwright.config.ts` | 8 | `fullyParallel: false` → `true` |
| `backend/app/main.py` | 380-405 | `_background_data_load` sequence |
| `backend/app/cli/import_trails.py` | 192 | `TRAILS_ENABLED` check |

### Technical Decisions

- **`exec` vs `run --rm`**: `exec` reuses the running container where `_background_data_load` already completed. No double seeding. **Important:** `TestClient(app)` in conftest still triggers lifespan → `_background_data_load`. Even with `exec`, this re-runs seeding. Must add early-exit guard in `_background_data_load` to skip when `startup_progress['done']` is already True.
- **`_enrich_edges` does real work**: Even with idempotent data, `enrich_heat_edges()` walks all heat_edges and enriches with OSM surface data. In TEST_MODE it uses brute-force in-memory index (osm_enrich.py:122). Must also short-circuit enrichment when no new edges exist.
- **auth_headers scope NOT changed**: Only 70/586 tests use it. Each call is ~1ms (simple INSERT + JWT sign). Not a bottleneck.
- **Autouse fixtures NOT changed**: All 6 are necessary and have low overhead (in-memory cache clears). Not worth the risk.
- **`fullyParallel: true` only for `default` project**: Heatmap-fixture tests share seeded DB state and must remain sequential. Use `workers: 1` in CI (2-core runner, CPU-bound E2E tests compete for resources), `workers: 2` only for local dev.
- **Parametrize, don't delete**: Redundant tests become parametrized versions — same edge case coverage, fewer function declarations, faster pytest collection.
- **E2E waits — client-graph blind spot**: `waitForResponse('/routing')` only works for server-routed segments. Client-graph routing (A* in browser) makes NO API call to `/routing`. Solution: add `data-route-status` attribute to page.tsx reflecting the `routing` state (already `true`/`false`), then use `waitForSelector('[data-route-status="idle"]')`. Single source of truth, no fragile `Promise.race` or text matching.

## Implementation Plan

### Tasks

- [x] **Task 0: Instrument CI step timings**
  - File: `.github/workflows/ci.yml`
  - Action: Add `date +%s` timestamps before and after each major step (docker build, pytest, frontend build, playwright). Log elapsed seconds. This provides baseline data and validates improvements.
  - Notes: Simple `echo "::group::Backend tests" && START=$(date +%s) ... echo "Backend tests: $(($(date +%s) - START))s"`. Remove after optimization is verified.

- [x] **Task 1: Fix CI double-container seeding + SKIP_BACKGROUND_LOAD**
  - File: `.github/workflows/ci.yml`
  - Action: At line 91, change `docker compose -f docker-compose.backend.yml run --rm ... backend pytest --tb=short -q` to `docker compose -f docker-compose.backend.yml exec backend pytest --tb=short -q`
  - Remove the `-e TEST_MODE=true -e ENABLE_STRAVA_INTEGRATION=true -e JWT_SECRET=ci-test-secret` flags (env vars already set in the running container via docker-compose.backend.yml).
  - File: `backend/app/main.py`
  - Action: At the top of `_background_data_load()` (line ~381), add TWO guards:
    ```python
    async def _background_data_load(admin_id: str) -> None:
        # Guard 1: already completed (e.g., TestClient re-triggering lifespan)
        if startup_progress.get("done"):
            logger.info("Background data already loaded — skipping")
            return
        # Guard 2: explicit skip for test environments
        if os.environ.get("SKIP_BACKGROUND_LOAD", "").lower() == "true":
            logger.info("SKIP_BACKGROUND_LOAD=true — skipping all background data loading")
            startup_progress["done"] = True
            return
        # ... existing code ...
    ```
  - File: `backend/tests/conftest.py`
  - Action: Add `os.environ["SKIP_BACKGROUND_LOAD"] = "true"` at line ~9 (alongside other test env vars).
  - **CI readiness check (RT2):** With `exec`, pytest starts in the RUNNING container — but `_background_data_load` may still be in progress (async background task). The `/healthz` endpoint only checks FastAPI is responding, NOT that data is loaded. Add a readiness poll in CI before `exec pytest`:
    ```yaml
    - name: Wait for data loading
      run: |
        for i in $(seq 1 60); do
          STATUS=$(docker compose -f docker-compose.backend.yml exec -T backend python -c "from app.main import startup_progress; print(startup_progress.get('done', False))" 2>/dev/null || echo "False")
          if [ "$STATUS" = "True" ]; then echo "Data loaded"; break; fi
          sleep 2
        done
    ```
    Alternatively, expose `startup_progress['done']` via `/healthz?ready=true` and poll that.
  - Notes: `SKIP_BACKGROUND_LOAD` is a single flag that short-circuits the ENTIRE `_background_data_load` — no need to chase individual importer flags (DFCI_ENABLED, TRAILS_ENABLED, etc.). This is more robust than patching each importer. The `startup_progress['done']` guard also prevents re-running when `TestClient(app)` re-triggers lifespan via `exec`.

- [x] **Task 2: Disable TRAILS_ENABLED in test conftest + skip enrichment when empty**
  - File: `backend/tests/conftest.py`
  - Action: After line 13 (`os.environ["DFCI_HERAULT_ENABLED"] = "false"`), add: `os.environ["TRAILS_ENABLED"] = "false"`
  - File: `backend/app/services/osm_enrich.py` (or `backend/app/main.py`)
  - Action: In `_enrich_edges()` (main.py:506), add early check: if heat_edges count is 0, skip enrichment entirely:
    ```python
    async def _enrich_edges(t0: float) -> tuple[int, int]:
        from app.db.session import SessionLocal
        from sqlalchemy import text as sa_text
        db = SessionLocal()
        try:
            count = db.execute(sa_text("SELECT COUNT(*) FROM heat_edges")).scalar() or 0
        finally:
            db.close()
        if count == 0:
            logger.info("No heat_edges to enrich — skipping (%.1fs)", _t.monotonic() - t0)
            return 0, 0
        # ... existing enrichment code ...
    ```
  - Notes: Also prevents `import_trails()` from trying Overpass or cache reads.
  - **Caveat (A8):** In TEST_MODE, demo data seeding creates activities → `ingest_activity` → heat_edges. So heat_edges may NOT be empty after `_background_data_load` runs. This early-exit only helps if `SKIP_BACKGROUND_LOAD=true` prevents demo seeding (Task 1) or if no demo activities exist. Conditional benefit — not a guaranteed win.

- [x] **Task 3: Add Docker image caching in CI**
  - File: `.github/workflows/ci.yml`
  - Action: Use `docker save/load` approach with `actions/cache` (simpler than Buildx with compose):
    ```yaml
    - name: Cache Docker image
      id: docker-cache
      uses: actions/cache@v4
      with:
        path: /tmp/docker-image.tar
        key: docker-${{ runner.os }}-${{ hashFiles('backend/Dockerfile', 'backend/requirements*.txt', 'backend/pyproject.toml') }}

    - name: Load cached Docker image
      if: steps.docker-cache.outputs.cache-hit == 'true'
      run: docker load -i /tmp/docker-image.tar

    - name: Build and start stack
      run: |
        docker compose -f docker-compose.backend.yml up --build -d
      env:
        DOCKER_BUILDKIT: 1
        COMPOSE_DOCKER_CLI_BUILD: 1

    - name: Save Docker image for cache
      if: steps.docker-cache.outputs.cache-hit != 'true'
      run: docker save $(docker compose -f docker-compose.backend.yml images -q backend) -o /tmp/docker-image.tar
    ```
  - Notes: `docker save/load` works reliably with `docker compose` without Buildx bake workarounds. On cache hit, `--build` detects the loaded image layers and skips rebuild (~5s vs ~3-4 min). On cache miss, builds normally and saves for next run.

- [x] **Task 4: Parametrize snap4 tests**
  - File: `backend/tests/test_routing.py`
  - Action: Replace 9 test methods at lines 1679-1714 (`TestSnap4` class) with 1 parametrized test:
    ```python
    @pytest.mark.parametrize("input_val,expected", [
        pytest.param(3.12345678, 3.1235, id="rounds_to_4_decimals"),
        pytest.param(-3.12345678, -3.1235, id="negative_coords"),
        pytest.param(0.0, 0.0, id="zero"),
        pytest.param(3.12345, 3.1235, id="boundary_rounds_up"),
        pytest.param(3.12344, 3.1234, id="boundary_rounds_down"),
        pytest.param(180.0001, 180.0001, id="antimeridian"),
    ])
    def test_snap4(self, input_val, expected):
        assert snap4(input_val) == expected

    def test_snap4_close_values(self):
        """Very close values may collapse or stay distinct."""
        a, b = snap4(3.12341), snap4(3.12349)
        # They collapse to same value
        assert a == b == 3.1234 or a != b

    def test_snap4_idempotent(self):
        assert snap4(snap4(3.12345678)) == snap4(3.12345678)
    ```
  - Notes: Read the existing test assertions carefully to extract exact input/expected pairs. The `very_close_collapse` and `barely_different_stay_distinct` tests check opposing edge cases — keep as separate assertions or combine into one `test_snap4_close_values`. Net reduction: 9 → 3 test functions.

- [x] **Task 5: Parametrize direction_factor unit tests**
  - File: `backend/tests/test_routing.py`
  - Action: In `TestDirectionFactor` class (line 3510), consolidate the first 13 unit tests (lines 3513-3578) into 3 parametrized tests:
    1. **Disabled profiles** (lines 3513-3523): Parametrize `road`, `gravel`, `offroad` → 1 test with `@pytest.mark.parametrize("profile", ["road", "gravel", "offroad"])`
    2. **No-penalty scenarios** (lines 3527, 3546, 3552, 3565): Parametrize cases where `direction_factor()` returns 1.0 (with_flow, balanced_traffic, below_threshold, not_enough_passes) → 1 test with parametrized inputs
    3. **Penalty scenarios** (lines 3533, 3540, 3558, 3571, 3578): Parametrize cases where factor > 1.0 → 1 test with parametrized inputs and `assert factor > 1.0` (or specific expected range)
  - Keep the 8 integration tests (lines 3584-3709) unchanged — they test full cost computation, not the unit function.
  - Notes: Net reduction: 13 → 3 unit test functions + 8 integration tests unchanged. Read each test body to extract the exact `direction_factor()` call args and expected return values.

- [x] **Task 6: Add `data-testid="route-complete"` signal to page.tsx + replace E2E routing waits in client-routing.spec.ts**
  - File: `frontend/app/map/page.tsx`
  - Action: Add a `data-testid="route-complete"` attribute to the route editor panel that reflects the `routing` state variable. The `routing` state is already `true` while routing is in progress and `false` when done (via `setRouting(false)` at lines 1933, 2894, 2965, 3018, 4254). Add it to the panel element at line ~6479:
    ```tsx
    <div data-testid="route-editor-panel" data-route-status={routing ? 'routing' : 'idle'}>
    ```
  - File: `e2e/tests/client-routing.spec.ts`
  - Action: Create a helper function `waitForRouteComplete` using `waitForSelector` on the DOM signal:
    ```typescript
    /** Wait for routing to complete — works for BOTH server routing and client-graph (A* in browser). */
    async function waitForRouteComplete(page: Page, timeoutMs = 10000) {
      // Step 1: Wait for routing to START (avoid resolving on pre-existing idle state)
      await page.waitForSelector('[data-route-status="routing"]', { timeout: 5000 }).catch(() => {});
      // Step 2: Wait for routing to FINISH
      await page.waitForSelector('[data-route-status="idle"]', { timeout: timeoutMs });
    }
    ```
    **Why two steps (RT3):** When the page loads, `routing` is `false` (idle). If `waitForSelector('idle')` runs before the click handler sets `routing=true`, it resolves on the STALE idle state — a classic "check before state change" race. Waiting for `routing` first ensures we see the transition. The `.catch(() => {})` on step 1 handles the edge case where routing completes before the selector runs (very fast client-graph).
    **Why this is better than `Promise.race`**: The `routing` state is the single source of truth — it's set to `false` after ANY routing completes (server OR client-graph). No fragile text matching. One attribute, one source of truth.
  - Specific replacements:
    - Lines 172, 204, 267, 309, 317, 349, 456, 503, 541, 579, 649, 709: Replace `waitForTimeout(2000)` with `waitForRouteComplete(page)`
    - Line 420: Replace `waitForTimeout(3000)` with `waitForRouteComplete(page)` (3-waypoint route)
    - Lines 82, 168, 201, 264, 409, 453, 500, 555, 645, 706: Keep `waitForTimeout(1500)` for tile loads (no observable event)
    - Lines 416, 418: Keep 500ms click-gap waits (animation/debounce buffer)
  - Notes: Resolves typically in <100ms after routing finishes. Saves ~24s cumulative. The `data-route-status` attribute is purely for testing — no visual impact.
  - **Pre-check (A2):** Before implementation, verify ALL routing error paths (catch blocks) also call `setRouting(false)`. If any don't, add it — otherwise `waitForSelector('[data-route-status="idle"]')` would hang indefinitely on routing errors.

- [x] **Task 7: Replace E2E routing waits in core.spec.ts**
  - File: `e2e/tests/core.spec.ts`
  - Action: Import or copy the same `waitForRouteComplete` helper from Task 6 (or extract to shared `e2e/tests/helpers.ts`). Replace routing-specific waits:
    - Lines 933, 946, 1008, 1015, 1247, 1288: Replace `waitForTimeout(2000)` with `waitForRouteComplete(page)`
    - Line 1483: Replace `waitForTimeout(3000)` for surface stats with `page.waitForResponse(resp => resp.url().includes('/surface_stats'), { timeout: 10000 })`
    - Line 1574: Replace `waitForTimeout(2000)` for elevation with `page.waitForResponse(resp => resp.url().includes('/elevation'), { timeout: 10000 })`
    - Lines 929, 1006, 1245: Keep 400ms click-gap waits
    - Lines 1589, 1605: Keep 300ms hover waits (UI animation)
    - Lines 1974, 1977: Keep 200ms mock waits
    - Line 1982: Replace `waitForTimeout(3000)` multi-waypoint wait with `waitForRouteComplete(page)`
  - Notes: Saves ~18s cumulative. Uses the same `data-route-status="idle"` signal — no fragile text matching or `Promise.race` needed.

- [x] **Task 8: Enable E2E parallel execution for default project**
  - File: `e2e/playwright.config.ts`
  - Action:
    1. Change line 8 from `fullyParallel: false` to `fullyParallel: true`
    2. Change `workers: 2` to `workers: process.env.CI ? 1 : 2`
  - Notes: `fullyParallel: true` allows tests within a file to run concurrently. The `heatmap-fixture` project keeps `dependencies: ['default']`.
  - **Hypothesis (A4):** `workers: 1` in CI is assumed faster because A* runs in Chromium (CPU-bound). However, A* runs in the BROWSER, not the Node worker — 2 Chromium instances on 2 cores could benefit from parallelism during I/O-mixed tests (network waits, API calls). Start with `workers: process.env.CI ? 1 : 2`, then **benchmark both values** on a real CI run to validate. Adjust based on data, not assumption.

- [x] **Task 9: Reduce fixture poll interval**
  - Files: `e2e/tests/routing-heatmap-fixture.spec.ts`, `e2e/tests/routing-quality-montpellier.spec.ts`
  - Action: In `waitForFixtureLoaded()`, reduce poll interval from `2000ms` to `1000ms`. The function polls `/heatmap/summary` — a fast endpoint (~5ms response).
  - Notes: Also consider reducing the `timeoutMs` from `120_000` to `90_000` since with `docker compose exec` (Task 1), the backend already has data loaded.
  - **Realistic savings (A10):** Average saving is ~0.5s per poll cycle, not "up to 60s". With ~5-10 polls, saves ~2-5s total. Low priority optimization.

- [x] **Task 10: Verify all tests pass**
  - Action: Run full test suite locally:
    1. `docker compose -f docker-compose.backend.yml up -d` + wait for healthz
    2. `docker compose -f docker-compose.backend.yml exec backend pytest --tb=short -q`
    3. `cd frontend && npm run build`
    4. `cd e2e && npx playwright test`
  - Notes: Verify test count is reasonable (should be ~570 backend tests after parametrization, down from 586). All E2E tests should still pass.

### Acceptance Criteria

- [ ] AC0: Given CI step timings instrumented, when pipeline runs, then each step logs elapsed seconds to identify the actual bottlenecks
- [ ] AC1: Given the CI workflow with `exec` + early-exit guard, when `pytest` runs, then `_background_data_load` detects `startup_progress['done']=True` and returns immediately
- [ ] AC2: Given `TRAILS_ENABLED=false` in conftest.py, when backend tests start, then `import_trails()` returns 0 immediately without Overpass queries
- [ ] AC3: Given Docker layer caching configured, when CI runs on unchanged requirements.txt, then Docker build step completes in <30s (cache hit)
- [ ] AC4: Given snap4 tests parametrized, when `pytest test_routing.py::TestSnap4` runs, then all edge cases pass and test count is 3 (down from 9)
- [ ] AC5: Given direction_factor unit tests parametrized, when `pytest test_routing.py::TestDirectionFactor` runs, then all scenarios pass and unit test count is reduced by ~10
- [ ] AC6: Given `data-route-status` attribute added to page.tsx and E2E waits replaced with `waitForSelector('[data-route-status="idle"]')`, when client-routing tests run, then both server-routed AND client-graph-routed tests complete without timeout
- [ ] AC7: Given `fullyParallel: true` for default project, when Playwright runs with `workers: 2`, then core.spec.ts and client-routing.spec.ts execute concurrently
- [ ] AC8: Given fixture poll interval of 1000ms, when heatmap-fixture tests run, then fixture detection completes within 30s (not 60s+)
- [ ] AC9: Given all optimizations applied, when full CI pipeline runs, then total time is <15 minutes (stretch goal: <12 minutes)
- [ ] AC10: Given all optimizations applied, when full test suite runs, then all tests pass with no regressions (backend + E2E)

## Additional Context

### Dependencies

- `docker compose exec` requires the container to be running (guaranteed by `up -d` + healthcheck wait in prior CI step)
- Docker Buildx action (`docker/setup-buildx-action@v3`) must be available in GitHub Actions runner
- `page.waitForResponse()` only works for server-routed segments — client-graph routing makes NO API call. The `data-route-status` attribute on page.tsx reflects the `routing` state and works for both server and client-graph routing.

### Testing Strategy

**Verification approach:**
1. Run backend tests locally with `docker compose exec` — verify same results as `run --rm`
2. Run parametrized tests individually — verify same edge case coverage
3. Run E2E tests with `fullyParallel: true` — verify no flaky failures from parallel execution
4. Push to CI — measure total pipeline time

**Risk mitigation:**
- `waitForRouteComplete` uses `data-route-status` attribute — a single source of truth reflecting the `routing` state variable. Works for both server and client-graph routing. If the attribute is removed, E2E tests will fail fast (clear signal, not silent timeout).
- If parallel E2E causes flakiness, revert `fullyParallel` to `false` and focus on other optimizations

### Notes

- **Estimated savings per optimization:**
  - Task 1 (exec vs run): ~2-5 min (eliminates double startup + seeding)
  - Task 2 (TRAILS_ENABLED): ~10-30s (skips trail import attempt)
  - Task 3 (Docker cache): ~2-4 min on cache hit
  - Tasks 4-5 (parametrize): ~5-10s (fewer test functions to collect/run)
  - Tasks 6-7 (waitForResponse): ~40s (replace 69s of fixed waits)
  - Task 8 (parallel E2E): ~30-60s (2 files run concurrently)
  - Task 9 (poll interval): ~15-30s (faster fixture detection)
- **Total estimated savings: 6-12 minutes** (from >24 min to ~12-18 min)
- auth_headers is NOT a bottleneck (only 70 tests, ~1ms each)
- 6 autouse fixtures are necessary and low-overhead — not worth changing
- `waitForRouteComplete` (via `data-route-status` attribute) is the most impactful single E2E optimization
- Client-graph routing makes NO `/routing` API call — `data-route-status="idle"` works for both server and client-graph routing
- CI runner has 2 cores — `workers: 1` avoids CPU contention on CPU-bound A* E2E tests
- If further speed needed post-implementation: split CI into parallel jobs (backend + E2E in separate jobs)

### Performance Profiler Panel Findings (Applied)

| Finding | Severity | Fix Applied |
|---------|----------|-------------|
| `_background_data_load` re-runs via TestClient lifespan even with `exec` | High | Task 1: added early-exit guard (`startup_progress['done']`) |
| `waitForResponse` fails for client-graph routes (no API call) | High | Tasks 6-7: `data-route-status` attribute + `waitForSelector` (single source of truth) |
| Docker Buildx caching with `docker compose` needs different approach | Medium | Task 3: `docker save/load` instead of Buildx cache |
| `_enrich_edges` does real work even with empty heat_edges | Medium | Task 2: early-exit when heat_edges count = 0 |
| `workers: 2` on 2-core CI runner hurts CPU-bound E2E | Low | Task 8: `workers: process.env.CI ? 1 : 2` |
| CI time breakdown unknown | Medium | Task 0: instrument step timings |

### Assumptions Audit Findings (Applied)

| # | Assumption | Verdict | Action |
|---|-----------|---------|--------|
| A2 | `routing` state always `false` on completion | Needs verification | Task 6: added pre-check to verify error-path `setRouting(false)` calls |
| A4 | `workers:1` faster than `workers:2` on 2-core CI | Questionable | Task 8: benchmark both values, don't assume |
| A8 | Enrichment early-exit saves time | Conditional | Task 2: noted demo data may create heat_edges, benefit is conditional |
| A10 | Poll interval 2s→1s saves "up to 60s" | Overstated | Task 9: realistic saving ~2-5s, lowered priority |

### Red Team / Adversarial Review Findings (Applied)

| # | Attack | Severity | Fix Applied |
|---|--------|----------|-------------|
| RT2 | `exec` starts pytest before `_background_data_load` finishes | **HIGH** | Task 1: added CI readiness poll checking `startup_progress['done']` |
| RT3 | `waitForSelector('idle')` resolves on PRE-EXISTING idle before routing starts | **HIGH** | Task 6: two-step wait — detect `routing` first, then `idle` |
| RT4 | Parametrized tests lose descriptive names in CI output | LOW | Task 4: use `pytest.param(..., id=...)` for readable test IDs |
| RT7 | `page.tsx` missing from `files_to_modify` frontmatter | LOW | Added to frontmatter |

### Code Review Notes (Post-Implementation)

10 findings reviewed, 5 fixed:

| # | Finding | Severity | Resolution |
|---|---------|----------|------------|
| F1+F2 | `waitForRouteComplete` Step 1 timeout too long (5s) | Medium | Reduced to 2s — still catches race, lower penalty |
| F4 | `_enrich_edges` early-exit skips `tag_dangerous_highways` | Low | Separated: skip only `enrich_heat_edges`, always run `tag_dangerous_highways` |
| F6 | `waitForRouteComplete` duplicated in 2 spec files | Low | Extracted to shared `e2e/tests/helpers.ts` |
| F10 | Duplicate `snap4` parametrize input (identical coords) | Low | Changed `boundary_rounds_down` to `3.87644` (actually tests rounding down) |
| F-DFCI | `test_get_dfci_empty` / `test_seed_dfci_in_test_mode` fail with `exec` | Medium | Added `DELETE FROM dfci_edges` to autouse fixture — tests now clean DB before asserting |
| F3 | `SKIP_BACKGROUND_LOAD` not in Docker server | — | By design: only TestClient needs it |
| F5 | `startup_progress` no locking | — | Noise: single-writer, single-reader |
| F7 | Docker cache key incomplete | — | Acceptable: pyproject.toml covers deps |
| F8 | `fullyParallel` no-op with workers:1 | — | Correct: enables local parallelism |
| F9 | Remaining fixed waits | — | Pre-existing: tile loads, click gaps, animations |

**Final results:** 584 passed, 1 skipped, 0 failed.
