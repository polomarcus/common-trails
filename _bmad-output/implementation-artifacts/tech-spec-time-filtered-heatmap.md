---
title: 'Time-Filtered Heatmap'
slug: 'time-filtered-heatmap'
created: '2026-03-22'
status: 'implementation-complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack: [python, postgresql, postgis, fastapi, sqlalchemy, alembic, typescript, next.js, maplibre]
files_to_modify: [backend/app/db/models.py, backend/app/services/ingest.py, backend/app/api/heatmap.py, backend/alembic/versions/0024_add_activity_date_to_contributors.py, backend/app/cli/backfill_contributor_dates.py, frontend/app/map/page.tsx, backend/tests/test_ingest_direction.py]
code_patterns: [raw-sql-upsert, on-conflict-do-nothing, dynamic-where-builder, in-memory-cache-dict, useCallback-fetch, inline-pill-buttons]
test_patterns: [pytest, isolated-sport-name, geojson-fixture-helpers, fixture-cleanup-before-after]
---

# Tech-Spec: Time-Filtered Heatmap

**Created:** 2026-03-22

## Overview

### Problem Statement

The heatmap shows all-time cumulative data with no way to see recent activity. A road ridden 3 years ago but since abandoned looks identical to one ridden last week. Users cannot distinguish fresh, actively-used routes from stale historical data.

### Solution

Add an `activity_date` column to `HeatEdgeContributor`. At query time, filter edges where at least one contributor has `activity_date` within the selected window (30 days / 60 days / 1 year / all time). K-anonymity is still enforced on the filtered contributor set — an edge only appears if it has ≥ K distinct contributors within the time window.

### Scope

**In Scope:**
- Schema change: add `activity_date` (DateTime, nullable) to `HeatEdgeContributor`
- Update ingest pipeline to populate `activity_date` from `Activity.activity_date`
- Backfill migration for existing contributors via join to Activity table
- Backend: add `days` query param to `/heatmap/trails` endpoint
- Frontend: time filter dropdown (All time / 30 days / 60 days / 1 year) in heatmap controls
- K-anonymity enforced on time-filtered results

**Out of Scope:**
- Time-filtering heat cells (tile heatmap) — edges/trails only
- Time-bucketed aggregates (Option B — separate monthly tables)
- Changing the edge-clustering logic (separate spec: `tech-spec-heatmap-edge-clustering.md`)
- Historical time-series or trend visualization
- Time filter on DFCI trails (static OSM data, no temporal dimension)
- Time-filtering `/heatmap/summary` and `/heatmap/export` endpoints — these remain all-time only

## Context for Development

### Codebase Patterns

**Function signature change** (`ingest.py:220`): `_update_heat_edges(user_id, sport, geojson_str, activity_date: datetime | None = None)`. The `activity_date` parameter **must be a `datetime` object** (not a string) — it's passed directly to raw SQL via `sa_text()` which does not auto-coerce strings. The caller `ingest_activity()` passes `activity_data.get("activity_date") or datetime.now(timezone.utc)` — the parsing layer (Strava/GPX) already produces `datetime` objects in `activity_data`.

**Contributor INSERT** (`ingest.py:372-377`): Raw SQL `INSERT INTO heat_edge_contributors (edge_key, user_id_hash) VALUES (:edge_key, :uid_hash) ON CONFLICT DO NOTHING RETURNING edge_key`. Must change to:
```sql
INSERT INTO heat_edge_contributors (edge_key, user_id_hash, activity_date)
VALUES (:edge_key, :uid_hash, :activity_date)
ON CONFLICT (edge_key, user_id_hash) DO UPDATE
  SET activity_date = GREATEST(excluded.activity_date, heat_edge_contributors.activity_date)
RETURNING edge_key
```

**Edge public query — time-filtered path** (`ingest.py:410-470`): `get_heat_edges_public()` builds WHERE dynamically. When `days` is specified, use a JOIN query instead:
```sql
SELECT he.edge_key, he.sport, COUNT(DISTINCT hec.user_id_hash) AS filtered_user_count,
       he.pass_count, he.forward_count, he.backward_count,
       he.ele_delta_m, he.slope_grade, he.surface_type, he.highway_type,
       he.tracktype, he.smoothness, he.trail_network, he.trail_type,
       ST_AsGeoJSON(he.geometry)::text AS geom_json
FROM heat_edges he
JOIN heat_edge_contributors hec ON hec.edge_key = he.edge_key
WHERE hec.activity_date >= NOW() - MAKE_INTERVAL(days => :days)
  [AND he.sport = :sport]
  [AND ST_Intersects(he.geometry, ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326))]
GROUP BY he.edge_key, he.id, he.sport, he.pass_count, he.forward_count, he.backward_count,
         he.ele_delta_m, he.slope_grade, he.surface_type, he.highway_type,
         he.tracktype, he.smoothness, he.trail_network, he.trail_type, he.geometry
HAVING COUNT(DISTINCT hec.user_id_hash) >= :k
```
Note: explicit GROUP BY on all selected columns (not just `he.id`) for clarity and portability. PostgreSQL allows `GROUP BY PK` but listing all columns is less confusing for dev agents.
When `days is None` or `days <= 0`: use existing fast query with `heat_edges.user_count >= :k` (no JOIN, no regression).
Guard: `if days is not None and days > 0:` — explicit check to avoid truthy/falsy bugs.
`# NOTE: two query paths — if you add columns to SELECT, update both.`

**Heatmap cache** (`heatmap.py:193-213`): In-memory dict `_trails_gz_cache` keyed by `sport or "all"`, 300s TTL for full-bbox, 120s for bbox-filtered. Cache key must include `days`: `f"{sport or 'all'}:{days or 'all'}"`.

**Endpoint changes** (`heatmap.py:175-270`): `/heatmap/trails` needs 3 changes:
1. Accept `days: int | None = Query(None)` param
2. Pass `days` to `get_heat_edges_public()`
3. Include `days` in both `cache_key` and `bbox_cache_key`: `f"{sport or 'all'}:{days or 'all'}"`. Note: filtered views use the same 300s/120s TTL as all-time — acceptable since `NOW()` shift over 5min is negligible vs 30-day windows

**Frontend state** (`page.tsx:1214`): Add `const [heatmapDays, setHeatmapDays] = useState<number | null>(null)` near `heatmapSport`. `null` = all time (default — preserves fast path, zero regression risk).

**Frontend fetch** (`page.tsx:3878-3895`): `loadCommunityHeatmap(map, sport, days)` — add `days` param. When `days` is not null, append `params.set('days', String(days))`. **All call sites** must pass both `heatmapSport` and `heatmapDays` — including sport-change triggers and route mode auto-set.

**Frontend effect deps** (`page.tsx:4095-4103`): The `useEffect` for heatmap loading must add `heatmapDays` to its dependency array: `[mapInstance, layers.heatmap, heatmapSport, heatmapDays, loadCommunityHeatmap]`.

**Time filter pills** (`page.tsx:6580-6608`): Below sport pills, same `#e63946` active / `#f0f0f0` inactive style. Values:
```typescript
[
  { value: null, label: 'Tout' },
  { value: 365, label: '1 an' },
  { value: 60, label: '60j' },
  { value: 30, label: '30j' },
]
```
**Route mode:** time pills become read-only (same pattern as sport pills — `cursor: 'default'`, dimmed opacity). Value persists but cannot be changed. Do NOT reset to "Tout" on route mode entry.

**Response shape consistency:** Both query paths (all-time and filtered) must return identical dict shape. The filtered path's `COUNT(DISTINCT hec.user_id_hash)` maps to the same `user_count` key. `compute_heat_score()` uses the filtered `user_count` (not the all-time value from `heat_edges`).

**`pass_count` in filtered view:** Intentionally shows all-time `pass_count` (from `heat_edges.pass_count`), not recent-only passes. The time filter answers "which roads are currently active?" — the total pass count adds useful popularity context. Documented as intentional design choice.

**Alembic migration** (`0024_add_activity_date_to_contributors.py`):
1. `op.add_column('heat_edge_contributors', Column('activity_date', DateTime(timezone=True), nullable=True))`
2. No index initially — add later only if filtered queries exceed 300ms in production (500k rows = fast sequential scan).
3. **No SQL backfill in migration** — see backfill note below.

**Backfill** (Python script, NOT raw SQL): The `user_id_hash` column stores `hash(user_id) & 0xFFFFFFFF` (Python's `hash()` with process-specific PYTHONHASHSEED). This value **cannot be reconstructed in SQL** (MD5/SHA don't match).

**PYTHONHASHSEED blocker & fix:** Python 3.13 randomizes `hash()` per process by default. A CLI script (`python -m ...`) starts a new process with a new seed — it will NOT match existing hash values. **Fix (prerequisite):** Set `PYTHONHASHSEED=0` (or a fixed value) in the Docker entrypoint / docker-compose environment for the backend container. This must be done BEFORE any activities are ingested, OR existing `user_id_hash` values must be recomputed. Since the current codebase already has this issue (hash values in DB depend on the process that wrote them), we must either:
- **Option A (recommended):** Add `PYTHONHASHSEED=0` to `docker-compose.yml` backend service environment, re-ingest all activities (drops and recreates heat data), then run backfill. This also fixes the pre-existing dedup issue.
- **Option B:** Switch `user_id_hash` from `hash()` to a deterministic hash, e.g. `int(hashlib.sha256(user_id.encode()).hexdigest()[:8], 16)` (32-bit int from SHA256, deterministic). Requires a migration to recompute all existing hash values + column stays BigInteger. More work but permanent fix.

**Backfill approach: re-ingest heat edges with activity_date.**

The per-user-max-date approach (grouping by `user_id_hash`, stamping all edges) is **wrong** — it marks a user's 2023 edge as "ridden yesterday" if they rode any edge yesterday. This defeats the time filter for backfilled data.

**Correct approach: per-activity re-trace.** For each activity, call `_update_heat_edges()` with the correct `activity_date`. The GREATEST logic in the contributor INSERT naturally keeps the per-edge maximum. This is effectively a re-ingest of heat data (contributor dates only — edge counts stay correct via the ON CONFLICT DO UPDATE).

```python
# backfill_contributor_dates.py (run inside backend container with fixed PYTHONHASHSEED)
activities = db.query(Activity).filter(Activity.contribute_heatmap == True).order_by(Activity.activity_date.asc()).all()
for i, act in enumerate(activities):
    date = act.activity_date or act.created_at
    sport = act.sport or "road"
    if act.geometry_geojson:
        _update_heat_edges(act.user_id, sport, act.geometry_geojson, activity_date=date)
    if i % 100 == 0:
        logger.info(f"Backfill progress: {i}/{len(activities)}")
```

This re-processes each activity's geometry through the same snapping + edge computation pipeline, so each contributor row gets the correct per-edge date. Order by `activity_date ASC` ensures GREATEST keeps the latest date per edge. With ~500-2000 activities, takes 1-5 minutes (each activity processes ~100-1000 edges).

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `backend/app/db/models.py:365-371` | `HeatEdgeContributor` model — add `activity_date` column |
| `backend/app/services/ingest.py:372-379` | Contributor INSERT — change `ON CONFLICT DO NOTHING` → `GREATEST` |
| `backend/app/services/ingest.py:410-470` | `get_heat_edges_public()` — add time-filtered query path |
| `backend/app/api/heatmap.py:175-270` | `/heatmap/trails` endpoint — add `days` param + cache key |
| `backend/alembic/versions/0024_*` (NEW) | Alembic migration: add `activity_date` column (no backfill) |
| `backend/app/cli/backfill_contributor_dates.py` (NEW) | Python backfill script — must run in container with same PYTHONHASHSEED |
| `frontend/app/map/page.tsx:1214` | `heatmapSport` state — add `heatmapDays` state nearby |
| `frontend/app/map/page.tsx:3878-3895` | `loadCommunityHeatmap()` — pass `days` param |
| `frontend/app/map/page.tsx:6580-6608` | Sport filter UI — add time filter dropdown |
| `backend/tests/test_ingest_direction.py` | Existing edge tests — add time-filter tests |
| `backend/app/db/models.py:256-280` | `Activity` model — has `activity_date` + `created_at` (source for backfill) |

### Technical Decisions

#### ADR: Query-Time Filtering (Option A) vs Time-Bucketed Aggregates (Option B)

| Criteria (weight) | Option A: `activity_date` on contributor | Option B: Monthly aggregate tables |
|---|---|---|
| Simplicity (30%) | ★★★★★ — 1 column addition | ★★☆☆☆ — new partitioned tables |
| Read perf (20%) | ★★★☆☆ — JOIN + WHERE (~100-300ms) | ★★★★★ — pre-aggregated (~50ms) |
| Clustering compat (20%) | ★★★★★ — additive change | ★★☆☆☆ — migration must touch N tables |
| Storage efficiency (10%) | ★★★★★ — 8 bytes/row | ★★☆☆☆ — 2-10x duplication |
| Backfill complexity (10%) | ★★★★☆ — Python script, per-activity re-trace | ★★☆☆☆ — replay all history |
| Accuracy (10%) | ★★★★★ — exact dates | ★★★★☆ — bucket boundary artifacts |
| **Weighted total** | **4.3** | **2.8** |

**Decision: Option A.** Rationale:
1. Dataset is small (~180k edges, ~500k contributor rows) — query-time filtering is well within PostgreSQL's comfort zone.
2. Edge-clustering spec is in-flight — Option B would create dangerous coupling (every clustering merge must touch N monthly tables).
3. Single column addition ships in days, not weeks.
4. **Upgrade path:** if the platform grows to millions of contributors, add a periodic materialization job later. Option A's schema remains the source of truth.

#### Why `activity_date` on `HeatEdgeContributor` (5 Whys validation)

| Level | Question | Answer | Validated? |
|-------|----------|--------|------------|
| 1 | Why time filter at all? | Users can't distinguish fresh routes from stale 3-year-old data | ✅ Real user need |
| 2 | Why not use existing data? | No edge→activity link exists — severed by design for K-anonymity | ✅ Privacy architecture |
| 3 | Why on contributor, not edge? | K-anonymity needs per-contributor dates to count distinct users within time window. A single `last_date` on `HeatEdge` can't enforce K≥2 per window | ✅ K-anonymity requires it |
| 4 | Why only most recent date, not all dates? | "Is this edge currently active?" needs only the latest ride per contributor. Storing all dates = N rows per user/edge, 50x storage, more complex queries — for no additional signal | ✅ Correct semantics |
| 5 | Why date directly, not `activity_id` FK? | `activity_id` would create a direct link back to a user's activity, breaking the privacy boundary. Also: multi-ride PK problem, extra JOIN cost | ✅ Preserves privacy |

**Conclusion:** Design is well-grounded at every level. No weak assumptions found.

#### Other Decisions

1. **`activity_date` not `created_at`** — use the actual ride date, not import date. A bulk Strava import of 6 months of history should reflect the correct dates.
2. **Nullable for backward compat** — existing contributors get backfilled, but NULL is acceptable (treated as "unknown date" = included in all-time only). For GPX uploads with no date metadata, fallback to `COALESCE(activity.activity_date, activity.created_at)` so upload date is used rather than NULL.
3. **K-anonymity on filtered set** — if a 30-day filter drops a road below K=2 distinct contributors, the edge disappears from the filtered view. Privacy guarantee holds regardless of time window.
4. **Backfill join path caveat** — `HeatEdgeContributor` stores `user_id_hash`, not `user_id`. Backfill must hash `activities.user_id` to match against `heat_edge_contributors.user_id_hash`. The path: hash each activity's user_id → match contributor rows → set `activity_date` from the activity.
5. **"All time" bypasses JOIN** — when no time filter is selected, use the existing `heat_edges.user_count` column directly (current behavior). Only use the contributor JOIN query when a time window is specified. Avoids unnecessary performance regression for the default view.
6. **Server-side filtering mandatory** — client-side filtering (returning `last_activity_date` per edge and filtering in JS) would leak which roads had recent activity even when below K-anonymity threshold. Time filtering must happen server-side.
7. **Routing graph stays all-time** — the client-graph for A* routing (`/routing/graph/{sport}/full.json`) is NOT affected by the time filter. Time filter is a visualization tool, not a routing constraint.
8. **GREATEST on conflict** — contributor INSERT uses `ON CONFLICT DO UPDATE SET activity_date = GREATEST(excluded.activity_date, heat_edge_contributors.activity_date)` to always keep the most recent ride date per contributor/edge pair.

#### Performance Profile (from panel analysis)

| # | Finding | Severity | Mitigation |
|---|---------|----------|------------|
| 1 | No index on `activity_date` → sequential scan on filtered query | **LOW** | ~500k rows = fast scan. Add index later only if filtered queries exceed 300ms |
| 2 | "All time" query slower if routed through JOIN | **MEDIUM** | Skip JOIN for all-time — use existing `heat_edges.user_count` (Decision #5) |
| 3 | NULL `activity_date` invisible in filtered views | **LOW** | Acceptable — NULL = only visible in "All time" mode |
| 4 | Cache key multiplication (×4 time windows) | **LOW** | Acceptable — ~10MB total at current scale |
| 5 | Client-side filtering breaks K-anonymity | **HIGH** | Server-side only (Decision #6) |
| 6 | Sparse results on short time windows look broken | **LOW** | Users understand filters — they'll switch to longer window. Add info badge later if feedback demands |
| 7 | Dropdown switch triggers re-fetch + flicker | **LOW** | Loading state: dim heatmap layer during fetch |
| 8 | Routing graph unaffected by time filter | **NONE** | No change needed, document only (Decision #7) |

## Implementation Plan

### Tasks

- [x] Task 1: Add `activity_date` column to `HeatEdgeContributor` model
  - File: `backend/app/db/models.py`
  - Action: Add `activity_date = Column(DateTime(timezone=True), nullable=True)` to the `HeatEdgeContributor` class (after `user_id_hash`, line 370)
  - Notes: Nullable — backfill populates existing rows. NULL = "unknown date" = only visible in "All time" mode.

- [x] Task 2: Create Alembic migration
  - File: `backend/alembic/versions/0024_add_activity_date_to_contributors.py` (NEW)
  - Action: `op.add_column('heat_edge_contributors', sa.Column('activity_date', sa.DateTime(timezone=True), nullable=True))`. Downgrade: `op.drop_column('heat_edge_contributors', 'activity_date')`.
  - Notes: No index, no backfill in migration. Backfill is a separate Python script (Task 3b) because `user_id_hash` uses Python's `hash()` which cannot be replicated in SQL. Add a comment in the migration file explaining this: `# Backfill runs as Python script (backfill_contributor_dates.py) — SQL cannot reconstruct hash() values`.

- [x] Task 3a: Fix PYTHONHASHSEED (prerequisite)
  - File: `docker-compose.yml`
  - Action: Add `PYTHONHASHSEED=0` to the backend service environment. This makes Python's `hash()` deterministic across restarts, fixing a pre-existing issue where contributor dedup could fail after container restart.
  - Notes: After adding this, existing `user_id_hash` values may not match. If the production DB has data from multiple container lifetimes, a re-ingest may be needed. For fresh/dev DBs, this is safe to add now.

- [x] Task 3b: Create Python backfill script
  - File: `backend/app/cli/backfill_contributor_dates.py` (NEW)
  - Action: Create CLI script that re-ingests heat edges per activity with correct `activity_date`:
    1. Load all activities with `contribute_heatmap=True`, ordered by `activity_date ASC`
    2. For each activity, call `_update_heat_edges(act.user_id, act.sport, act.geometry_geojson, activity_date=act.activity_date or act.created_at)`
    3. The GREATEST logic in the contributor INSERT naturally keeps the per-edge max date
    4. Log progress every 100 activities
    5. Add `--dry-run` flag
  - Notes: **Must run with fixed PYTHONHASHSEED** (Task 3a). This is a per-activity re-trace, NOT a per-user grouping (per-user would wrongly stamp all edges with the user's latest date). Order ASC ensures GREATEST keeps the latest per-edge date. With ~500-2000 activities, takes 1-5 minutes. Validate after: `SELECT COUNT(*) FROM heat_edge_contributors WHERE activity_date IS NULL` should be 0 (or only orphaned rows).

- [x] Task 4: Update `_update_heat_edges()` to accept and store `activity_date`
  - File: `backend/app/services/ingest.py`
  - Action:
    1. Change signature (line 220): `_update_heat_edges(user_id, sport, geojson_str, activity_date=None)`
    2. Add `"activity_date": activity_date` to `edges_to_upsert` dict (line 322)
    3. Change contributor INSERT (line 372-377) — both the SQL AND the params dict:
       ```sql
       INSERT INTO heat_edge_contributors (edge_key, user_id_hash, activity_date)
       VALUES (:edge_key, :uid_hash, :activity_date)
       ON CONFLICT (edge_key, user_id_hash) DO UPDATE
         SET activity_date = GREATEST(excluded.activity_date, heat_edge_contributors.activity_date)
       RETURNING (xmax = 0) AS is_insert
       ```
       Update the params dict (line 377) to include `activity_date`:
       ```python
       {"edge_key": key, "uid_hash": edge_data["user_id_hash"], "activity_date": edge_data["activity_date"]}
       ```
  - Notes: **Critical behavior change:** `ON CONFLICT DO UPDATE` + `RETURNING` always returns a row (both insert AND update), unlike `DO NOTHING` which returns nothing on conflict. The existing check `if contrib_result.fetchone()` would now be ALWAYS truthy, causing `new_contributor_keys` to contain ALL edges → massive `user_count` recalc on every ingest. **Fix:** Add `RETURNING (xmax = 0) AS is_insert` to the SQL (same pattern as heat_edges upsert at line 351), AND change the Python consuming code from:
       ```python
       # BEFORE (broken with DO UPDATE):
       if contrib_result.fetchone():
           new_contributor_keys.add(key)
       # AFTER (correct):
       row = contrib_result.fetchone()
       if row and row[0]:  # row[0] is is_insert (True only for actual INSERT, not UPDATE)
           new_contributor_keys.add(key)
       ```

- [x] Task 5: Update ALL 3 `_update_heat_edges()` call sites to pass `activity_date`
  - File: `backend/app/services/ingest.py`
  - Action: Update all three callers:
    1. **`ingest_activity()`** (line 1042): `_update_heat_edges(user_id, sport, geometry_geojson, activity_date=activity_data.get("activity_date") or datetime.now(timezone.utc))` — use `activity_data` dict directly (safer than `activity` ORM object after session close)
    2. **`_bulk_update_heat_from_activities()`** (line 1240): `_update_heat_edges(user_id, sport, geometry_geojson, activity_date=act_data.get("activity_date") or datetime.now(timezone.utc))` — `act_data` is the dict from Strava; add same `or` fallback as `ingest_activity` to prevent NULL dates
    3. **`load_seed_activities()`** (line 1448): `_update_heat_edges(act.user_id, sport, geometry_geojson, activity_date=act.activity_date or act.created_at)` — `act` is an Activity ORM object, session is still open here
  - Notes: Missing ANY call site means that path silently drops `activity_date`. The bulk import path (line 1240) is critical — it handles Strava batch imports.

- [x] Task 6: Add `days` parameter to `get_heat_edges_public()`
  - File: `backend/app/services/ingest.py`
  - Action:
    1. Add `days: int | None = None` parameter to function signature (line 410)
    2. Add guard: `if days is not None and days > 0:` — use the time-filtered JOIN query path
    3. In the filtered path: build the JOIN query as specified in Codebase Patterns. Map `filtered_user_count` → `user_count` in the result dict. Use `compute_heat_score(filtered_user_count)`.
    4. In the `else` path: existing query, unchanged (no regression)
    5. Add comment: `# NOTE: two query paths — if you add columns to SELECT, update both.`
  - Notes: `pass_count` always comes from `heat_edges.pass_count` (all-time) even in filtered mode — intentional design choice. **Column positions:** The filtered query must produce the same positional column order as the all-time query so the result-processing loop (lines 444-467) can be reused. `filtered_user_count` at position 2 maps to `uc = row[2]` — same as `user_count` in all-time path. Include `trail_score` computation: `"trail_score": round(compute_trail_score(tt), 2)` in both paths.

- [x] Task 7: Add `days` query param to `/heatmap/trails` endpoint
  - File: `backend/app/api/heatmap.py`
  - Action:
    1. Add parameter: `days: int | None = Query(None, ge=7, le=3650, description="Filter by last N days (min 7)")`
    2. Pass `days` to `ingest_service.get_heat_edges_public(sport=s, bbox=bbox, k=HEATMAP_K_ANONYMITY, days=days)`
    3. Update cache keys: `cache_key = f"{sport or 'all'}:{days or 'all'}"` and include `days` in `bbox_cache_key`
  - Notes: No changes to response shape — `feature_count` in metadata naturally reflects the filtered count.

- [x] Task 8: Add time filter pills to frontend
  - File: `frontend/app/map/page.tsx`
  - Action:
    1. Add state (near line 1214): `const [heatmapDays, setHeatmapDays] = useState<number | null>(null)`
    2. Add time filter pill row below sport pills (after line 6608), using the same button style (`#e63946` active / `#f0f0f0` inactive). Values: `[{value: null, label: 'Tout'}, {value: 365, label: '1 an'}, {value: 60, label: '60j'}, {value: 30, label: '30j'}]`
    3. In route mode: `cursor: 'default'`, dimmed opacity, onClick does nothing (same pattern as sport pills)
    4. Update `loadCommunityHeatmap` callback (line 3878): add `days: number | null` parameter. When `days !== null`, append `params.set('days', String(days))`
    5. Update ALL call sites of `loadCommunityHeatmap` to pass `heatmapDays`
    6. Update the `useEffect` at line 4095-4103: add `heatmapDays` to dependency array AND change the function call inside from `loadCommunityHeatmap(mapInstance, heatmapSport)` to `loadCommunityHeatmap(mapInstance, heatmapSport, heatmapDays)`
    7. Clear heatmap popup on time filter change: add `useEffect(() => { setHeatmapPopup(null); }, [heatmapDays]);` — same pattern as the existing `exploreSport` clearing at line 2024. Without this, stale popup data (all-time counts) remains visible after switching to a filtered view.
  - Notes: Loading state already exists (`setHeatmapLoading`). The dim effect during fetch is already implemented.

- [x] Task 9: Add backend tests for time-filtered heatmap
  - File: `backend/tests/test_ingest_direction.py`
  - Action: Add tests using isolated sport `"test_time"`:
    1. **activity_date stored on ingest:** Ingest an activity with `activity_date=2026-03-20`. Verify `heat_edge_contributors` row has matching `activity_date`.
    2. **GREATEST keeps most recent:** Ingest same user on same edge twice: first with `activity_date=2026-01-01`, then `2026-03-20`. Verify contributor `activity_date = 2026-03-20`.
    3. **GREATEST handles NULL:** Ingest with `activity_date=None`, then with `2026-03-20`. Verify `activity_date = 2026-03-20`.
    4. **Filtered query returns only recent edges:** Create 2 edges: one with contributors dated today, one dated 2 years ago. Call `get_heat_edges_public(days=30)`. Verify only the recent edge is returned (if it has ≥K contributors).
    5. **All-time query returns all edges:** Same setup. Call `get_heat_edges_public(days=None)`. Verify both edges returned.
    6. **K-anonymity on filtered set:** Edge with 3 all-time contributors but only 1 within 30 days. `get_heat_edges_public(days=30, k=2)` must NOT return this edge.
    7. **Filtered heat_score uses filtered user_count:** Verify `heat_score` in filtered results is computed from the filtered contributor count, not the all-time `user_count`.
  - Notes: Use sport `"test_time"` to isolate. Add cleanup in fixture matching existing pattern.

- [x] Task 10: Update clustering migration to preserve `activity_date`
  - File: `backend/app/cli/migrate_edge_clustering.py`
  - Action: Update the contributor redirect SQL (lines 257-263) from:
    ```sql
    INSERT INTO heat_edge_contributors (edge_key, user_id_hash)
    SELECT :canonical, user_id_hash
    FROM heat_edge_contributors WHERE edge_key = :old
    ON CONFLICT DO NOTHING
    ```
    To:
    ```sql
    INSERT INTO heat_edge_contributors (edge_key, user_id_hash, activity_date)
    SELECT :canonical, user_id_hash, activity_date
    FROM heat_edge_contributors WHERE edge_key = :old
    ON CONFLICT (edge_key, user_id_hash) DO UPDATE
      SET activity_date = GREATEST(excluded.activity_date, heat_edge_contributors.activity_date)
    ```
  - Notes: Without this, running the clustering migration after backfill would NULL all `activity_date` values for merged edges. This was identified as Risk #3 but must be an explicit task.

- [x] Task 11: Run backfill and validate
  - Action: `docker compose exec backend python -m app.cli.backfill_contributor_dates --dry-run` then without `--dry-run`
  - Notes: Verify with `SELECT COUNT(*) FROM heat_edge_contributors WHERE activity_date IS NULL`. Check heatmap visually with different time filters.

### Acceptance Criteria

- [x] AC 1: Given a new activity ingested with `activity_date = 2026-03-20`, when the contributor row is inserted, then `heat_edge_contributors.activity_date = 2026-03-20`.

- [x] AC 2: Given the same user rides the same edge twice (dates: Jan 1 and Mar 20), when both are ingested, then the contributor row has `activity_date = 2026-03-20` (most recent via GREATEST).

- [x] AC 3: Given `/heatmap/trails?days=30` is requested, when edges exist with contributors dated within 30 days AND ≥K distinct contributors in that window, then only those edges are returned.

- [x] AC 4: Given `/heatmap/trails` is requested (no `days` param), when called, then the response is identical to the pre-deployment response (all-time path unchanged, no regression).

- [x] AC 5: Given an edge with 3 all-time contributors but only 1 within 30 days, when `/heatmap/trails?days=30&k=2` is requested, then the edge is NOT returned (K-anonymity enforced on filtered set).

- [x] AC 6: Given the heatmap is visible on the map, when the user clicks "30j" in the time filter pills, then the heatmap reloads showing only recently-active edges.

- [x] AC 7: Given the user is in route mode, when they view the time filter pills, then the pills are visible but read-only (dimmed, not clickable).

- [x] AC 8: Given the backfill script has run, when `SELECT COUNT(*) FROM heat_edge_contributors WHERE activity_date IS NULL` is executed, then the result is 0 (or only orphaned rows with no matching activity).

- [x] AC 9: Given the user switches between time filters (Tout → 30j → 1 an), when each switch occurs, then the heatmap layer dims briefly (loading state) and re-renders with the filtered data.

- [x] AC 10: Given both `sport=mtb` and `days=30` are specified, when `/heatmap/trails?sport=mtb&days=30` is called, then only MTB edges with recent contributors (≥K in 30-day window) are returned.

- [x] AC 11: Given the edge-clustering migration runs after the backfill, when contributor rows are redirected to canonical edges, then `activity_date` values are preserved via GREATEST (not NULLed).

- [x] AC 12: Given the user has a heatmap popup open showing edge details, when they change the time filter, then the popup is dismissed (no stale data displayed).

## Additional Context

### Dependencies

- No new libraries needed — all Python stdlib + existing SQLAlchemy/PostGIS/FastAPI
- Depends on existing `HeatEdgeContributor` table and `heat_edge_contributors` PK `(edge_key, user_id_hash)`
- **Ordering constraint:** Alembic migration 0024 (this spec) MUST run before the edge-clustering migration script (`migrate_edge_clustering.py`), which redirects contributor rows and must preserve `activity_date`
- Frontend: no new npm packages — uses existing state management + fetch patterns

### Testing Strategy

**Unit tests** (Task 9):
- `activity_date` stored correctly on ingest
- GREATEST keeps most recent date (multi-ride scenario)
- GREATEST handles NULL → non-NULL transition
- Filtered query returns only recent edges
- K-anonymity enforced on filtered contributor set
- `heat_score` computed from filtered user_count
- All-time query unchanged (regression guard)

**Integration test** (existing E2E):
- Existing `test_ingest_direction.py` tests still pass (no regression on edge counting, direction tracking)
- `/heatmap/trails` endpoint returns valid GeoJSON with and without `days` param

**Manual validation** (Task 10):
- Backfill: verify NULL count is 0 post-backfill
- Visual: toggle time filter on map, verify heatmap changes
- Performance: `/heatmap/trails` (no param) p95 must not regress
- Performance: `/heatmap/trails?days=30` p95 < 500ms

### Risk Mitigations (from edge case + pre-mortem analysis)

| # | Scenario | Severity | Mitigation |
|---|----------|----------|------------|
| 1 | Multi-ride same edge: oldest `activity_date` wins with `ON CONFLICT DO NOTHING` | **HIGH** | Use `ON CONFLICT DO UPDATE SET activity_date = GREATEST(excluded.activity_date, heat_edge_contributors.activity_date)` — always keep most recent date |
| 2 | GPX upload with no date metadata → NULL `activity_date` | **MEDIUM** | Fallback chain: `COALESCE(activity.activity_date, activity.created_at)` at ingest time |
| 3 | Edge-clustering migration drops `activity_date` on contributor redirect | **HIGH** | Clustering migration's contributor redirect must include `activity_date` column + use `GREATEST(...)` on conflict. **Ordering constraint:** this spec's Alembic migration MUST run before clustering migration script |
| 4 | Cache key must include `days` param | **LOW** | Full-bbox: `f"{sport or 'all'}:{days or 'all'}"`. Bbox: `f"{sport or 'all'}:{days or 'all'}:{min_lon:.3f},..."` — always use `or 'all'` fallback |
| 5 | Backfill picks wrong date (multi-activity users) | **HIGH** | Backfill does per-activity re-trace via `_update_heat_edges()` (NOT per-user grouping). GREATEST in contributor INSERT keeps per-edge max date. See backfill script in Codebase Patterns. |
| 6 | All-time path accidentally goes through JOIN (truthy/falsy bug) | **MEDIUM** | Explicit check: `if days is not None and days > 0:` — not just `if days:`. Load test both paths pre-deploy |
| 7 | Default view regresses after deployment | **MEDIUM** | Before/after snapshot test: capture `/heatmap/trails` response pre-deploy, diff post-deploy. All-time code path = existing code untouched |

### Notes

- **Interaction with edge-clustering spec:** The clustering spec modifies `_update_heat_edges()` and the contributor insertion logic. This spec adds `activity_date` to the contributor INSERT — the two changes are additive and non-conflicting. The clustering spec's migration script must explicitly include `activity_date` in contributor redirects and use `ON CONFLICT DO UPDATE SET activity_date = GREATEST(...)` to preserve the most recent date when merging. **This spec's Alembic migration (adding the column) MUST run before the clustering migration script.**
- **Performance estimate:** The time-filtered query adds a JOIN + WHERE on `activity_date`. With ~500k contributor rows, sequential scan takes ~50-150ms — no index needed initially. Add index later if queries exceed 300ms.
- **Critical ingest change:** The contributor INSERT must change from `ON CONFLICT DO NOTHING` to `ON CONFLICT (edge_key, user_id_hash) DO UPDATE SET activity_date = GREATEST(excluded.activity_date, heat_edge_contributors.activity_date)`. This ensures multi-ride users always reflect their most recent ride on each edge.
- **Backfill must be Python, not SQL:** `user_id_hash` stores `hash(user_id) & 0xFFFFFFFF` which cannot be reconstructed in SQL. Backfill does per-activity re-trace via `_update_heat_edges()` with the correct `activity_date` — GREATEST in contributor INSERT keeps per-edge max date automatically. See Codebase Patterns section for the script. **Prerequisite:** PYTHONHASHSEED must be fixed (see PYTHONHASHSEED blocker in Codebase Patterns).
- **GREATEST(NULL, val) behavior:** PostgreSQL returns val — correct. GREATEST(NULL, NULL) returns NULL — also correct (no date data from either side). No special handling needed.
- **Default time window:** Frontend defaults to "All time" (`null`) — preserves the fast no-JOIN path. Users opt in to filtered views via the dropdown.
- **Deferred to post-MVP (add only if user feedback demands):**
  - Composite index on `(activity_date, edge_key)` — only if filtered queries exceed 300ms
  - Info badge "Showing N edges from last X days (M all-time)" — only if users find sparse results confusing
- **Pre-deploy validation checklist:**
  1. Load test: `/heatmap/trails` (no param) p95 must not regress vs pre-deploy baseline
  2. Load test: `/heatmap/trails?days=30` p95 < 500ms
  3. Backfill validation: `SELECT COUNT(*) FROM heat_edge_contributors WHERE activity_date IS NULL` → should be 0 (or only orphaned rows)
  4. Snapshot diff: `/heatmap/trails` response identical pre/post deploy (all-time path untouched)
