# Prod elevation + surface rebuild runbook

> ⚠️ **SUPERSEDED for DEPLOYS.** Prod is deployed **ONLY** via
> `scripts/deploy-prod.sh` (see **`docs/prod-deploy.md`**). **Terraform is
> frozen** and the **`deploy-gcp` workflow is disabled** — any `terraform apply`
> or push-to-main-to-deploy step referenced below is **FORBIDDEN**. The rebuild
> mechanics (DEM/surface backfill, job invocations) remain useful for reference.

After PR #259 (D+ smoothing + local DEM at PBF import + `surface_confidence`
end-to-end) and PR #261 (HGT tiles baked into the API image + activities
backfill CLI), prod needs a one-shot rebuild so the new columns are
populated with real values.

This runbook is the **mechanical, command-by-command** version of that
rebuild. Each step is idempotent unless flagged otherwise.

## Pre-flight

```bash
# 1. Confirm the GCP account is the personal one — never the okeiro pro account.
gcloud config get-value account     # → paleclercq@gmail.com
gcloud config get-value project     # → common-trails

# 2. Confirm the new image is deployed to the OSM import Cloud Run Job.
#    The image tag should include the commit hash from after #261 merge.
gcloud run jobs describe common-trails-import-osm-prod \
    --region=europe-west1 --format='value(template.template.containers[0].image)'

# 3. Confirm DEM_DIR is set on the job.
gcloud run jobs describe common-trails-import-osm-prod \
    --region=europe-west1 --format='value(template.template.containers[0].env)' | \
    grep DEM_DIR

# 4. Confirm migrations 0041 + 0042 already landed on prod.
#    (They were applied during the PR #259/#261 rollout — verify.)
make prod-alembic-current 2>/dev/null || \
    echo "→ run alembic upgrade head via cloud-sql-proxy if not present"
```

Cloud SQL tier should currently be `db-custom-2-7680` (the 2026-05-14
bump). Verify and only bump if it isn't:

```bash
gcloud sql instances describe common-trails-prod --format='value(settings.tier)'
# expected: db-custom-2-7680 — if db-f1-micro, bump first:
# gcloud sql instances patch common-trails-prod --tier=db-custom-2-7680
```

### ⚠ Pre-flight memory bump (REQUIRED if importing PACA / Rhône-Alpes / whole-France)

The Cloud Run Job's default is **2 GiB / 2 vCPU**, set in terraform
([`infra/terraform/main.tf` `google_cloud_run_v2_job.import_osm`](../infra/terraform/main.tf)).
This is fine for Occitanie + Auvergne but **OOMs on PACA / Rhône-Alpes**
(pyosmium's node-locations index for a region with denser highway
coverage exceeds 2 GiB). Whole-France needs even more.

**Bump BEFORE running an at-risk region** — running first and waiting
for the SIGKILL costs ~30 min of wasted work:

```bash
gcloud run jobs update common-trails-import-osm-prod \
    --region=europe-west1 \
    --memory=8Gi --cpu=4
```

After the rebuild settles you can downgrade memory back to 2 GiB if you
only re-import the smaller regions in the future. Or leave it at 8 GiB —
Cloud Run only bills for the actual VM time, not the reservation.

## The rebuild — 6 commands

```bash
# 1. Re-import OSM PBFs (4 southern regions). DEM lookup runs per
#    segment — end-of-parse log shows ``DEM hits N/M = X%``. Total ~1-2 h.
make prod-import-osm-job-south

# 2. Link freshly-imported OSM rows to the existing heat_edges via
#    osm_way_id. group_edges_osm now also copies ele_delta_m, slope_grade,
#    and surface_confidence from osm_road_edges → heat_edges with COALESCE
#    (won't overwrite prior high-quality values). ~5-30 min.
make prod-group-edges-osm

# 3. Re-apply smoothed D+ to existing activities.elevation_gain_m. Reads
#    z from stored geometry_geojson, writes only elevation_gain_m. Crouzet-
#    safe. Preview first with DRY_RUN=1.
make prod-recompute-elevation-gain DRY_RUN=1
make prod-recompute-elevation-gain

# 4. Refresh the heat_edges materialized view + force a PMTiles rebuild
#    so the new ele/conf signal lands in the served tiles.
gcloud run jobs execute common-trails-rebuild-heatmap-prod --region=europe-west1 --wait

# 5. Roll the Cloud Run backend so all instances re-read _edge_version
#    (module-local counter; without a roll, instances serve stale routes).
gcloud run services update common-trails-backend-prod --region=europe-west1 \
    --update-env-vars=REBUILD_TOKEN=$(date +%s)

# 6. (Optional) Downgrade the Cloud SQL tier once the rebuild settles. Wait
#    24 h before downgrading so any post-rebuild query plans warm up
#    against the bigger tier.
# gcloud sql instances patch common-trails-prod --tier=db-f1-micro
```

## Verification (after each step)

```bash
# After step 1 — osm_road_edges should have non-null ele_* and surface_confidence
psql -h 127.0.0.1 -p 5433 -U api -d common_trails -c "
SELECT
  ROUND(100.0 * COUNT(*) FILTER (WHERE ele_delta_m IS NOT NULL) / COUNT(*), 1) AS pct_with_ele,
  ROUND(100.0 * COUNT(*) FILTER (WHERE surface_confidence IS NOT NULL) / COUNT(*), 1) AS pct_with_conf,
  ROUND(AVG(surface_confidence)::numeric, 2) AS avg_conf
FROM osm_road_edges;"
# expect: pct_with_ele >= 95%, pct_with_conf = 100%, avg_conf 0.5-0.8

# After step 2 — heat_edges should pick up the new signal
psql ... -c "
SELECT
  ROUND(100.0 * COUNT(*) FILTER (WHERE ele_delta_m IS NOT NULL AND ele_delta_m != 0) / COUNT(*), 1) AS pct_with_ele,
  ROUND(100.0 * COUNT(*) FILTER (WHERE surface_confidence IS NOT NULL) / COUNT(*), 1) AS pct_with_conf
FROM heat_edges;"
# expect: pct_with_ele jumps significantly vs pre-rebuild

# After step 3 — recompute log shows updated counts
# Spot-check one activity: the old (unsmoothed) D+ should be > new (smoothed)
psql ... -c "
SELECT id, elevation_gain_m FROM activities
WHERE updated_at > now() - interval '1 hour'
ORDER BY updated_at DESC LIMIT 5;"

# After step 4 — PMTiles rebuilt
gsutil ls -l gs://common-trails-data-prod/pmtiles/ | head

# After step 5 — Backend healthy on new revision
gcloud run services describe common-trails-backend-prod --region=europe-west1 \
    --format='value(status.traffic[0].revisionName)'
```

## Heat-quality alert check (post-rebuild)

The monitored regions are configured in `app/services/heat_quality.py:MONITORED_REGIONS`
(Montpellier z17, Anduze corridor, Lyon, Marseille). The thresholds:

- `grid_fallback_ratio > 0.20` → WARN
- `isolated_ratio > 0.05` → WARN

Run a baseline measurement:

```bash
gcloud run jobs execute common-trails-heat-quality-prod --region=europe-west1 --wait
# or via cloud-sql-proxy:
docker compose exec backend python -m app.cli.heat_quality
```

The post-rebuild numbers should be **better than or equal to** pre-rebuild.
If `grid_fallback_ratio` regressed, investigate `group_edges_osm` output —
likely a tile_key mismatch between the new OSM run and the old heat_edges.

## Rollback

Migrations 0041 + 0042 are pure `ADD COLUMN NULL` — safe. The data
populated by the rebuild can't be cleanly "rolled back" without a fresh
import, but the rebuild itself is idempotent: re-running each step
produces the same result.

If anything goes badly wrong:

1. **Backend instances serving stale data** — kill the new revision and
   roll back to the previous: `gcloud run services update-traffic
   common-trails-backend-prod --to-revisions=<PREV>=100`.
2. **PMTiles look wrong** — re-run step 4. Idempotent.
3. **`elevation_gain_m` looks wrong** — the smoothed values *should* be
   lower than the inflated naive sums. If a particular activity shows
   `elevation_gain_m=0` despite having 3D coords, check the
   `_smoothed_elevation_gain` log warnings during step 3.

## Time / cost budget

| Step | Time | $ |
|---|---|---|
| 1. Re-import 4 regions | 1-2 h | $0.20 |
| 2. group_edges_osm | 5-30 min | $0 |
| 3. recompute_elevation_gain | 5-15 min (~1400 activities) | $0 |
| 4. rebuild_heatmap | 10-30 min | $0.10 |
| 5. Roll backend | <1 min | $0 |
| 6. Tier downgrade | 5-10 min | saves $1/day |

**Total: ~2-3 h, ~$0.30 in extra compute. Cloud SQL tier-bump cost
stops when you downgrade (step 6).**

## What to tell friends after this lands

The user-facing changes from the rebuild:

- Elevation profile on every activity is now Garmin-quality (no more
  phantom +200-400 m D+ from baro noise on flat rides).
- Heatmap-derived routes carry real slope info, so the cost model gives
  better-graded routes for gravel/MTB profiles.
- Surface overlay opacity now fades by confidence rather than binary-
  dropping at `data_quality=poor` (front-end work to flip the switch is
  optional; the data is there either way).

## Lessons learned — 2026-05-15 rebuild

These bit us during the first end-to-end run and now appear as
explicit caveats above. Future-you, read these before kicking off:

### 1. Memory: bump BEFORE running PACA / Rhône-Alpes / france

ara (Rhône-Alpes) silently OOMed at 2 GiB and the job exited with
`Container terminated on signal 9` — Cloud Run reports this as
`status.conditions[0].status=False` but doesn't surface the OOM
reason in the dashboard. Diagnosed via `gcloud logging read … severity>=WARNING`.

**Fix:** bump to 8 GiB / 4 vCPU before any region whose PBF >300 MB.
See the "⚠ Pre-flight memory bump" section above. The Occitanie
smoke test (~150 MB PBF) is safe at 2 GiB and confirms DEM coverage
without burning a big run on a misconfigured spec.

### 2. Never use `gcloud run jobs update --image=…` alone — pass `--command` too

The terraform spec was:
```
command = ["python", "-m", "app.cli.import_osm_roads"]
args    = ["occitanie"]
```

Running `gcloud run jobs update --image=<new>` to deploy a fresh
image *collapsed* this into `command=["python"]` +
`args=["-m", "app.cli.import_osm_roads", "occitanie"]`. The
container ran fine, but the next `gcloud run jobs execute --args=paca`
overwrote args entirely → effective command `python paca` →
`can't open file '/app/paca'` and exit 2.

**Fix at execute time** (works without re-updating the job):
```bash
gcloud run jobs execute common-trails-import-osm-prod \
    --region=europe-west1 \
    --args=-m,app.cli.import_osm_roads,<region> --wait
```

**Permanent fix** — re-apply terraform once the deploy SA has IAM
permissions to read `compute.backendBuckets` and `cloudtasks.queues`
(see lesson 3). Until then, document the workaround in any new
ad-hoc invocation.

### 3. Deploy workflow IAM gap — Terraform half-deploys silently

`.github/workflows/deploy-gcp.yml` builds + pushes the image, then
updates Strava / edge-clustering / rebuild_heatmap jobs explicitly,
*then* runs `terraform apply` to align everything else. On 2026-05-15
the terraform step failed with:
```
Error 403: Required 'compute.backendBuckets.get' permission for …
Error 403: …'cloudtasks.queues.get' permission for …
```

The deploy reported "completed/failure" overall, but the API service
+ 3 explicit jobs had already deployed. The OSM import job — relied
on terraform — stayed on the *old* image and *old* env. We caught it
manually by inspecting `gcloud run jobs describe …`.

**Fix (queued for the post-beta cleanup PR):** grant the deploy SA
`roles/compute.viewer` + `roles/cloudtasks.viewer` and re-apply.
Until then, after every deploy: verify each Cloud Run Job's
`spec.template.spec.containers[0].image` matches the latest main SHA.

### 4. Parallel imports + db-custom tier: 1.5× throughput, not 3×

3 parallel jobs on db-custom-2-7680 (2 vCPU / 7.5 GiB):

- Occitanie solo: 3420 segments/sec (38 min for 7.8M)
- 3 concurrent: ~1500 segments/sec each → ara 105 min, paca 66 min,
  auvergne 34 min
- Net win: ~1.5× vs strict sequential (Occitanie's 38 min × 3 = 114
  min sequential vs 105 min for the parallel batch)

**Conclusion:** parallel is still worth it but don't expect 3×.
Don't try to run 4-5 concurrent — DB CPU saturates and you'll see
serialised COPY waits.

### 5. Don't mix orchestration strategies mid-flight

The original sequential `for region in paca ara auvergne; do gcloud …; done`
shell loop didn't get killed when I switched to "fire ara + auvergne in
parallel". The loop kept advancing — when paca finished, it fired its
own ara execution (`nmds6`), creating a duplicate concurrent with the
manually-fired `cgdqx`. Both processed the same Rhône-Alpes PBF,
double-DELETEd then double-INSERTed every tile — wasted DB CPU and
~30 min.

**Fix:** before changing strategy, `kill -TERM <pid>` the local
orchestrator. Then check for orphaned executions with
`gcloud run jobs executions list … status=Unknown` and
`gcloud run jobs executions cancel <name>` any duplicates.

### 6. Cloud Run "Unknown" status = "in progress", not "failed"

`status.conditions[0].status` reads:
- `True` = succeeded
- `False` = failed
- `Unknown` = still running

The dashboard shows "Running" + spinning icon for `Unknown`, but the
CLI default output is ambiguous. Always check `--format='value(status.conditions[0].status)'`
when scripting.

### 7. Coverage count is cumulative, not per-run

The `Coverage: X z14 tiles total in DB` line at end-of-parse is the
total across all imports, not the count this run added. The
"unique tiles" earlier in the log is the per-run count.

Auvergne reported `Coverage: 44,600` after a 9,716-tile parse → the
DB had ~35,000 tiles from earlier imports already.
