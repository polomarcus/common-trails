# Production Migration & Deploy Runbook

> ⚠️ **SUPERSEDED for DEPLOYS.** Prod is deployed **ONLY** via
> `scripts/deploy-prod.sh` (see **`docs/prod-deploy.md`** — the current
> doctrine). **Terraform is frozen** (any `terraform apply` recreates DELETED
> resources) and the **`deploy-gcp` workflow is disabled**. Any `terraform
> apply` / push-to-main-to-deploy step below is **FORBIDDEN**. The
> Alembic-migration ordering + prod-gotcha content remains useful for reference.

This is the operator-facing checklist for shipping the May 2026 ingest-pipeline overhaul (PRs #210, #212–#215) to production. It captures the prod-only gotchas the agents flagged in their PR descriptions plus the lessons learned during the dev rollout.

The general posture: **everything in this batch is opt-in or backwards-compatible.** Nothing has to be done all at once. But there's an ordering that minimises downtime.

## Quick reference — order of operations

1. **Deploy the new image** (no schema changes yet — PRs are merged into main, image rebuild picks up the new code).
2. **Apply migration 0036** (PostGIS `geometry` column on `activities`) — see [§ 0036](#migration-0036-activitiesgeometry).
3. **Apply migration 0037** (no-op until activated) — see [§ 0037](#migration-0037-drop-heat_cells--deferred).
4. **Configure Cloud Tasks** (one-time terraform two-step) — see [§ Cloud Tasks](#cloud-tasks-async-ingest-pr-212).
5. **Rebuild PMTiles** if you've recently re-ingested or run any mass cleanup — see [§ PMTiles](#pmtiles-rebuild).
6. **Watch metrics for 48 h** — see [§ Health checks](#health-checks-after-deploy).

---

## Migration 0036 — `activities.geometry`

**What it does**: adds a binary `geometry(LineString, 4326)` column alongside the existing TEXT `geometry_geojson`, backfills it via `ST_GeomFromGeoJSON`, creates a GIST index. Both columns are dual-written by the new ingest code. **The TEXT column stays** — a follow-up PR will drop it once readers have all migrated.

### Cost in prod

The dev DB (4 278 activities) backfilled in seconds. **Estimate for prod:**

| step | dev (4 278 rows) | prod (project to scale) |
|------|------------------|---|
| ADD COLUMN | <100 ms | <100 ms (PG does not rewrite for nullable, no-default add) |
| Backfill (`ST_GeomFromGeoJSON` per row) | ~10 s | ~10 min per 100 k rows |
| `CREATE INDEX` (NOT CONCURRENTLY) | ~5 s | **~10 min for 1 M rows; HOLDS ACCESS EXCLUSIVE** |

**The `CREATE INDEX` line is the prod risk**: while it runs, *no writes can hit the activities table* (uploads will 5xx). On a 4 M-row prod table this could be 30+ minutes of write-blocked uploads.

### Recommended prod application

If your prod activities table is < 100 k rows, just run `alembic upgrade head` during low traffic. The whole migration finishes in a couple minutes.

If it's larger, **split the migration manually**:

```bash
# 1. ADD COLUMN — instant, no lock issue
psql … -c "ALTER TABLE activities ADD COLUMN IF NOT EXISTS geometry geometry(LineString, 4326)"

# 2. Backfill in chunks. Each chunk is a separate transaction.
#    Run during low-traffic hours; safe to interrupt + resume.
psql … <<'SQL'
DO $$
DECLARE
  v_done INT;
BEGIN
  LOOP
    WITH batch AS (
      SELECT id FROM activities
      WHERE geometry IS NULL AND geometry_geojson IS NOT NULL
      LIMIT 5000
    )
    UPDATE activities a SET geometry = ST_SetSRID(
      ST_Force2D(ST_GeomFromGeoJSON(a.geometry_geojson)), 4326
    )
    FROM batch
    WHERE a.id = batch.id
      AND ST_GeometryType(ST_GeomFromGeoJSON(a.geometry_geojson)) = 'ST_LineString';
    GET DIAGNOSTICS v_done = ROW_COUNT;
    EXIT WHEN v_done = 0;
    PERFORM pg_sleep(0.1);  -- yield CPU to other workloads
    RAISE NOTICE 'Backfilled %', v_done;
  END LOOP;
END $$;
SQL

# 3. Build the index CONCURRENTLY (no write lock)
psql … -c "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_activities_geometry ON activities USING GIST (geometry)"

# 4. Stamp alembic so it knows we're at 0036
docker compose exec backend alembic stamp 0036
```

### Rollback

`alembic downgrade 0035` drops the column + index. The TEXT column stays untouched, so no data is lost.

---

## Migration 0037 — drop `heat_cells` (deferred)

**What it does (currently)**: nothing. The migration file is in place but its `upgrade()` body is `pass` with a clear "DO NOT ENABLE YET" comment. PR #214 stopped writing to `heat_cells` at ingest time but kept the tables.

### When to activate

After **one full prod soak cycle** with the new code (recommend ~1 week of normal traffic + one full Strava resync), confirm:
- `/heatmap/export` returns sensible counts (the aggregation reads `heat_edges` directly).
- No alerts on the `/heatmap/stats` endpoint.
- Nothing is writing to `heat_cells` anymore (`SELECT MAX(updated_at) FROM heat_cells` should be older than the deploy).

Then edit `backend/alembic/versions/0037_drop_heat_cells_tables.py`:

```python
# Replace `pass` with:
def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS heat_cell_contributors")
    op.execute("DROP TABLE IF EXISTS heat_cells")

def downgrade() -> None:
    raise NotImplementedError(
        "heat_cells drop is one-way — restore from backup if needed"
    )
```

…and apply.

### Storage savings expected

In dev, `heat_cells` + `heat_cell_contributors` together are ~50 MB. In prod (~10× scale): ~500 MB freed.

---

## Cloud Tasks (async ingest, PR #212)

The async path replaces the synchronous heat-compute on `POST /gpx/upload` and `POST /imports/files` with a Cloud Tasks-driven background job. **Until you complete the two-step terraform apply below, the path falls back to the legacy inline behaviour** — no functional regression, just no cost win.

### One-time prod setup

```bash
# Step 1 — apply the terraform with TF_VAR_api_public_url=""
#         (this creates the Cloud Run service, Cloud Tasks queue, and SAs)
terraform -chdir=infra/terraform apply

# Step 2 — capture the API URL terraform just created
TF_VAR_api_public_url="$(terraform -chdir=infra/terraform output -raw api_url)"
export TF_VAR_api_public_url

# Step 3 — re-apply so the Cloud Run env var INTERNAL_HEAT_HANDLER_URL
#         points at the freshly-created service URL. Cloud Tasks needs
#         this URL to sign OIDC tokens with the right audience.
terraform -chdir=infra/terraform apply
```

The chicken-and-egg is real: a Cloud Run service can't reference its own `.uri` inside its own template, so the operator has to pass the URL on the second apply. Setting `TF_VAR_api_public_url=""` (the default) keeps the legacy inline path; setting the URL flips Cloud Tasks on.

### Verifying it's live

After step 3:

```bash
# 1. Tail the API logs and upload a GPX. You should see
#    "Enqueued heat compute task for activity_id=…" log line.
gcloud run services logs tail common-trails-api-prod --region europe-west1

# 2. Confirm the queue exists + has the right backoff policy
gcloud tasks queues describe heat-compute --location europe-west1
```

### Rolling back

Set `TF_VAR_api_public_url=""` and re-apply. The handler stays in the image but the upload paths fall through to the inline path because Cloud Tasks env is incomplete.

---

## Valhalla (map-matching) — prod deploy

Adds the post-May-2026 ingest map-matcher to prod. Off by default until you set `MAP_MATCHER_ENABLED=true` on the API service. Without this section, ingest works fine via the legacy spatial matcher.

### Cost ceiling

| Item | $/mo |
|---|---|
| Cloud Run `valhalla-prod` (scale-to-zero, 1 GB RAM) | < $1 (50 cold starts × ~10 s × free-tier-adjacent vCPU billing) |
| GCS tile storage (~200 MB for Occitanie + PACA) | < $0.01 |
| Egress within same region | $0 |
| **Total addition** | **< $1/mo** |

This stays compatible with the soft-launch $10/mo target. Friends won't notice the cold start because ingest is already async via Cloud Tasks — they get a 202 immediately, the map-match runs in the background.

### One-time setup, local

Build the tiles locally **once** so you don't pay Cloud Run CPU time to build them on first deploy:

```bash
# 1. Make sure the local valhalla service is running with the right PBFs
#    (edit ``tile_files`` in docker-compose.yml first, e.g.
#       /custom_files/occitanie.osm.pbf /custom_files/paca.osm.pbf)
docker compose up -d valhalla
docker compose logs -f valhalla   # wait for "Running."

# 2. Tarball the built tiles
sudo tar czf /tmp/valhalla-tiles-fr.tar.gz -C data/valhalla_tiles .
ls -lh /tmp/valhalla-tiles-fr.tar.gz   # expect ~150–300 MB

# 3. Upload to a GCS bucket (use an existing one or create
#    gs://YOUR-PROJECT-valhalla-tiles)
gsutil cp /tmp/valhalla-tiles-fr.tar.gz \
    gs://YOUR-PROJECT-valhalla-tiles/valhalla-tiles-fr.tar.gz
```

The bucket can stay private — only Cloud Run needs read access (granted via IAM in the next step).

### Terraform additions

In `infra/terraform/main.tf`, append a new `valhalla` Cloud Run service. Skeleton (paste-ready, adjust project/region):

```hcl
# ── Valhalla map-matching service ─────────────────────────────────────
resource "google_storage_bucket" "valhalla_tiles" {
  name                        = "${var.gcp_project}-valhalla-tiles"
  location                    = var.gcp_region
  uniform_bucket_level_access = true
  versioning { enabled = false }
}

resource "google_service_account" "valhalla_sa" {
  account_id   = "valhalla-prod"
  display_name = "Valhalla map-matcher service account"
}

resource "google_storage_bucket_iam_member" "valhalla_tiles_reader" {
  bucket = google_storage_bucket.valhalla_tiles.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.valhalla_sa.email}"
}

resource "google_cloud_run_v2_service" "valhalla" {
  name     = "valhalla-prod"
  location = var.gcp_region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.valhalla_sa.email
    scaling {
      min_instance_count = 0   # scale-to-zero — cost saver
      max_instance_count = 1   # 3 friends don't need more
    }
    timeout = "300s"

    containers {
      image = "ghcr.io/gis-ops/docker-valhalla/valhalla:latest"
      resources {
        limits = {
          memory = "1Gi"
          cpu    = "1"
        }
      }
      ports { container_port = 8002 }

      env {
        name  = "tile_urls"
        value = "gs://${google_storage_bucket.valhalla_tiles.name}/valhalla-tiles-fr.tar.gz"
      }
      env {
        name  = "use_tiles_ignore_pbf"
        value = "True"
      }
      env {
        name  = "force_rebuild"
        value = "False"
      }
      env {
        name  = "server_threads"
        value = "1"
      }

      startup_probe {
        http_get {
          path = "/status"
          port = 8002
        }
        initial_delay_seconds = 30
        period_seconds        = 10
        failure_threshold     = 30   # ~5 min for tar download + extract
      }
    }
  }
}

# Allow the API service to call valhalla
resource "google_cloud_run_service_iam_member" "valhalla_invoker" {
  service  = google_cloud_run_v2_service.valhalla.name
  location = google_cloud_run_v2_service.valhalla.location
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.api.email}"
}

# Surface the URL so you can hand it to the API service env
output "valhalla_url" {
  value = google_cloud_run_v2_service.valhalla.uri
}
```

### Wire the API service to use it

```bash
# Step 1 — apply terraform to create valhalla-prod
terraform -chdir=infra/terraform apply

# Step 2 — capture the URL terraform just created
VALHALLA_URL="$(terraform -chdir=infra/terraform output -raw valhalla_url)"

# Step 3 — set the API service env vars and redeploy
gcloud run services update common-trails-api-prod \
  --region "${REGION}" \
  --update-env-vars "MAP_MATCHER_ENABLED=true,VALHALLA_URL=${VALHALLA_URL}"
```

After step 3, every new ingest hits Valhalla. Existing heat_edges keep their NULL `match_source` until you backfill (see § "Backfill" below).

### Verifying it's live

```bash
# 1. Tail API logs while uploading a single GPX. Expect:
#    "ingest: valhalla matched N edges (mean_conf=0.9X, sport=road)"
gcloud run services logs tail common-trails-api-prod --region "${REGION}"

# 2. Spot-check the DB after a few ingests
gcloud sql connect "${SQL_INSTANCE}" --user=postgres --database=common_trails
> SELECT match_source, COUNT(*)
  FROM heat_edges
  WHERE created_at > NOW() - INTERVAL '1 hour'
  GROUP BY 1;
# Expect: 'valhalla' >= 70%, NULL or 'spatial' < 30%
```

### Cold start expectations

- First ingest after idle: ~10 s extra (Valhalla container starts + tar extracts on the volume).
- Steady-state (within 15 min of last ingest): < 200 ms/match.
- Cold starts cost almost nothing (CPU billing is short-lived).
- If you want zero cold starts, set `min_instance_count = 1` in the terraform — adds ~$5/mo.

### Adding new regions

When friends start riding outside Occitanie + PACA:

1. Locally: edit `tile_files` in docker-compose to add the new PBF, force-rebuild Valhalla tiles (`docker compose stop valhalla && rm -rf data/valhalla_tiles && docker compose up -d valhalla`).
2. Re-tarball + upload to GCS, overwriting the previous `valhalla-tiles-fr.tar.gz`.
3. Force the Cloud Run revision to redeploy so it pulls the new tar:
   ```bash
   gcloud run services update common-trails-valhalla-prod \
       --region "${REGION}" \
       --update-env-vars=_=force-redeploy-$(date +%s)
   ```

### Disabling / rolling back

```bash
gcloud run services update common-trails-api-prod \
  --region "${REGION}" \
  --update-env-vars MAP_MATCHER_ENABLED=false
```

The legacy spatial matcher takes over on the next ingest. Existing Valhalla-matched heat_edges stay (rollback is forward-compatible).

### Backfill (re-match existing data)

After the initial deploy, only NEW ingests use Valhalla. Existing heat_edges keep `match_source = NULL` (treated as 'spatial'). To upgrade them:

```bash
# Dry-run first — estimates impact, doesn't write
docker compose exec backend python -m app.cli.rematch_heat_edges \
    --since all --dry-run

# Real run, schedule overnight
docker compose exec backend python -m app.cli.rematch_heat_edges --since all
```

(The `rematch_heat_edges` CLI is sketched but not implemented yet — see `docs/heatmap-map-matching-plan.md` § Step 7. Add when you actually need the backfill.)

### Don't do this

- **Don't run Valhalla on the same Cloud Run instance as the API.** It needs 1 GB RAM by itself; sharing with FastAPI + heatmap renderers will OOM during a tile burst.
- **Don't enable Valhalla in prod before verifying it locally.** A failing match returns `MatchUnavailable` → falls through to spatial matcher → looks fine but nothing's actually using Valhalla. Check the `match_source` distribution.
- **Don't increase `max_instance_count` past 1** during the soft-launch. 3 friends + 50 uploads/week never need parallel Valhalla. Bumping it costs idle minutes for no gain.
- **Don't skip the GCS-tar approach and let Cloud Run build tiles at startup.** That's a 5–10 min cold start; the tar approach is 30–60 s.

### Verify success criteria

After deploy + soak for one week:
- `heat_edges.match_source` distribution: `'valhalla' >= 70%`, NULL/`'spatial' < 30%`.
- Heatmap on a known city street (e.g. Rue de la Garenne, Montpellier): ONE smooth line per road, no parallel-cycle-path bundle.
- No new error type in logs from the API → Valhalla request path.
- Valhalla cold starts < 15 s p99.

---

## Heatmap artefact freshness — event-driven, by design

Four artefacts derive from `heat_edges` and have to stay in sync:
1. `heat_edges_display` matview
2. `heatmap-display.pmtiles` (the file the browser loads)
3. `*.fgraph` files (one per sport × partition)
4. live MVT path (no artefact — re-reads `heat_edges_display` per tile)

**Design principle**: rebuilds are triggered by *the end of an ingest job*, not by a clock. We don't run hourly cron over these — that wastes CPU when nothing changed and risks a rebuild interrupting a long ingest. Every rebuild is causally tied to fresh data.

### Local dev (already implemented in `app/services/ingest.py`)

After every successful `_update_heat_edges`, two debounced timers fire:

| Hook | Function | Delay | Coalesces |
|---|---|---|---|
| `_schedule_matview_refresh()` | `REFRESH MATERIALIZED VIEW CONCURRENTLY heat_edges_display` | 10 s | yes — within the window |
| `_schedule_pmtiles_rebuild()` | `build_pmtiles.main()` + write IN-PLACE to bind-mounted host file | 5 min | yes — within the window |

A friend uploading 50 GPX files in a session triggers **one** matview refresh (~10 s after the last upload) and **one** PMTiles rebuild (~5 min after the last upload). The 5-min PMTiles window is set to amortise the ~30 s build cost — set `PMTILES_REBUILD_DELAY_S` if your scale changes.

`.fgraph` is **not** auto-rebuilt in this loop. It needs the Rust `prebuild` binary which lives outside the backend container (architecture-specific Mach-O on Mac dev hosts). Two paths:

- **Dev**: manual `make fgraph` after big data events (Strava bulk import, OSM PBF refresh). `make heatmap-status` flags it with a ⚠️ when older than the PMTiles file.
- **Prod (when ready)**: a dedicated Cloud Run service `fgraph-builder-prod` (Rust + the prebuild binary baked into the image). The `artefact-rebuild` handler in api-prod sends an HTTP POST to it after PMTiles is built. Same `const task name = automatic dedup` pattern. Estimated ~$0.50/mo at 3-friend cadence.

For routing freshness in dev: tolerable lag — `heat_edges` popularity rankings don't shift much between ingests, so an `.fgraph` from yesterday is fine for today's routing.

Bulk-import bypass: `SKIP_MATVIEW_REFRESH=true` + `SKIP_PMTILES_REBUILD=true` env vars skip both timers during a loop. The bulk-import scripts (`make heat-edges`, `import_strava_export.py`, `audit_ingest_perf.py`) set them and call `make heatmap-deploy` once at the end.

### Prod (Cloud Run + Cloud Tasks — same pattern, different transport)

`threading.Timer` doesn't survive across Cloud Run cold starts (instance can die between requests). Replace it with **Cloud Tasks with a 5-min schedule**.

```
                 friend uploads GPX
                         │
                         ▼
            POST /gpx/upload  (api-prod)
                         │
                         ▼
       enqueue heat-compute task (existing path)
                         │
                         ▼
           POST /internal/ingest/heat
                         │
                  _update_heat_edges
                         │
                         ▼
   ┌─────────────────────┴────────────────────────┐
   │                                              │
   ▼                                              ▼
enqueue matview-refresh task                 enqueue artefact-rebuild task
  (queue: matview-refresh,                     (queue: artefact-rebuild,
   schedule: now + 10s,                         schedule: now + 5min,
   task_name = const "matview-refresh"          task_name = const "artefact-rebuild"
   → Cloud Tasks dedupes new                    → Cloud Tasks dedupes new
   enqueues onto the same task)                 enqueues onto the same task)
   │                                              │
   ▼                                              ▼
POST /internal/matview/refresh                POST /internal/artefacts/rebuild
   │                                              │
   ▼                                              ▼
 REFRESH MATERIALIZED VIEW                    build_pmtiles → upload to GCS
                                              build_fgraph → upload to GCS
```

**Key trick: const task name = automatic debouncing.** Cloud Tasks rejects new tasks with the name of an existing one. So 50 ingests in 5 min all try to enqueue `artefact-rebuild` → only the first one succeeds. When that task fires (5 min after the burst), it rebuilds once. Next ingest after that re-enqueues fresh.

Sketched terraform addition (paste-ready, on top of the Valhalla section above):

```hcl
resource "google_cloud_tasks_queue" "matview_refresh" {
  name     = "matview-refresh"
  location = var.gcp_region
  retry_config { max_attempts = 3 }
  rate_limits { max_concurrent_dispatches = 1 }
}

resource "google_cloud_tasks_queue" "artefact_rebuild" {
  name     = "artefact-rebuild"
  location = var.gcp_region
  retry_config { max_attempts = 3 }
  rate_limits { max_concurrent_dispatches = 1 }
}

resource "google_storage_bucket" "heatmap_artefacts" {
  name                        = "${var.gcp_project}-heatmap-artefacts"
  location                    = var.gcp_region
  uniform_bucket_level_access = true
  versioning { enabled = false }
}
```

Backend code additions (sketched — implement when you start prod):

- `app/services/cloud_tasks.py`: `enqueue_matview_refresh()`, `enqueue_artefact_rebuild()`. Both take **no payload** (the rebuild reads current DB state) and use a **constant task name** so duplicates collapse.
- `app/api/internal_ingest.py`: replace the local `_schedule_matview_refresh()` + `_schedule_pmtiles_rebuild()` calls with `enqueue_*` when `CLOUD_TASKS_ENABLED=true`.
- `app/api/internal_artefacts.py` (NEW): `POST /internal/matview/refresh` → calls `refresh_display_matview()`. `POST /internal/artefacts/rebuild` → builds PMTiles + .fgraph, uploads to GCS. Both protected by OIDC like `/internal/ingest/heat` already is.

Frontend:

- Heatmap layer reads `heatmap-display.pmtiles` from `gs://YOUR-PROJECT-heatmap-artefacts/` via `pmtiles://` URL with a CDN cache header `max-age=300`.
- For the very-latest-data layer, point `community-trails-line-live` (NEW) at `/heatmap/tiles/{z}/{x}/{y}.mvt` (matview-backed). Friends see their own ride within 40 s.

### Why no cron

A cron-based rebuild ("every hour, rebuild PMTiles regardless of whether anything changed") was considered and rejected:

- Wastes CPU/cost when nothing changed (most hours).
- Can race with an in-flight ingest (cron starts mid-`_update_heat_edges` → matview refresh runs against partial data).
- Decouples cause from effect — operator can't tell why the rebuild ran.
- Doesn't compose with `MAP_MATCHER_ENABLED` toggles (cron rebuilds regardless of feature flag state).

The only place cron makes sense: a daily safety-net `make heatmap-deploy` at 4 AM as backup if the event-driven path silently failed for a day. Optional, not in the soft-launch scope.

### When to manually run `make heatmap-deploy`

Even with the auto path, run the full deploy explicitly when:

- After `make heat-edges` (manual rebuild from `activities`) — bulk imports skip the auto-trigger to avoid 1000-rebuild thrash.
- After `alembic upgrade` if migration adds/changes anything matview-related.
- After OSM PBF re-import — both the matview LATERAL JOIN and the .fgraph rebuilds need to pick up the new `osm_road_edges`.
- When `make heatmap-status` shows mtimes older than the last activity ingest.

### Verifying the auto path is live in prod

```bash
# 1. Tail API logs while a single GPX is uploaded.
gcloud run services logs tail common-trails-api-prod --region "${REGION}"
# Expect: "heat compute done", then 10s later "matview-refresh enqueued",
#         then 5min after the LAST upload "artefact-rebuild enqueued".

# 2. Confirm Cloud Tasks queues drained correctly.
gcloud tasks queues describe matview-refresh --location "${REGION}"
gcloud tasks queues describe artefact-rebuild --location "${REGION}"
# Both should show stats.tasksCount=0 between uploads.

# 3. Check the GCS bucket is being written.
gsutil ls -l gs://${PROJECT}-heatmap-artefacts/heatmap-display.pmtiles
# Last-Modified should be within 5 min of the last upload-burst end.
```

---

## `heat_edges_display` matview

### What it is

A pre-joined materialized view of `heat_edges` × `osm_road_edges`: every heat_edge that matched an OSM way gets the full multi-point road curve in its `geometry` column; everything else falls back to the raw 2-point segment. This is what the live MVT endpoint (`/heatmap/tiles/{sport}/{z}/{x}/{y}.mvt`) reads from. Without it, the MVT path falls through to a slower `heat_edges` LATERAL-join query (50–200 ms/tile vs 15–50 ms with the matview) and the API container CPU climbs noticeably under panning.

### Why it has to be rebuilt explicitly

`alembic upgrade head` creates the matview as an **empty placeholder** (`WHERE false`). The live data is populated by `python -m app.jobs.refresh_matview`, which:

1. `DROP MATERIALIZED VIEW IF EXISTS heat_edges_display`
2. `CREATE MATERIALIZED VIEW heat_edges_display AS …` with the LATERAL join
3. Re-creates the unique + GIST + btree indexes
4. Resets the in-process matview-exists cache flag
5. Pre-warms the tile cache (z8–z12) to disk + memory

In dev with ~5 M heat_edges + 11 GB osm_road_edges this takes 5–15 min. The bottleneck is the LATERAL join, not the index builds.

**Auto-refresh after ingest** uses `REFRESH MATERIALIZED VIEW CONCURRENTLY heat_edges_display` — fast (~30–60 s on dev) but only valid once the matview has been populated at least once.

### When to refresh

| Trigger | Refresh needed | How |
|---|---|---|
| First-time setup (or after `alembic downgrade`) | Yes — full build | `python -m app.jobs.refresh_matview` |
| Single activity ingest | Auto, debounced 10 s | `_schedule_matview_refresh()` |
| Bulk Strava resync / `rebuild_heatmap` | Yes, after import finishes | `python -m app.jobs.refresh_matview` |
| Orphan contributor cleanup | Yes — user_count moves | `python -m app.jobs.refresh_matview` (or `REFRESH CONCURRENTLY` if already populated) |
| Migration 0037 (drop heat_cells) | No — matview reads from heat_edges only | n/a |

### Quick health check

```sql
-- Is it populated?
SELECT COUNT(*) FROM heat_edges_display;
-- 0 means placeholder; you need to refresh.

-- Is auto-refresh wedged behind a stuck job?
SELECT pid, age(now(), query_start) AS dur
FROM pg_stat_activity
WHERE query LIKE '%REFRESH MATERIALIZED%' AND state = 'active';
```

A defensive guard in `refresh_display_matview()` (PR #210) skips its own refresh if it sees one already running, so pile-ups only happen when the orchestrator (audit script, Cloud Run job) is killed mid-flight. See § matview pile-ups below for recovery.

---

## PMTiles rebuild

The frontend serves `frontend/public/heatmap-display.pmtiles` for the heatmap layer. After any of:

- Re-ingest of activities (Strava bulk import, resync, or manual upload of many files)
- Cleanup of orphan `heat_edge_contributors` rows
- Migration 0037 once activated

…the PMTiles file is stale. Rebuild it:

```bash
docker compose exec backend python -m app.jobs.build_pmtiles --output-dir /tmp
docker compose cp backend:/tmp/heatmap-display.pmtiles \
    frontend/public/heatmap-display.pmtiles
# In dev, the bind-mount picks it up immediately.
# In prod, upload to GCS:
gsutil cp frontend/public/heatmap-display.pmtiles gs://YOUR_BUCKET/
```

### Tippecanoe options that matter

The `build_pmtiles.py` job uses these (May 2026):

- `--maximum-tile-bytes=500000` — keeps tiles small, fast pan
- `--simplification=10 --simplify-only-low-zooms` — strong simplification at z6–z11, raw geometry at z14
- The export query filters `osm_way_id IS NULL AND ST_Length > 60m` — drops grid-fallback "spaghetti" edges from cold ingests

A typical rebuild on the dev DB takes ~4 min and produces a ~55 MB file. Prod will scale roughly with `heat_edges` row count.

---

## Orphan contributor cleanup

If a bulk-ingest job is killed mid-flight (or an audit user in `audit_ingest_perf.py` doesn't get cleaned up), `heat_edge_contributors` can accumulate rows whose `user_id_hash` no longer maps to any `users` row. These inflate the heatmap, bloat the matview, and produce phantom contributors in the popup.

**Detection**:

```sql
SELECT COUNT(*) AS orphans FROM heat_edge_contributors
WHERE user_id_hash NOT IN (
  SELECT ('x' || substring(encode(sha256(id::text::bytea), 'hex'), 1, 8))::bit(32)::int::bigint
  & x'7fffffff'::bigint
  FROM users
);
```

If this is > 0 you have orphans. **Cleanup procedure** (chunked, recoverable, no transaction wrapping the whole thing — autovacuum + matview refresh have to be paused):

```sql
-- 1. Pause the matview refresh debounce (set the env var first if not already)
-- 2. Chunked delete
DO $$
DECLARE v_deleted INT; v_total INT := 0;
BEGIN
  LOOP
    DELETE FROM heat_edge_contributors WHERE ctid IN (
      SELECT ctid FROM heat_edge_contributors
      WHERE user_id_hash NOT IN (
        SELECT ('x' || substring(encode(sha256(id::text::bytea), 'hex'), 1, 8))::bit(32)::int::bigint
        & x'7fffffff'::bigint FROM users
      )
      LIMIT 500000
    );
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    v_total := v_total + v_deleted;
    EXIT WHEN v_deleted = 0;
    RAISE NOTICE 'Deleted %', v_total;
  END LOOP;
END $$;

-- 3. Recompute user_count on affected edges + drop empty ones
UPDATE heat_edges SET user_count = COALESCE(sub.cnt, 0)
FROM (SELECT edge_key, COUNT(*) AS cnt FROM heat_edge_contributors GROUP BY edge_key) sub
WHERE heat_edges.edge_key = sub.edge_key;
UPDATE heat_edges SET user_count = 0 WHERE edge_key NOT IN (SELECT edge_key FROM heat_edge_contributors);
DELETE FROM heat_edges WHERE user_count = 0;

-- 4. Force a single matview refresh (or wait for the debounce)
REFRESH MATERIALIZED VIEW CONCURRENTLY heat_edges_display;
```

### Watch out for matview pile-ups

`REFRESH MATERIALIZED VIEW CONCURRENTLY` serializes — only one at a time. If a bulk-ingest job is killed, its scheduled refresh may be queued behind it, and the next ingest schedules another one, etc. Result: a queue of stuck refreshes that takes forever to drain.

Detect:

```sql
SELECT pid, age(now(), query_start), wait_event, LEFT(query, 50)
FROM pg_stat_activity
WHERE query LIKE '%REFRESH MATERIALIZED%' AND state = 'active';
```

Clear:

```sql
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE query LIKE '%REFRESH MATERIALIZED%' AND state = 'active';
-- Then run ONE refresh manually.
```

The defensive `refresh_display_matview()` helper (PR #210) skips its own refresh if it sees one already running, so this only piles up when the orchestrator (Cloud Run job, dev script) is killed mid-flight.

---

## Health checks after deploy

| What to watch | Where | Healthy looks like |
|---|---|---|
| Backend `/healthz` | Cloud Run revision dashboard | 200 within 30 s of cold start |
| `/heatmap/summary` total_edges | Direct curl | Grows monotonically with ingests |
| `pg_stat_activity` long-running queries | psql / Cloud SQL Insights | None > 5 min unless during an active rebuild |
| Cloud Tasks queue depth | `gcloud tasks queues describe heat-compute` | Should drain to 0 between bursts |
| `heat_edge_contributors` orphan count | SQL above | 0 (anything > 0 means a job died mid-flight) |
| PMTiles file age (`Last-Modified`) | `curl -I /heatmap-display.pmtiles` | Updated after every rebuild script run |

If any of these go sideways, the rollback is usually:
- For schema: `alembic downgrade <revision>`
- For Cloud Tasks: `TF_VAR_api_public_url=""` + apply
- For data drift: the orphan cleanup procedure above

---

## Lessons from the dev rollout (May 2026)

These bit us during the dev work; surfacing them so the prod operator doesn't repeat them:

1. **Audit users left orphan contributors**. `audit_ingest_perf.py --keep-data` followed by manual `DELETE FROM users` left ~2.7 M `heat_edge_contributors` rows whose `user_id_hash` no longer mapped to a real user. Visible as inflated `heatmap_summary.total_contributors`. Always run the cleanup-by-bbox helper or the orphan-cleanup SQL above when removing users.

2. **Killed bulk imports leave matview refreshes queued**. Ctrl-C on the audit script (or `docker compose kill`) doesn't propagate to the SQL-side `REFRESH MATERIALIZED VIEW CONCURRENTLY` — it keeps running. The next ingest queues another. We saw 18 stuck refreshes accumulate over a session, eventually requiring `pg_terminate_backend` on each.

3. **Bind-mount `./backend:/app` shares state across worktrees**. When parallel agents ran tests in their isolated worktrees, all four hit the same backend container and stepped on each other's DB sessions. For prod that's not a problem (each container is its own deploy), but for parallel dev work use a separate compose project per worktree.

4. **`bind-mount` PMTiles file must exist before `docker compose up`**. The `frontend` service in `docker-compose.yml` mounts `./frontend/public/heatmap-display.pmtiles` as read-only. On a fresh clone there's no file → mount fails. Either build PMTiles first or comment out the mount, bring up, build, restore the mount.

5. **`OSM_GRID_CACHE_MAX=200` is too aggressive for db-f1-micro**. Each cached tile-set is ~5 MB; 200 sets ≈ 1 GB of backend container RAM. Set `OSM_GRID_CACHE_MAX=50` in prod (already in `infra/terraform/main.tf` post-PR-#210). Bump only when you upsize Cloud SQL.

---

## Soft-launch playbook — 3 friends on prod, ~$10/mo

**Context** (read this first, fresh-session Claude). Paul destroyed the prod DB to save money. We're rebuilding from scratch and inviting 2–3 friends next week. Motto: *"my friends love it, and I don't pay much for the platform."* Target cost ceiling: **~$10/mo** (db-f1-micro + Cloud Run idle + GCS + Cloud Tasks free tier).

This section is self-contained: you don't need to read the rest of the runbook to execute it. Each step has a verification command. If a step's verification fails, stop and ask Paul before mutating further — `terraform apply` and OSM imports are not free to redo.

### Phase 0 — preflight (5 min)

Goal: make sure the local repo + tools are in shape before touching prod.

```bash
# 1. On main, clean working tree
git checkout main && git pull
git status     # must be clean

# 2. terraform binary present
terraform version    # >= 1.5

# 3. gcloud auth
gcloud auth list                               # Paul's account active
gcloud config get-value project                 # confirm prod project ID
```

If any of these fail, stop and ask Paul (auth tokens, GCP project IDs etc. are state he owns).

### Phase 1 — rebuild infra (1 h)

```bash
cd infra/terraform

# Step 1: create everything except the URL-self-reference
TF_VAR_api_public_url="" terraform apply

# Step 2: capture the freshly-created Cloud Run URL and re-apply so
# INTERNAL_HEAT_HANDLER_URL gets set inside the api container
TF_VAR_api_public_url="$(terraform output -raw api_url)" terraform apply
```

**Verify:**
```bash
# Cloud Run service exists and serves /healthz
curl "$(terraform output -raw api_url)/healthz"
# Cloud Tasks queue exists
gcloud tasks queues describe heat-compute --location europe-west1
# Cloud SQL instance is db-f1-micro (cost ceiling check)
gcloud sql instances list   # tier = db-f1-micro
```

If `tier` is anything other than `db-f1-micro`, **stop** — that's a $50+/mo difference. Confirm with Paul before proceeding.

### Phase 2 — apply migrations (15 min)

The Cloud Run image runs `alembic upgrade` on container start, so this should be automatic. Verify:

```bash
gcloud run services logs tail common-trails-api-prod --region europe-west1 \
  | grep alembic
# Should see "0001 -> 0002 -> ... -> 0037" runs and exit cleanly
```

If the image hung on a migration, it's almost certainly 0036 (PostGIS column add) — a fresh DB has 0 rows so no backfill is needed and the `CREATE INDEX` is instant. If the image is still hung after 5 min, dump the alembic state:

```bash
gcloud sql connect $(gcloud sql instances list --format="value(name)") --user=postgres
> SELECT version_num FROM alembic_version;
```

### Phase 3 — bootstrap data (3 h, mostly waiting)

Without these, the heatmap and routing graph are empty and friends will see a blank map.

```bash
# A. OSM PBF import — only the regions friends ride.
#    Adjust the list below before running. See docs/ops-osm-pbf-import.md
#    for the per-region wall-clock budget. db-f1-micro can do one region
#    at a time; running two in parallel will swap and crawl.
gcloud run jobs execute import-osm-pbf-occitanie --region europe-west1 --wait
gcloud run jobs execute import-osm-pbf-paca      --region europe-west1 --wait

# B. .fgraph routing graphs (offroad/gravel/mtb). These live in GCS, not Cloud SQL.
gcloud run jobs execute build-fgraphs --region europe-west1 --wait

# C. heat_edges_display matview — first build (5–15 min)
gcloud run jobs execute refresh-matview --region europe-west1 --wait
```

(If any of those Cloud Run Jobs aren't terraformed, run them by `gcloud run jobs deploy --image=…` then `execute`. The image is the same `common-trails-api-prod`; only the entrypoint differs.)

**Verify:**
```bash
psql … <<'SQL'
SELECT (SELECT COUNT(*) FROM osm_road_edges) AS osm,
       (SELECT COUNT(*) FROM heat_edges)     AS heat,
       (SELECT COUNT(*) FROM heat_edges_display) AS display;
SQL
# Expect:  osm  ≈ 1–5 M (depends on region),  heat = 0,  display = 0
# That's correct — heat_edges populates as friends upload.

gsutil ls gs://YOUR-FGRAPH-BUCKET/  # offroad-*.fgraph + gravel-*.fgraph + mtb-*.fgraph present
```

### Phase 4 — set the K-anonymity dial (30 s)

K=2 in prod hides everything until 2+ users overlap on the same edge. With 3 friends, the heatmap will look empty for ~24 h while activities fan out. **For the soft launch:** run with K=1 and put a banner in the UI.

```bash
gcloud run services update common-trails-api-prod \
  --region europe-west1 \
  --update-env-vars HEATMAP_K_ANONYMITY=1
```

**Bump back to K=2 once you have ~10 users.** A reminder TODO entry in your tracker is a fine commit-and-forget.

### Phase 5 — Strava OAuth callback URL

The Strava developer console must point at the new prod domain. If you let `*.run.app` be the public URL:

```
https://common-trails-api-prod-xxxxx-ew.a.run.app/auth/strava/callback
```

Update at <https://www.strava.com/settings/api>. Without this, the "Connect Strava" button on the new prod will 4xx.

### Phase 6 — onboarding rehearsal (45 min)

Before sending the first invite, walk yourself through the flow on **iPhone Safari + desktop**:

1. **Sign up** with a fresh email at `https://<prod>/signup` → expect 201 + JWT cookie.
2. **Connect Strava** → expect redirect to Strava → consent → redirect back → "Strava connected".
3. **Trigger sync** at `/me/strava/sync` (or via UI button). Watch:
   ```bash
   gcloud tasks queues describe heat-compute --location europe-west1 \
     --format="value(stats.tasksCount)"
   ```
   The count should rise then drain to zero.
4. **Heatmap appears** within 5 min of the queue draining (matview auto-refreshes 10 s after the last ingest).
5. **Route editor** — click two points 50 km apart in a region you imported. Expect 3 proposals in <1 s. The motto-test target is "100 km in 50 ms"; we measured 77 ms locally for 75 km offroad on 2026-05-03.

If any step hangs >30 s without progress, capture logs (`gcloud run services logs tail …`) and **stop**. Don't invite friends until the rehearsal is clean.

### Phase 7 — invite, one at a time

Send invites **sequentially**, not in a batch. The first friend always lands the deepest issues; fix what they hit before the second friend signs up. Suggested cadence: friend 1 day 1, friend 2 day 3, friend 3 day 5.

A short French invite text — copy into your messaging app of choice:

> Salut, j'ai un truc à te montrer 🚴
>
> [URL] — créer un compte, connecter Strava, attendre 5 min, ouvrir la carte.
>
> Le but: heatmap perso + propositions d'itinéraires basées sur tes traces.
> Tes données sont privées, le heatmap est anonymisé. C'est de l'alpha — ton retour brut m'intéresse.

### Phase 8 — watch costs for the first week

```bash
# Daily — just to be sure no runaway query is racking up Cloud SQL CPU
gcloud sql operations list --instance=$(gcloud sql instances list --format='value(name)') \
  --filter="status=RUNNING" --limit=5

# Cost estimate up to today
gcloud billing accounts get-iam-policy …  # or open the GCP console
```

Targets: < $0.50/day. If anything is > $1/day it's worth a look.

### Phase 9 — what NOT to do during the soft launch

- **Don't run a full `rebuild_heatmap`** while friends are using it — db-f1-micro will choke and uploads will time out.
- **Don't bump `OSM_GRID_CACHE_MAX` from 50** — 200 ≈ 1 GB RAM and Cloud Run will OOM-kill the container.
- **Don't enable migration 0037** (drop heat_cells) until you've soaked for one full week — see § Migration 0037 above. The placeholder body is fine for now.
- **Don't push to main without the matview rebuild** afterwards if your change touches the heatmap query — see § matview.

### Done state

You'll know the soft launch is healthy when, after a week:
- 3 friends have at least 1 activity each
- `/healthz` p99 < 1 s (with cold-start spikes acceptable)
- `heat_edge_contributors` rowcount > 0 and `orphans` = 0
- Cloud SQL CPU < 30 % p99
- Total monthly bill projected < $15

If all of those hold, ramp K=1 → K=2 and consider invite #4.
