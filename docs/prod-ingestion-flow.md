# Production ingestion flow — how GPX upload becomes a heatmap update

Single trace: a friend uploads a GPX, what happens in prod from t=0 to "the heatmap shows their ride." Companion to `docs/ingestion-pipeline.md` (dev/code focus) and `docs/migration-runbook.md` (operator focus). This one is for "explain it to me one time" understanding.

## Component map

```
                                  Friend's browser
                                        │
                           ① POST /gpx/upload (multipart)
                                        │
                                        ▼
                           Cloud Run "common-trails-api-prod"
                                  (FastAPI, scale-to-zero,
                                   1 GB RAM, 1 vCPU)
                                        │
                ┌──────────────────────┼──────────────────────┐
                │                      │                      │
              ② DB write             ③ enqueue task         ④ (optional)
                │                      │                    GCS upload
                ▼                      ▼                      │
          Cloud SQL                Cloud Tasks                ▼
        "common-trails-prod"      heat-compute queue   gs://YOUR-PROJECT
        (Postgres 17 PostGIS,                          -uploads/{user}/{
         db-f1-micro, $9/mo)                            activity}.gpx
                                        │
                                        ▼
                           ⑤ Cloud Run "common-trails-api-prod"
                              POST /internal/ingest/heat
                              (OIDC-verified, same image)
                                        │
                                        ▼
                              ⑥ map-matching
                                  │       │
                  high confidence │       │ unavail / mid / low
                                  ▼       ▼
                       Cloud Run            spatial fallback
                      "valhalla-prod"       in-process
                       (1 GB, scale-       _match_to_osm
                        to-zero)
                                  │       │
                                  └───┬───┘
                                      ▼
                           ⑦ heat_edges UPSERT (Cloud SQL)
                              + heat_edge_contributors
                                      │
                                      ▼
                           ⑧ enqueue matview-refresh task
                              (10 s debounce, const task name)
                                      │
                                      ▼
                              ⑨ enqueue artefact-rebuild task
                                 (5 min debounce, const task name)
                                      │
                                      └──────────────────┐
                                                         │
                                                         ▼
                                       ⑩ POST /internal/artefacts/rebuild
                                          - build_pmtiles → GCS
                                          - (optional) build .fgraph → GCS
                                                         │
                                                         ▼
                                               gs://YOUR-PROJECT
                                               -heatmap-artefacts/
                                                heatmap-display.pmtiles
                                                offroad-sud-est.fgraph
                                                ...
                                                         │
                                                         ▼
                                               Cloud CDN edge
                                                (5 min cache)
                                                         │
                                                         ▼
                                                Friend's browser
                                                (heatmap layer)
```

## Step-by-step with timings

### t=0 — friend clicks Upload

Browser sends `POST /gpx/upload` with the file (multipart, ≤ 10 MB GPX). API service receives.

### t=0–200 ms ── ① + ② + ③: synchronous

Inside the request handler in `app/api/gpx_upload.py`:

1. Parse GPX (~30 ms for a 50 km ride).
2. `ingest_activity(skip_heat_computation=True)` — writes a row to `activities` (Cloud SQL). Activity is now visible in `/me/activities`.
3. `archive_gpx(...)` background task — uploads the raw GPX to `gs://YOUR-PROJECT-uploads/{user}/{activity}.gpx` for safety (lets us re-ingest from raw bytes if something corrupts heat_edges later).
4. `enqueue_heat_compute(activity_id, user_id)` — pushes a Cloud Task. **Returns 202 to the browser.**

Friend's browser sees "Upload complete" within 200 ms. Activity is in the list. Heatmap not updated yet.

### t=0.2–30 s ── ⑤: Cloud Task fires

Cloud Tasks queue `heat-compute` dispatches the task. It hits `POST /internal/ingest/heat` on the same `api-prod` service (the queue's target URL). OIDC token signed by `tasks-invoker` SA; the handler verifies it.

This is on a Cloud Run instance — could be the same one that handled the upload (warm) or a new one (cold start, +5 s).

### t=0.5–25 s ── ⑥: map-matching

`_update_heat_edges` is called. Inside, `_match_to_osm`:

```python
if MAP_MATCHER_ENABLED:
    try:
        result = valhalla_match(coords, sport)
        if result.is_high_confidence():
            return _heat_edges_from_valhalla(result, ...), []
    except MatchUnavailable: pass            # silent fallback
    except MatchLowConfidence: return [], []  # skip — too noisy
return _match_to_osm_spatial(coords, sport, db)  # legacy
```

Calls `https://valhalla-prod-xxx.run.app/trace_attributes` over the internal Cloud Run mesh. Two scenarios:

- **Valhalla warm**: 200 ms p50, 1 s p95.
- **Valhalla cold**: ~10 s extra to extract tiles from `gs://YOUR-PROJECT-valhalla-tiles/valhalla-tiles-fr.tar.gz`. Friend doesn't see this — it's all post-202.

Result: a list of OSM-matched heat_edges with `match_source='valhalla'` + `match_confidence`. Or fallback to spatial.

### t=25–30 s ── ⑦: DB UPSERT

Bulk INSERT into `heat_edges` (partitioned by sport) + `heat_edge_contributors`. ON CONFLICT keeps the existing row's pass_count incremented. For a 50 km ride this is ~2–5k rows per sport-partition.

After this, the data is **in the DB**. But the browser is still showing stale PMTiles.

### t=30–40 s ── ⑧: matview refresh

The task handler enqueues a `matview-refresh` Cloud Task with:

- **Schedule**: now + 10 s.
- **Task name**: const `"matview-refresh"`.

Cloud Tasks accepts the first one with that name; subsequent enqueues during the 10-s window get rejected (duplicate name). Result: 50 uploads in 10 s → 1 refresh.

When the task fires (10 s later), it hits `POST /internal/matview/refresh` which runs `REFRESH MATERIALIZED VIEW CONCURRENTLY heat_edges_display`. Takes ~30 s on db-f1-micro for ~5 M edges.

After this: the **live MVT path** (`/heatmap/tiles/{z}/{x}/{y}.mvt`) is fresh. If the friend pans the map after t≈40 s (matview refresh), they see their ride if the frontend uses the live MVT layer (TBD — see "Frontend layer choice" below).

### t=30 s + 5 min ── ⑨ + ⑩: artefact rebuild

In parallel with ⑧, the heat-compute handler also enqueues an `artefact-rebuild` task:

- **Schedule**: now + 5 min.
- **Task name**: const `"artefact-rebuild"`.

Same dedup: a session with 50 uploads = 1 rebuild after the last one.

When that task fires (5 min after the last upload in a burst), it hits `POST /internal/artefacts/rebuild` which:

1. `build_pmtiles.main(...)` → writes `/tmp/heatmap-display.pmtiles` (~30 s for 5 M edges, ~3 MB output).
2. `gsutil cp /tmp/heatmap-display.pmtiles gs://YOUR-PROJECT-heatmap-artefacts/` with `Cache-Control: public, max-age=300`.
3. (optional, longer debounce) `wasm-router/prebuild` → 18 .fgraph files → GCS.

At t≈5 min: the static **PMTiles file in GCS is fresh**. Browsers refresh within their `max-age=300` window.

### t=5 min + ε — friend's browser sees the update

The map page's heatmap layer reads `pmtiles://YOUR-CDN/heatmap-display.pmtiles`. The browser cache holds the previous version with `max-age=300`. After the cache TTL or a manual refresh, it pulls the new bytes.

For "see my own upload right now" — that's the live MVT path:

| Layer | Source | Lag after upload |
|---|---|---|
| `community-trails-line` (PMTiles) | static GCS file | ≤ 5 min + 5 min cache |
| `community-trails-line-live` (NEW, live MVT) | matview, refreshed after ingest | ≤ 40 s |

**Frontend layer choice**: heatmap-pipeline.md § Stage 4 documents both paths exist. For the soft launch, use both — PMTiles for cached low-zoom overview (panning is fast, edge cache keeps origin bill near zero), live MVT for "I just rode this" feedback at high zoom. See `docs/heatmap-komoot-parity.md` for layer-style integration.

## What runs where

| Component | Service | Cost @ 3 friends |
|---|---|---|
| API | Cloud Run `common-trails-api-prod` | < $1/mo (scale-to-zero, ~50 invocations/wk) |
| DB | Cloud SQL `common-trails-prod` (db-f1-micro) | $9/mo |
| Heat-compute task queue | Cloud Tasks (free tier) | $0 |
| Matview-refresh task queue | Cloud Tasks (free tier) | $0 |
| Artefact-rebuild task queue | Cloud Tasks (free tier) | $0 |
| Valhalla | Cloud Run `valhalla-prod` (scale-to-zero) | < $1/mo |
| Raw GPX storage | GCS bucket `…uploads` | < $0.10/mo |
| Heatmap artefacts | GCS bucket `…heatmap-artefacts` (~50 MB) | < $0.01/mo |
| CDN | Cloud CDN over the artefacts bucket | < $0.50/mo (most fetches free tier) |
| Valhalla tiles | GCS bucket `…valhalla-tiles` | < $0.01/mo |
| **Total** | | **~$10–12/mo** |

## Failure modes & retries

| Stage | Failure | Behaviour |
|---|---|---|
| ① upload | network drop client-side | client retries (it's a single HTTP POST) |
| ② DB write | Cloud SQL hiccup | request 5xx, friend re-uploads |
| ③ enqueue heat-compute | Cloud Tasks 5xx | request 5xx, friend re-uploads |
| ⑤ heat-compute task | API service down | Cloud Tasks retries 3× with exponential backoff (max 10 min) |
| ⑥ Valhalla call | service down / 5xx | falls through to spatial matcher silently — heat_edges still produced |
| ⑦ DB UPSERT | DB down | task retries (queue config: max_attempts=3) |
| ⑧ matview refresh | another refresh in flight | new one is no-op (defensive guard `refresh_display_matview`) |
| ⑨ artefact-rebuild | task creation fails | next ingest re-enqueues. Worst case: PMTiles is 5 min stale. |
| ⑩ build_pmtiles | OOM | task retries with exponential backoff. If persistent: Cloud Run instance with > 1 GB RAM is needed; document escalation. |
| GCS upload | service down | `gsutil cp --retries 3` (default). Worst case task fails, retries, friend's update visible 5–10 min later. |

## Things that AREN'T in the prod path (and why)

- **No cron**. Rebuilds are event-driven (see migration-runbook.md § "Why no cron"). A clock-based rebuild rebuilds on no data; risks racing an in-flight ingest.
- **No DB triggers** firing the rebuild. Cloud Tasks gives observability (queue depth, retries) and dedup (task names) that DB triggers can't.
- **No PubSub**. Cloud Tasks is the simpler primitive for "fire X, in Y seconds, with name Z". PubSub adds persistence/ordering guarantees we don't need.
- **No CDN cache invalidation API call**. We just ride the 5-min `max-age` — friends accept "up to 5 min lag" for the cached layer; the live MVT layer covers immediate feedback.

## Mental model in one sentence

> **Friend uploads → 202 in 200 ms → matview fresh in 40 s → PMTiles fresh in 5 min**, all event-driven by Cloud Tasks with const-task-name dedup, no clock-based rebuilds. Costs ≈ $10/mo at 3-friend scale, scales linearly with users (mostly DB CPU + Cloud Run invocations).

## Pointers to the actual code / config

- Backend ingest path: `backend/app/api/gpx_upload.py` (sync) → `backend/app/services/cloud_tasks.py` (enqueue) → `backend/app/api/internal_ingest.py` (async handler).
- Map-matcher: `backend/app/services/map_matcher.py`.
- Auto-rebuild hooks: `backend/app/services/ingest.py` `_schedule_matview_refresh`, `_schedule_pmtiles_rebuild` (dev) — to be replaced with `enqueue_*` Cloud Tasks calls in prod (sketched in `docs/migration-runbook.md` § "Heatmap artefact freshness").
- Terraform: `infra/terraform/main.tf` — adds the Cloud Run services, Cloud Tasks queues, GCS buckets, IAM bindings.
- Operator runbook: `docs/migration-runbook.md` § "Soft-launch playbook — 3 friends on prod".
