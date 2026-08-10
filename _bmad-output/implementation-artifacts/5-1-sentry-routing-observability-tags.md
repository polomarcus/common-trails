# Story 5.1: Sentry Routing Observability Tags

Status: done

## Story

As an admin,
I want every routing request tagged with cascade level, method, and sport in Sentry,
So that I can track fallback rates and identify routing quality issues.

## Acceptance Criteria

1. **Given** a routing request is processed
   **When** the response is sent
   **Then** Sentry captures custom tags: `routing.level` (client/personal/straight), `routing.method` (heatmap/personal/straight_line), `routing.sport` (road/gravel/mtb/offroad)

2. **Given** an admin opens the Sentry dashboard
   **When** they filter by `routing.level`
   **Then** they can see the percentage of requests at each cascade level
   **And** identify if fallback rate exceeds 5%

3. **Given** a routing error occurs (timeout, exception)
   **When** Sentry captures the exception
   **Then** the routing tags are attached to the error event for diagnosis

## Architectural Constraint

- Backend Sentry is already initialized in `main.py` (conditional on `SENTRY_DSN`, `traces_sample_rate=0.1`, `enable_tracing=True`)
- Frontend Sentry is lazy-loaded via `SentryProvider.tsx` (`@sentry/react@^8.0.0`, production-only, 0% trace sample rate)
- Currently only `/routing/proposals` has Sentry instrumentation (1 span + 3 tags at `routing.py:326-344`). Other endpoints (`/routing`, `/routing/multi`, `/routing/preview`) have zero Sentry tagging.
- The `routing.level` tag maps to the MVP cascade: `client_graph` (heatmap+DFCI+trails Dijkstra), `personal` (user traces), `straight_line` (no path found)
- BRouter and OSRM are NOT in the current MVP cascade (frontend does client-side routing). The backend cascade still has them but they're rarely hit. Tag them as `brouter`/`osrm` if they appear.
- Frontend has `routing-metrics.ts` with console-only output — no Sentry bridge needed for this story (backend-only scope).

## Tasks / Subtasks

- [x] Task 1: Add Sentry tags to `/routing` endpoint (AC: #1, #3)
  - [x] 1.1: Import `sentry_sdk` at top of `routing.py` (move from inline import at line 326)
  - [x] 1.2: After `result = await ...` in `calculate_route()`, add `sentry_sdk.set_tag("routing.sport", sport)`, `sentry_sdk.set_tag("routing.method", result.method)`, `sentry_sdk.set_tag("routing.level", _method_to_level(result.method))`
  - [x] 1.3: In the `except TimeoutError` block, call `sentry_sdk.capture_exception()` before building fallback, and set tags `routing.level=straight_line`, `routing.method=straight_line`
  - [x] 1.4: Add `sentry_sdk.set_measurement("routing_duration_ms", elapsed_ms, "millisecond")` with timing

- [x] Task 2: Add Sentry tags to `/routing/multi` endpoint (AC: #1, #3)
  - [x] 2.1: After `result = await ...` in `multi_route()`, set tags: `routing.sport`, `routing.method` (from result), `routing.level` (from method)
  - [x] 2.2: In `except TimeoutError`, call `sentry_sdk.capture_exception()` and set fallback tags
  - [x] 2.3: Add duration measurement

- [x] Task 3: Add Sentry tags to `/routing/preview` endpoint (AC: #1)
  - [x] 3.1: After `alt1, alt2 = await ...` in `preview_route()`, set tags from alt1 result
  - [x] 3.2: In `except TimeoutError`, call `sentry_sdk.capture_exception()` and set fallback tags
  - [x] 3.3: Add duration measurement

- [x] Task 4: Normalize existing `/routing/proposals` Sentry tags (AC: #1)
  - [x] 4.1: Move `import sentry_sdk` from inline (line 326) to top-level import
  - [x] 4.2: Add `routing.level` and `routing.method` tags (currently only has `proposals.sport` and `proposals.count`)
  - [x] 4.3: Rename `proposals.sport` → `routing.sport` for consistency (keep `proposals.count` as-is)

- [x] Task 5: Create `_method_to_level()` helper (AC: #1, #2)
  - [x] 5.1: Add helper function mapping method strings to cascade levels:
    - `community_heatmap`, `smart_in_memory`, `client_graph`, `hybrid` → `"client_graph"`
    - `personal_traces` → `"personal"`
    - `brouter` → `"brouter"`
    - `osrm` → `"osrm"`
    - `straight_line`, `fallback`, `no_route`, `off_heatmap` → `"straight_line"`
  - [x] 5.2: Place at module level in `routing.py` (pure function, no dependencies)

- [x] Task 6: Add `routing.endpoint` tag to all endpoints (AC: #2)
  - [x] 6.1: Set `sentry_sdk.set_tag("routing.endpoint", "single")` in `/routing`
  - [x] 6.2: Set `sentry_sdk.set_tag("routing.endpoint", "multi")` in `/routing/multi`
  - [x] 6.3: Set `sentry_sdk.set_tag("routing.endpoint", "proposals")` in `/routing/proposals`
  - [x] 6.4: Set `sentry_sdk.set_tag("routing.endpoint", "preview")` in `/routing/preview`

- [x] Task 7: Tests (AC: #1, #3)
  - [x] 7.1: Add pytest test verifying Sentry tags are set on `/routing` response (mock `sentry_sdk.set_tag`)
  - [x] 7.2: Add pytest test verifying `sentry_sdk.capture_exception()` is called on TimeoutError
  - [x] 7.3: Add pytest test for `_method_to_level()` mapping (12 test cases)
  - [x] 7.4: Verify existing backend tests pass — 604 passed, 0 failed

## Dev Notes

### Current Sentry State

**Backend** (`backend/app/main.py:19-26`):
```python
_sentry_dsn = os.environ.get("SENTRY_DSN", "")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=0.1,
        environment=os.environ.get("ENV", "production"),
        enable_tracing=True,
        send_default_pii=True,
    )
```

**Only instrumented endpoint** (`backend/app/api/routing.py:326-344`):
```python
import sentry_sdk  # inline import
with sentry_sdk.start_span(op="routing.proposals", ...):
    proposals, ... = routing_service.compute_proposals(...)
sentry_sdk.set_measurement("proposals_duration_ms", _elapsed_ms, "millisecond")
sentry_sdk.set_tag("proposals.sport", sport)
sentry_sdk.set_tag("proposals.count", str(len(proposals)))
```

### Key Design Decisions

1. **Backend-only scope**: This story instruments the backend API endpoints. Frontend Sentry (SentryProvider.tsx) already exists but has 0% trace sample rate — no frontend changes needed.
2. **`sentry_sdk.set_tag()` is safe without DSN**: When `SENTRY_DSN` is not set, `sentry_sdk` is a no-op — all `set_tag`/`capture_exception`/`start_span` calls silently do nothing. No guard needed.
3. **Tag naming convention**: Use `routing.` prefix for all routing tags (consistent with Sentry best practices for custom tags).
4. **`capture_exception` on timeout**: Currently the `TimeoutError` handler builds a straight-line fallback silently. This should also report to Sentry so admins can track timeout frequency.
5. **No spans on `/routing` and `/routing/multi`**: These endpoints use `asyncio.wait_for(asyncio.to_thread(...))` which doesn't propagate Sentry spans well. Use tags + measurements instead of wrapping in `start_span`.

### Routing Method → Level Mapping

| Method | Level | Description |
|--------|-------|-------------|
| `community_heatmap` | `client_graph` | Heatmap Dijkstra (most common) |
| `smart_in_memory` | `client_graph` | In-memory smart routing |
| `client_graph` | `client_graph` | Direct client graph routing |
| `hybrid` | `client_graph` | Mixed source routing |
| `personal_traces` | `personal` | User's own traces |
| `brouter` | `brouter` | BRouter fallback (rare in MVP) |
| `osrm` | `osrm` | OSRM fallback (rare in MVP) |
| `straight_line` | `straight_line` | No path found |
| `fallback` | `straight_line` | Generic fallback |
| `no_route` | `straight_line` | Routing failure |
| `off_heatmap` | `straight_line` | Off-graph bridging |

### Existing Functions to Reuse (DO NOT reinvent)

- `routing_service.compute_route()` — returns `RouteResult` with `.method`, `.sport`, `.quality_score`
- `routing_service.compute_proposals()` — returns list of `ProposalResult`
- `RouteResult.method` field — string identifying which cascade level succeeded

### Project Structure Notes

- All changes in `backend/app/api/routing.py` (single file)
- Tests in `backend/tests/test_routing_sentry.py` (new file)
- No frontend changes
- No new dependencies (sentry-sdk already installed)

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 5.1] — Original story ACs
- [Source: backend/app/main.py:19-26] — Sentry SDK initialization
- [Source: backend/app/api/routing.py:326-344] — Only existing Sentry instrumentation (proposals endpoint)
- [Source: backend/app/api/routing.py:19-117] — `/routing` endpoint (no Sentry tags)
- [Source: backend/app/api/routing.py:126-182] — `/routing/multi` endpoint (no Sentry tags)
- [Source: backend/app/api/routing.py:379-435] — `/routing/preview` endpoint (no Sentry tags)
- [Source: backend/app/api/routing.py:65-78] — TimeoutError handler (no capture_exception)
- [Source: frontend/lib/routing-metrics.ts] — Frontend console-only metrics (out of scope)
- [Source: frontend/components/SentryProvider.tsx] — Frontend Sentry init (out of scope)

## Dev Agent Record

### Agent Model Used
Claude Opus 4.6

### Debug Log References
- New tests: 15 passed (`tests/test_routing_sentry.py`)
- Full backend suite: 604 passed, 1 skipped, 0 failed
- Fixed deprecation: `start_span(description=...)` → `start_span(name=...)` in proposals endpoint
- Code review fix H1: moved `capture_exception()` after `set_tag()` calls so error events have routing context
- Code review fix L1: removed unused `import pytest`
- Added test assertion verifying tag ordering before capture_exception

### Completion Notes List
- Task 5: Added `_method_to_level()` helper at module level — maps 11 known methods to 5 cascade levels (client_graph, personal, brouter, osrm, straight_line). Unknown methods default to straight_line.
- Task 1: Instrumented `/routing` with `routing.sport`, `routing.method`, `routing.level` tags + `routing_duration_ms` measurement. Added `capture_exception()` and fallback tags in TimeoutError handler.
- Task 2: Instrumented `/routing/multi` — extracts method from result dict. Same timeout capture pattern.
- Task 3: Instrumented `/routing/preview` — tags from alt1 result. Same timeout capture pattern.
- Task 4: Moved `import sentry_sdk` and `import time` from inline to top-level. Added `routing.method`, `routing.level` tags. Kept `proposals.sport` → renamed to `routing.sport`. Fixed deprecated `description` → `name` param on `start_span`.
- Task 6: Added `routing.endpoint` tag to all 4 routing endpoints (single, multi, proposals, preview).
- Task 7: Created `test_routing_sentry.py` with 15 tests: 12 for `_method_to_level()` mapping, 3 integration tests verifying tag/measurement/capture_exception behavior via mocked sentry_sdk.

### File List
- `backend/app/api/routing.py` — Top-level sentry_sdk + time imports, `_method_to_level()` helper, Sentry tags on all 4 endpoints, `capture_exception()` on timeout, deprecated `description` → `name`
- `backend/tests/test_routing_sentry.py` — New: 15 tests for Sentry observability tags
