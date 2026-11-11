# Prod deploy doctrine

Prod (chemins-communs.fr) runs on Cloud Run + a fresh db-f1-micro Cloud SQL
(`common-trails-prod`, reactivated 2026-07-10/11). This is the operational
contract for deploying it. **The rules below are load-bearing — each one
encodes an incident that already happened.**

## 1. Deploy ONLY via `scripts/deploy-prod.sh` — never hand-typed `gcloud`

**Normal path: merge to `main`.** `.github/workflows/deploy-gcp.yml` runs on
every push to `main` (and on manual `workflow_dispatch`): it builds the
frontend static export into the backend image, pushes it, then runs
`DEPLOY_YES=1 scripts/deploy-prod.sh <sha>`. See [`ci.md`](ci.md). Run the
script by hand only for an out-of-band redeploy:

```bash
scripts/deploy-prod.sh <IMAGE_TAG>               # tag already built+pushed to Artifact Registry
```

**Why.** `gcloud run deploy --set-env-vars=…` *replaces the entire env set*.
Twice, a hand-typed deploy listed only a subset and silently dropped the
Cloud-Tasks wiring (`CLOUD_TASKS_*_QUEUE`, `INTERNAL_*_HANDLER_URL`), so a
background path went **off** with no error. The
script is the **single source of truth**: it applies the *complete* env set
declaratively (`--env-vars-file` + `--set-secrets`, both authoritative) so a
var can never persist-stale or drop. It prints an env-key **diff vs the live
service** and waits for confirmation before applying — an unexpected `+ adds`
line for the Cloud Tasks keys is precisely the drift being repaired.

The env set in `deploy-prod.sh` is the env set of the **live-good revision**
(rev 00395), captured 1:1 — NOT terraform's superset. `infra/terraform/main.tf`
is a useful *design* reference, but the working revision is the SSOT: adding a
var terraform declares but live doesn't carry is itself drift (e.g. the matview
queue dropped in migration 0055, the export queue, `SKIP_OSM_FETCH`; Overpass is
off in prod via `OVERPASS_ENABLED=false` alone). `SENTRY_DSN` (secret) and
`DB_POOL_SIZE`/`DB_MAX_OVERFLOW` have since been added deliberately. Host
subtleties the script encodes: the heatmap display gate is
`HEATMAP_DISPLAY_SOURCE=raw` + `HEATMAP_MIN_USERS=1` (distinct-user floor, K=1
during the beta) + `TRACE_MASK_METERS=200` (`HEATMAP_K_ANONYMITY=1` only feeds
legacy matched-era paths), and the `INTERNAL_*_HANDLER_URL` vars point at
a **`.run.app`** origin (their OIDC audience is bound to it), while the
user-facing `FRONTEND_URL` uses `chemins-communs.fr`.
Since the microservice split (§8) the HEAVY handler URLs (heat / artefact)
point at the **worker** `.run.app` — `deploy-prod.sh` derives it from the
project number.

Re-verify + update the block whenever prod's env legitimately changes:

```bash
gcloud run services describe common-trails-api-prod --region=europe-west1 \
  --format=json | python3 -c "import sys,json;d=json.load(sys.stdin);\
[print(e['name'],'=',e.get('value','[secret]')) for e in \
d['spec']['template']['spec']['containers'][0]['env']]"
```

## 2. Every Cloud Run Job is synced to the SAME image as the service

`deploy-prod.sh` updates the image of **all 3** live jobs (`JOBS=(…)` —
build-pmtiles, ingest-pending-archives, resync-reminder) and asserts their memory, env and
secrets (`job_memory_for` / `job_env_for` / `job_secrets_for`). The seven
matched-era jobs (import-osm, group-edges-osm, rebuild-heatmap,
rebuild-heat-agg, verify-heat-agg, migrate-edge-clustering,
recompute-elevation-gain) were removed from the list at the raw-trace pivot,
and the three Strava API sync jobs with the removal of that feature (§3).
**Why.** Jobs were
left pinned to a pre-migration image (git-sha `65251dfb`, pre-0056) and threw
`bigint = text` when they ran against the new schema. A deploy must never leave
a job on a stale image. Add any new job to that list the moment it's created
(cross-check `gcloud run jobs list --region=europe-west1` after a deploy).

## 3. Strava API sync is removed — orphaned prod resources

The product only ingests **files users upload**. The Strava OAuth connect, the
push webhook, the API sync jobs and the token storage are gone from the code
and from `deploy-prod.sh`. The deploy no longer touches the resources below;
they stay in the project until deleted **by hand** (nothing references them):

- Cloud Run Jobs `common-trails-import-strava-prod`,
  `common-trails-strava-subscribe-prod`, `common-trails-resync-strava-prod`
  (their entrypoints no longer exist in the image — a run would fail);
- Cloud Scheduler `strava-health-check-daily-prod` (now POSTs a route that
  no longer exists, so it fails every day until deleted);
- Cloud Tasks queue `common-trails-strava-webhook-prod`;
- Secret Manager `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`,
  `STRAVA_TOKEN_ENC_KEY`, `strava-webhook-verify-token`;
- GitHub Actions secret `STRAVA_WEBHOOK_SUBSCRIPTION_ID`;
- the Strava push subscription itself (Strava side; the API app is inactive).

Existing `provider='strava'` activities stay in the database and keep showing
in their owner's « Ma heatmap »; they never reach the community map
(provenance gate, `backend/app/services/provenance.py`).

## 4. The heatmap build is a Cloud Run Job (8Gi), not a DB tier bump

Since the raw-trace pivot, `build-pmtiles` reads `activities` directly
(`build_pmtiles.py`, raw branch of `main`) and the OSM substrate
(`osm_road_edges`, `heat_edges_agg`, …) is dropped in prod, so the old
"bump the Cloud SQL tier → rebuild `heat_edges` → downgrade" dance is gone.
The job runs on db-f1-micro; its memory is asserted at **8Gi** by
`deploy-prod.sh` (`job_memory_for`) because the export runs in a child process
(`_run_raw_export`, `HEATMAP_EXPORT_ISOLATED`) and shares one cgroup with the
tmpfs intermediates and tippecanoe. Lowering it there silently reverts any
out-of-band bump on the next deploy. Manual run + verification:
[`runbooks/heatmap-rebuild.md`](runbooks/heatmap-rebuild.md).

Anything on a **request or cron hot path** must stay bounded
(`statement_timeout`, bbox filters); the `/admin` stats are bounded + fail-soft
(`ADMIN_STAT_TIMEOUT_MS`, degrade to `-1` rather than dropping the connection).

## 5. Terraform is retired — do NOT `apply`

`infra/terraform/*.tf` were renamed `*.tf.archived` (see
[`infra/terraform/README.md`](../infra/terraform/README.md)): terraform state
still references deleted resources, so any `apply` would recreate them. Prod is
deployed by `deploy-gcp.yml` → `deploy-prod.sh` (gcloud only). The archived
files remain a design reference, not a source of truth.

## 6. Post-deploy verify

After a deploy, confirm the two build-time artefacts the frontend + /admin
depend on are current:

- **Freshness pointer.** `curl -s https://tiles.chemins-communs.fr/heatmap-display.json`
  — `snapshot_started_at` / `captured_at` tell you when the live heatmap was
  built (details in [`runbooks/heatmap-rebuild.md`](runbooks/heatmap-rebuild.md)).

- **`stats.json` published (#446).** The `build-pmtiles` job computes the
  home-banner headline via `compute_community_stats(db, min_uc)` (the SSOT in
  `backend/app/jobs/build_pmtiles.py`) and `publish_stats_json` writes it next to
  the PMTiles in the public export bucket. The home banner reads that static
  file — not a live query. Verify after a rebuild:

  ```bash
  curl -s https://storage.googleapis.com/common-trails-heatmap-prod/stats.json | python3 -m json.tool
  # expect contributors / activities / network_km to match the live heatmap
  ```

- **`heatmap_metrics` daily snapshot (#447).** Migration `0058_heatmap_metrics`
  adds the append-only `heatmap_metrics` time-series that drives the /admin
  freshness panel + evolution chart. One row is written per rebuild
  (`source='rebuild'`, inside `build_pmtiles`). The former once-a-day row
  (`source='daily'`) was piggybacked on the Strava health-check cron and
  stopped with it (§3). Numbers mirror `compute_community_stats` (same SSOT). Verify via
  the admin endpoint (see §7) or:

  ```sql
  SELECT captured_at, source, activities, contributors, network_km
  FROM heatmap_metrics ORDER BY captured_at DESC LIMIT 5;
  ```

  A stale-looking `captured_at` means no rebuild has fired recently.

## 7. Admin & monitoring — /admin, promote-admin, heatmap-metrics

**Prod has NO admin by default.** `main.py`'s lifespan seeds an `is_admin=True`
user ONLY under `TEST_MODE=true`; prod runs `TEST_MODE=false`, so no admin row
is ever created and the `/admin` dashboard (#443/#447) returns 403 for everyone
until you explicitly promote an account.

**Promote an account to admin** with the `promote_admin` CLI (idempotent —
re-running on an already-admin user is a no-op). Register/login with the
account first, then:

```bash
# by email
python -m app.cli.promote_admin paul@example.com
# by legacy Strava athlete id (integration_accounts.external_user_id → user)
python -m app.cli.promote_admin <athlete_id>
```

Run it with the prod `DATABASE_URL` in scope — locally against
`docker compose exec -T backend`, or in prod via a one-off Cloud Run Job on the
api image (any job in the `JOBS` list shares that image; override its command
with `--args` / `--command`) or through the Cloud SQL proxy from your machine.

The CLI resolves the identifier by email OR Strava athlete id (whichever
matches), prints the resolved user + the `is_admin` before/after, is idempotent,
and errors clearly on no-match or an ambiguous identifier. Source:
`backend/app/cli/promote_admin.py`.

**What /admin gives you.** `GET /admin/users` (per-user traces / per-sport)
and `GET /admin/heatmap-metrics` — the latter serves current
freshness (`_heatmap_freshness`) plus the last-N `heatmap_metrics` series that
render the dashboard's freshness panel + heatmap-size evolution chart. All reads
are bounded/fail-soft (never full-scan raw `heat_edges` on the request path).

## 8. Microservice split — light WEB (512Mi) + heavy WORKER (2Gi)

Since 2026-07-13 prod runs **two Cloud Run services from the SAME image**:

| Service | Memory | Scale | Serves |
|---|---|---|---|
| `common-trails-api-prod` (WEB) | **512Mi** | min=1 (warm), max=1 | static frontend + light API (auth, `/imports` init/complete, heatmap/export reads, `me_activities`) |
| `common-trails-worker-prod` (WORKER) | **2Gi** | min=0 (scale-to-zero), max=1 | the HEAVY Cloud Tasks handlers: `POST /internal/ingest/heat` (heat compute), `/internal/artefacts/rebuild` (PMTiles build) |

**Why.** The 2Gi + heavy CPU were forced by the in-request `/internal/*`
handlers on the single service. Moving them to a scale-to-zero worker lets the
public web drop to 512Mi (cheap enough to keep warm) while the heavy work only
spins up (and only bills) when Cloud Tasks has something to do.

**One image, one env, differs only by memory + traffic.** `deploy-prod.sh`
applies the *identical* env-file + secrets to both. The split is realised by
where `INTERNAL_HEAT_HANDLER_URL` / `INTERNAL_ARTEFACT_HANDLER_URL`
point (the **worker** `.run.app`) — the
web only *enqueues* (uses those vars to set the Cloud Tasks OIDC `audience`),
the worker *receives + verifies* (compares the token `aud` against the SAME
env var). Because both sides read one value, they can never drift apart.

**The OIDC-audience trap (why this is safe).** The 2026-06-04 401 loop was a
token signed for one host hitting a service that expected another. Here the
task audience is set *per task* by the web from `INTERNAL_*_HANDLER_URL` and
verified by the worker against the *same* env var — there is **no queue-level
audience to change**. Both services are `--allow-unauthenticated` (identical to
the pre-split single service), so the app-layer `verify_oidc_token` (signature +
`aud` + signer email) is the gate and the tasks-invoker SA needs **no**
`run.invoker` binding. The only hard requirement is that `WORKER_RUN_URL` in
`deploy-prod.sh` equals the worker's real `.run.app` URL — it is derived from
the project number (`{service}-301616827398.europe-west1.run.app`), the same
deterministic form the live web URL already uses.

**Post-deploy end-to-end verification** (do this after the FIRST split deploy):

```bash
# 1. Both services healthy
curl -fsS https://chemins-communs.fr/healthz                                        # web  → 200
curl -fsS https://common-trails-worker-prod-301616827398.europe-west1.run.app/healthz  # worker → 200

# 2. The heat → artefact chain lands on the WORKER, not the web.
#    Upload a file, then confirm the heavy work ran on the worker:
gcloud run services logs read common-trails-worker-prod --region=europe-west1 --limit=50 \
  | grep -E 'ingest/heat|artefacts/rebuild'      # expect handler logs HERE
gcloud run services logs read common-trails-api-prod    --region=europe-west1 --limit=50 \
  | grep -E 'ingest/heat|artefacts/rebuild'      # expect NOTHING (heavy work is off-web)

# 3. Artefact freshness — the worker's /internal/artefacts/rebuild uploaded PMTiles:
curl -sI https://storage.googleapis.com/common-trails-heatmap-prod/heatmap-display.pmtiles | grep -i last-modified

# 4. No 401s on the worker's /internal/* (would mean an audience mismatch):
gcloud run services logs read common-trails-worker-prod --region=europe-west1 --limit=100 | grep -i 'OIDC\|401' || echo "no OIDC/401 — good"
```

**Rollback.** The split is deploy-only — no schema/code change is required to
undo it. To revert to the single service, in `deploy-prod.sh` point the
heavy `INTERNAL_*_HANDLER_URL` back at `RUN_URL` (the web) and remove the
`deploy_service "$WORKER_SERVICE" …` call (or just stop routing to it), then
redeploy; the web goes back to 2Gi by setting the WEB memory to `2Gi`. The
idle worker costs ~nothing (scale-to-zero) so leaving it deployed but unwired is
also a valid, faster rollback. Cloud Tasks tasks already enqueued for the worker
drain normally; nothing needs draining to flip back.

**Warm web.** The WEB service runs `--min-instances=1` (`deploy-prod.sh`,
`deploy_service … 1`): at 512Mi a warm instance is cheap and kills the ~43 s
cold start. The WORKER stays min=0 (scale-to-zero is the whole point).

## Account

Deploy only as a **common-trails** deployer identity (`paleclercq@gmail.com` /
project `common-trails`) — never the `okeiro` account. The script prints the
active account + project in its pre-flight for a visual check.
