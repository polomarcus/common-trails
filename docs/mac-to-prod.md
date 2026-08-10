# Mac → Prod: complete ops runbook

> ⚠️ **SUPERSEDED for DEPLOYS.** Prod is deployed **ONLY** via
> `scripts/deploy-prod.sh` (see **`docs/prod-deploy.md`** — the current
> doctrine). **Terraform is frozen** (its state references DELETED resources →
> any `terraform apply` recreates them, including the old 200 GB DB) and the
> **`deploy-gcp` GitHub workflow is disabled**. Any `terraform apply` /
> push-to-main-to-deploy step in this doc is **FORBIDDEN**. The non-deploy
> content below (Cloud SQL proxy, IAM roles, OSM import mechanics, debugging)
> is still useful for reference.

> **Audience:** Either Paul running commands manually, OR Claude executing
> autonomously when given access to: this repo, `gcloud` CLI authenticated
> as a user with these IAM roles on the `common-trails` GCP project, and
> Docker on the local machine:
>
> - `roles/cloudsql.client` — connect to Cloud SQL via proxy
> - `roles/cloudsql.editor` — patch instance tier (§12 troubleshooting)
> - `roles/secretmanager.secretAccessor` — read DATABASE_URL secret
> - `roles/storage.objectAdmin` — read/write GCS buckets
> - `roles/run.admin` — execute Cloud Run Jobs, update services
> - `roles/artifactregistry.reader` — pull backend image
> - `roles/compute.loadBalancerAdmin` — invalidate CDN (when URL map exists)
>
> See **Section 13** for known infra gaps (URL map, custom domain,
> standalone GPX backup bucket).

Everything heavy (PMTiles build, SRTM enrichment, bulk GPX import) runs on
the Mac (24GB RAM) against the prod Cloud SQL DB via Cloud SQL Proxy.
Cloud Run serves only; never used for batch work.

---

## Table of contents

| § | Topic | First-time | Weekly | Daily |
|---|---|:-:|:-:|:-:|
| [0](#section-0--one-time-mac-setup-run-once-ever) | One-time Mac setup | ✅ | | |
| [1](#section-1--connect-to-prod-db-every-session) | Connect to prod DB | ✅ | ✅ | ✅ |
| [2](#section-2--osm-pbf-import-one-time-per-region) | OSM PBF import | ✅ | | |
| [3](#section-3--dfci--trail-data-mostly-automatic) | DFCI + trail data | ✅ | | |
| [4](#section-4--bulk-gpx-import-from-local-folder) | Bulk GPX import | ✅ | | |
| [5](#section-5--strava-import-alternative-or-complement-to-4) | Strava import | maybe | | |
| [6](#section-6--heatmap-rebuild) | Heatmap rebuild | ✅ | ✅ | ✅ |
| [7](#section-7--edge-enrichment-after-6) | Edge enrichment | ✅ | ✅ | |
| [8](#section-8--build-pmtiles--upload-to-cdn) | Build PMTiles + CDN | ✅ | ✅ | |
| [9](#section-9--routing-graphs-fgraph) | .fgraph routing graphs | ✅ | weekly | |
| [10](#section-10--end-to-end-verification) | E2E verification | ✅ | ✅ | |
| [11](#section-11--routine-ops) | Routine ops summary | | ✅ | ✅ |
| [12](#section-12--disaster-recovery) | Disaster recovery | | | |
| [13](#section-13--known-infra-gaps-todo) | Known infra gaps (TODO) | reference | | |
| [14](#section-14--post-run-analysis-claude-do-this-every-time) | Post-run analysis (Claude) | always | always | always |

---

## Quick reference card

After **§0** is done once, every session is just:

```bash
# 1. Connect to prod DB (one terminal tab)
ct-proxy

# 2. Common one-liners (in another tab)
ct-psql "SELECT COUNT(*) FROM activities;"     # peek at data
ct-run python -m app.cli.enrich_edges all      # run any CLI
ct-run python -m app.jobs.rebuild_heatmap --since 24h
ct-run python -m app.jobs.build_pmtiles --min-uc 1 --output-dir /out
gsutil cp /tmp/heatmap-display.pmtiles "$CT_FRONTEND_BUCKET/"
```

The `ct-proxy`, `ct-psql`, `ct-run` helpers are defined in **§0.4** below.

---

## Why Mac, not Cloud Run

| Task | RAM needed | Cloud Run (512MB–2GB) | Mac (24GB) |
|---|---|---|---|
| tippecanoe PMTiles build | 4–8GB | ❌ OOM | ✅ fast |
| OSM PBF import (France) | 6–10GB | ❌ OOM | ✅ fast |
| SRTM DEM enrichment | 1GB | ⚠️ API rate-limit (429) | ✅ local tiles |
| Bulk GPX import | 2GB | ✅ but slow | ✅ faster |
| rebuild_heatmap | 2–4GB | ⚠️ slow | ✅ fast |

---

## Section 0 — One-time Mac setup (run once ever)

### 0.1 Install tooling

```bash
# Required
brew install cloud-sql-proxy google-cloud-sdk
brew install --cask docker

# Verify
gcloud --version    # expect: Google Cloud SDK 4xx+
cloud-sql-proxy --version  # expect: 2.x+
docker --version    # expect: 24+
docker compose version  # expect: v2.x+
```

**Verification:** all four commands print versions, no errors.

### 0.2 Authenticate to GCP

```bash
gcloud auth login                          # opens browser
gcloud auth application-default login      # opens browser
gcloud config set project common-trails
gcloud config set compute/region europe-west1
```

**Verification:**
```bash
gcloud config list  # project = common-trails, region = europe-west1
gcloud auth list    # active account = your email
```

### 0.3 Save DB connection details (one-time, to ~/.zshrc or .envrc)

```bash
# Add to ~/.zshrc (or wherever your shell rc lives)
cat >> ~/.zshrc <<'EOF'
# ─── Common Trails prod ────────────────────────────────────────────────
export CT_PROJECT=common-trails
export CT_REGION=europe-west1
export CT_DB_INSTANCE="common-trails:europe-west1:common-trails-prod"
export CT_FRONTEND_BUCKET=gs://common-trails-frontend
# GPX backup bucket = uploads bucket (Terraform creates this; both gpx_archive
# and gpx_backup services write here)
export CT_UPLOADS_BUCKET=gs://common-trails-common-trails-uploads-prod
export CT_BACKEND_IMAGE=europe-west1-docker.pkg.dev/common-trails/backend/backend:latest
EOF
source ~/.zshrc
```

### 0.4 Helper functions (paste into ~/.zshrc)

These wrap the verbose `cloud-sql-proxy`, `psql`, and `docker run` commands
into 3 short helpers so the rest of this doc is short and copy-pastable.

```bash
cat >> ~/.zshrc <<'EOF'

# Start Cloud SQL Proxy on port 5433 + export PROD_DATABASE_URL
ct-proxy() {
  pkill -f "cloud-sql-proxy.*$CT_DB_INSTANCE" 2>/dev/null
  cloud-sql-proxy "$CT_DB_INSTANCE" --port 5433 &
  echo $! > /tmp/cloud-sql-proxy.pid
  sleep 2
  RAW_DATABASE_URL=$(gcloud secrets versions access latest \
    --secret=DATABASE_URL --project="$CT_PROJECT")
  export PROD_DATABASE_URL=$(echo "$RAW_DATABASE_URL" | \
    sed -E 's|@[^:/]+(:[0-9]+)?/|@localhost:5433/|')
  echo "✓ Cloud SQL Proxy on :5433 (PID $(cat /tmp/cloud-sql-proxy.pid))"
  echo "✓ PROD_DATABASE_URL exported"
}

ct-proxy-stop() {
  kill "$(cat /tmp/cloud-sql-proxy.pid)" 2>/dev/null
  rm -f /tmp/cloud-sql-proxy.pid
  unset PROD_DATABASE_URL
  echo "✓ Cloud SQL Proxy stopped"
}

# Run psql against prod (after ct-proxy)
ct-psql() {
  if [ -z "$PROD_DATABASE_URL" ]; then echo "Run ct-proxy first"; return 1; fi
  if [ -n "$1" ]; then psql "$PROD_DATABASE_URL" -c "$*"; else psql "$PROD_DATABASE_URL"; fi
}

# Run a CLI command in the prod backend image against prod DB
# Usage: ct-run python -m app.cli.enrich_edges all
#        ct-run -v /tmp/data:/data python -m app.cli.import_osm_roads /data/file.pbf
ct-run() {
  if [ -z "$PROD_DATABASE_URL" ]; then echo "Run ct-proxy first"; return 1; fi
  docker run --rm --network host \
    -e DATABASE_URL="$PROD_DATABASE_URL" \
    -e UPLOADS_BUCKET="$CT_UPLOADS_BUCKET" \
    "$CT_BACKEND_IMAGE" "$@"
}

# Variant that mounts a host folder. Usage:
#   ct-run-mount /Users/paul/gpx-export python -m app.cli.import_gpx_folder \
#     --folder /data --user-email me@example.com --sport offroad
ct-run-mount() {
  local mount="$1"; shift
  if [ -z "$PROD_DATABASE_URL" ]; then echo "Run ct-proxy first"; return 1; fi
  docker run --rm --network host \
    -e DATABASE_URL="$PROD_DATABASE_URL" \
    -e UPLOADS_BUCKET="$CT_UPLOADS_BUCKET" \
    -v "$mount:/data" \
    "$CT_BACKEND_IMAGE" "$@"
}
EOF
source ~/.zshrc

# One-time: pull the latest backend image
gcloud auth configure-docker europe-west1-docker.pkg.dev
docker pull "$CT_BACKEND_IMAGE"
```

**Verification:**
```bash
type ct-proxy ct-psql ct-run ct-run-mount  # all should print "is a function"
```

---

## Section 1 — Connect to prod DB (every session)

```bash
ct-proxy           # starts proxy + exports PROD_DATABASE_URL
```

**Verification (sample success output):**
```bash
ct-psql "SELECT version();"
#                                                 version
# ─────────────────────────────────────────────────────────────────────────────────────
#  PostgreSQL 17.4 on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19)...
```

When done for the day:
```bash
ct-proxy-stop
```

---

## Section 2 — OSM PBF import (one-time per region)

Imports OSM road geometry for map-matching. `france-latest.osm.pbf` is ~4GB,
takes ~60 minutes on db-f1-micro.

### 2.1 Pre-flight: check what's already imported

```bash
psql "$PROD_DATABASE_URL" -c "SELECT COUNT(*) FROM osm_road_edges;"
# 0 = nothing imported yet → proceed with section 2.2
# >5M = full France already done → skip this section
```

### 2.2 Download PBF

```bash
mkdir -p /tmp/pbf && cd /tmp/pbf
# For full France (~4GB):
curl -fSL -o france.pbf https://download.geofabrik.de/europe/france-latest.osm.pbf

# Or for a single region (lighter, e.g. Occitanie ~500MB):
# curl -fSL -o region.pbf https://download.geofabrik.de/europe/france/occitanie-latest.osm.pbf
```

**Verification:**
```bash
ls -lh /tmp/pbf/france.pbf
# expect: ~4GB file
```

### 2.3 Import

```bash
ct-run-mount /tmp/pbf python -m app.cli.import_osm_roads /data/france.pbf
# Positional arg, not --pbf. Also accepts a region name (e.g. "occitanie").
# ⏱ ~60 min for full France on db-f1-micro.
```

**Verification (sample success output):**
```bash
ct-psql "SELECT COUNT(*) FROM osm_road_edges;"
#  count
# ────────
#  9876543
# (1 row)
```

If the count is below 5M for full France, check the import log for errors
(disk full, OOM, alembic migration mismatch).

---

## Section 3 — DFCI + trail data (mostly automatic)

DFCI/trail edges with pre-baked SRTM slopes ship in the Docker image at
`backend/data/dfci_ign_edges.json` and `backend/data/trail_edges.json`.
The backend imports them automatically on first startup.

### 3.1 Verify after backend deploy

```bash
psql "$PROD_DATABASE_URL" -c "
  SELECT 'dfci_edges' AS tbl, COUNT(*) AS total,
         COUNT(*) FILTER (WHERE slope_grade > 0) AS w_slope FROM dfci_edges
  UNION ALL
  SELECT 'trail_edges', COUNT(*),
         COUNT(*) FILTER (WHERE slope_grade > 0) FROM trail_edges;
"
# expect: dfci_edges ~50K (90% w_slope), trail_edges ~48K (83% w_slope)
```

### 3.2 If empty: trigger import via container restart

```bash
# Force a new Cloud Run revision (re-runs container lifespan including DFCI/trail import)
gcloud run services update common-trails-api-prod \
  --region=$CT_REGION \
  --update-env-vars="REVISION_BUMP=$(date +%s)"
# REVISION_BUMP is a no-op env var — its only purpose is to trigger a new
# revision. The backend doesn't read it; new revision = container restart =
# lifespan re-runs the cache import from backend/data/*.json.
```

### 3.3 Refreshing DFCI/trail data (rare, only if source changes)

```bash
cd /Users/paulleclercq/projects/common-trails

# Re-extract DFCI from IGN (slow, 20min)
python scripts/extract_dfci_ign.py --bbox sud_france -o backend/data/dfci_ign_edges.json

# Re-enrich slopes from local SRTM (fast, 1s)
python3 scripts/enrich_cache_dem.py

# Commit + push → CI rebuilds image with fresh cache
git add backend/data/*.json
git commit -m "chore: refresh DFCI/trail cache with SRTM slopes"
git push origin main
```

---

## Section 4 — Bulk GPX import from local folder

Import a folder of GPX files (e.g. Strava export) into prod. Uses
`file_hash` dedup — running twice is idempotent.

### 4.1 Find the user UUID for the imports

> ⚠️ **Important:** in prod, `TEST_MODE=false` so no user is auto-seeded.
> The target user (e.g. `paul@epauler.fr`) **must register via the frontend
> first** — visit the Quick Connect or Strava OAuth flow before importing.

```bash
psql "$PROD_DATABASE_URL" -c "
  SELECT id, email FROM users WHERE email = 'paul@epauler.fr';
"
# If empty: register the user via the app, then re-run.
```

### 4.2 Dry-run first (counts files, no DB writes)

```bash
GPX_FOLDER=/Users/paul/Documents/strava-export

ct-run-mount "$GPX_FOLDER" python -m app.cli.import_gpx_folder \
  --folder /data --user-email paul@epauler.fr --sport offroad --dry-run
# Expected: "Found N .gpx files in /data"
```

### 4.3 Run the actual import

```bash
ct-run-mount "$GPX_FOLDER" python -m app.cli.import_gpx_folder \
  --folder /data --user-email paul@epauler.fr --sport offroad
```

**Options:** `--sport road|gravel|mtb|offroad|running`,
`--no-contribute-heatmap` (private import only).

**Verification:**
```bash
ct-psql "
  SELECT COUNT(*), ROUND(SUM(distance_m)/1000.0) AS total_km
  FROM activities WHERE provider='file';
"
#  count │ total_km
# ───────┼──────────
#    847 │   12345
```

### 4.4 Re-run is safe

If the import is interrupted, just re-run the same command. Files already
imported (matched by `file_hash`) will be reported as `duplicate`.

---

## Section 5 — Strava import (alternative or complement to §4)

If user has connected Strava OAuth, batch-import all their activities:

```bash
gcloud run jobs execute common-trails-import-strava-prod --region=$CT_REGION --wait
```

**Monitor:**
```bash
gcloud run jobs executions list --job=common-trails-import-strava-prod --region=$CT_REGION --limit=3
```

---

## Section 6 — Heatmap rebuild

After new activities are imported, regenerate the `heat_edges` aggregation.

### 6.1 Full rebuild (run after §2 or §4 first time)

```bash
ct-run python -m app.jobs.rebuild_heatmap
# ⏱ 5-15 min for 6M edges
```

### 6.2 Incremental rebuild (daily)

```bash
ct-run python -m app.jobs.rebuild_heatmap --since 24h
```

**Verification:**
```bash
ct-psql "SELECT sport, COUNT(*) FROM heat_edges GROUP BY sport ORDER BY 2 DESC;"
#   sport  │  count
# ─────────┼─────────
#  road    │ 3230551
#  running │ 2106137
#  gravel  │ 1076400
```

---

## Section 7 — Edge enrichment (after §6)

```bash
ct-run python -m app.cli.enrich_edges all
# Runs: heat-edges (OSM tags) + surfaces (DFCI/trail join)
#     + slopes (DEM-derived, only if not pre-baked) + dangerous-highways
# ⏱ ~5 min total.
```

Individual tasks if needed:
```bash
ct-run python -m app.cli.enrich_edges heat-edges
ct-run python -m app.cli.enrich_edges surfaces
ct-run python -m app.cli.enrich_edges slopes
ct-run python -m app.cli.enrich_edges dangerous-highways
```

---

## Section 8 — Build PMTiles + upload to CDN

The most critical step for end-user perf. Builds a single ~170MB file
that serves all heatmap tiles via HTTP range requests (no backend involved).

### 8.1 Build

```bash
mkdir -p /tmp/pmtiles-out
ct-run-mount /tmp/pmtiles-out python -m app.jobs.build_pmtiles \
  --min-uc 1 --output-dir /data
# ⏱ ~2 min. min-uc=1 for beta (all edges visible). Use min-uc=2 once
# K-anonymity is required for public release.
```

**Verification:**
```bash
ls -lh /tmp/pmtiles-out/heatmap-display.pmtiles
# -rw-r--r--  1 paul  staff  168M ... heatmap-display.pmtiles  (min-uc=1)
# -rw-r--r--  1 paul  staff   10M ... heatmap-display.pmtiles  (min-uc=2)
```

### 8.2 Upload to GCS

```bash
gsutil -h "Cache-Control:public, max-age=3600" \
  cp /tmp/pmtiles-out/heatmap-display.pmtiles \
     "$CT_FRONTEND_BUCKET/heatmap-display.pmtiles"
```

### 8.3 Invalidate CDN cache

> ⚠️ **Infra gap:** Terraform creates a `google_compute_backend_bucket`
> (`common-trails-frontend-cdn-prod`) with `enable_cdn = true`, but **no
> URL map / load balancer / custom domain is provisioned yet**. CDN
> invalidation requires a URL map. Until that's set up:

```bash
# Option A: discover any existing URL map
URL_MAP=$(gcloud compute url-maps list --filter="name~common-trails" \
  --format="value(name)" --limit=1)

if [ -n "$URL_MAP" ]; then
  gcloud compute url-maps invalidate-cdn-cache "$URL_MAP" \
    --path "/heatmap-display.pmtiles" --async
else
  echo "No URL map found — clients hit the storage origin directly."
  echo "Cache busts only via Object versioning (gsutil rewrite) or wait for"
  echo "the 1h Cache-Control TTL set in §8.2 to expire."
fi
```

### 8.4 Verify deployed file

```bash
# If you've configured a custom domain (see §13 Infra gaps):
# curl -sI -H "Range: bytes=0-127" https://chemins-communs.fr/heatmap-display.pmtiles

# Otherwise, hit the GCS public URL directly:
curl -sI -H "Range: bytes=0-127" \
  "https://storage.googleapis.com/common-trails-frontend/heatmap-display.pmtiles" \
  | grep -E "HTTP|Content-Range|Content-Length"
# expect: HTTP/2 206, Content-Range: bytes 0-127/...
```

---

## Section 9 — Routing graphs (.fgraph)

```bash
cd /Users/paulleclercq/projects/common-trails
./wasm-router/build.sh --prebuild
# Produces frontend/public/*.fgraph (~23MB max per partition)

# Upload all
gsutil -m cp frontend/public/*.fgraph "$CT_FRONTEND_BUCKET/"

# Invalidate (only if URL map exists — see §8.3 caveat)
URL_MAP=$(gcloud compute url-maps list --filter="name~common-trails" --format="value(name)" --limit=1)
[ -n "$URL_MAP" ] && gcloud compute url-maps invalidate-cdn-cache "$URL_MAP" --path "/*.fgraph"
```

**Verification:**
```bash
# Direct GCS URL (works without custom domain):
curl -sI "https://storage.googleapis.com/common-trails-frontend/gravel-sud-est.fgraph" \
  | grep -E "HTTP|Content-Length"
# expect: HTTP/2 200, ~25MB
```

---

## Section 10 — End-to-end verification

After everything above, sanity-check the user-facing service. Get the
Cloud Run URLs from the deployment first:

```bash
API_URL=$(gcloud run services describe common-trails-api-prod \
  --region=$CT_REGION --format='value(status.url)')
echo "API: $API_URL"

# Frontend public URL (storage.googleapis.com origin until URL map exists)
FRONTEND_URL="https://storage.googleapis.com/common-trails-frontend"
echo "Frontend: $FRONTEND_URL"
```

```bash
# API healthy
curl -sf "$API_URL/healthz" | jq .
# expect: {"status":"ok","version":"..."}

# Heatmap summary fast (<200ms)
time curl -sf "$API_URL/heatmap/summary" > /dev/null

# Frontend serves PMTiles with Range support
curl -sI -H "Range: bytes=0-127" "$FRONTEND_URL/heatmap-display.pmtiles" | head -3
# expect: HTTP/2 206

# DFCI endpoint returns features
curl -sf "$API_URL/heatmap/dfci" | jq '.features | length'
# expect: 50000+ (or whatever's in the DB)

# Routing endpoint works (single-route GET /routing was removed late May
# 2026; use /routing/proposals to smoke-test the server-side cascade).
curl -sf -X POST "$API_URL/routing/proposals" \
  -H "Content-Type: application/json" \
  -d '{"start_lon":3.875,"start_lat":43.612,"end_lon":3.88,"end_lat":43.62,"profile":"gravel"}' \
  | jq '.proposals[0].distance_m'
# expect: a number > 0
```

---

## Section 11 — Routine ops

### Daily (cron-able)

```bash
ct-proxy && ct-run python -m app.jobs.rebuild_heatmap --since 24h
```

### Weekly: rebuild + upload PMTiles

Run sections **6.1**, **7**, **8.1**, **8.2**, **8.3** in order.

### After every infra change

Just push to `main` — the GitHub Actions `deploy-gcp.yml` workflow:
1. Builds frontend static export
2. Builds + pushes backend Docker image
3. Deploys Cloud Run API + Jobs
4. Runs `terraform apply`

You don't need to do anything for code-only changes.

---

## Section 13 — Known infra gaps (TODO)

The following are referenced in this doc but **not yet provisioned by
Terraform**. Document them honestly so future Claude/Paul knows what's
missing vs misconfigured.

### 13.1 No URL map / load balancer

Terraform creates a `google_compute_backend_bucket` (CDN-enabled), but
no `google_compute_url_map`, `google_compute_target_https_proxy`, or
`google_compute_global_forwarding_rule`. Consequences:
- No custom domain (clients hit `storage.googleapis.com` URLs)
- `gcloud compute url-maps invalidate-cdn-cache` fails
- Cache busts only via Object versioning or waiting for `Cache-Control` TTL

**Fix:** add to `infra/terraform/main.tf`:
- `google_compute_url_map`
- `google_compute_target_https_proxy` (with managed cert)
- `google_compute_managed_ssl_certificate` for `chemins-communs.fr`
- `google_compute_global_forwarding_rule`
- `google_compute_global_address` (anycast IP)

### 13.2 No custom domain (chemins-communs.fr)

Same root cause as 13.1. Once URL map + cert exist, point DNS A record
to the global forwarding rule's anycast IP.

### 13.3 No standalone GPX backup bucket

Terraform creates ONE `uploads` bucket (`google_storage_bucket.uploads`,
named `${project_id}-common-trails-uploads-${env}`). Both `gpx_archive`
and `gpx_backup` services write to it (since they share `UPLOADS_BUCKET`
env var via the fallback in `gpx_backup.py`).

If a separate bucket is needed (e.g. different lifecycle for hash-keyed
backups vs activity-keyed archives), add a `google_storage_bucket.gpx_backup`
resource and set `GPX_BACKUP_BUCKET` env var explicitly.

### 13.4 No daily heatmap rebuild scheduler

Terraform creates a Cloud Run Job for `rebuild-heatmap` but the only
Scheduler entry is for monthly `resync-strava` (3 AM 1st of month). Add
a daily `google_cloud_scheduler_job` to invoke `rebuild-heatmap --since 24h`.

### 13.5 PMTiles upload not automated

Section 8 must be run manually after each heatmap rebuild. Consider:
- Adding `build_pmtiles` as a Cloud Run Job
- Wiring it as a downstream step of `rebuild-heatmap` job (Cloud Workflows)
- Or running on local Mac via cron (current approach — see §11 weekly)

---

## Section 12 — Disaster recovery

### DB lost: restore from Cloud SQL backup (24h RPO)

```bash
gcloud sql backups list --instance=common-trails-prod
gcloud sql backups restore <BACKUP_ID> \
  --restore-instance=common-trails-prod --backup-instance=common-trails-prod
```

### Worst case: rebuild from GPX backup bucket

```bash
# 1. List user backups
gsutil ls "$CT_UPLOADS_BUCKET/" | head -10
# format: $CT_UPLOADS_BUCKET/{user_uuid}/{file_hash}.gpx

# 2. Download
mkdir -p /tmp/gpx-restore
gsutil -m cp -r "$CT_UPLOADS_BUCKET/*" /tmp/gpx-restore/

# 3. Re-import per user (need to map UUID → email first via psql)
for user_dir in /tmp/gpx-restore/*/; do
  user_uuid=$(basename "$user_dir")
  email=$(psql "$PROD_DATABASE_URL" -tA -c "SELECT email FROM users WHERE id='$user_uuid';")
  if [ -z "$email" ]; then
    echo "Skip $user_uuid: not in users table"
    continue
  fi
  echo "Restoring $email from $user_dir..."
  ct-run-mount "$user_dir" python -m app.cli.import_gpx_folder \
    --folder /data --user-email "$email" --sport gravel
done

# 4. Re-run §6, §7, §8 to rebuild heatmap + PMTiles
```

---

## Reference: env vars

| Var | Dev | Prod |
|---|---|---|
| `HEATMAP_K_ANONYMITY` | 1 | 2 (when public) |
| `--min-uc` (PMTiles) | 1 | 1 (beta) / 2 (public) |
| `DATABASE_URL` | local docker | Cloud SQL via proxy (full URL in Secret Manager) |
| `UPLOADS_BUCKET` | `/data/gpx-backup` | bucket name from Terraform `google_storage_bucket.uploads` |
| `GPX_BACKUP_BUCKET` | (unset → uses UPLOADS_BUCKET) | (unset → uses UPLOADS_BUCKET) |
| `DEM_PROVIDER` | `open-meteo` or `none` | `none` (slopes pre-baked) |
| `TEST_MODE` | `true` | `false` |
| `ADMIN_EMAIL` | `admin@admin` (auto-seeded) | n/a (users register via frontend) |

---

## Troubleshooting decision tree

**"Cloud SQL Proxy: permission denied"**
→ `gcloud auth application-default login`
→ Verify role: `gcloud projects get-iam-policy common-trails --flatten="bindings[].members" --filter="bindings.members~$(gcloud config get account)"`

**"docker pull: denied"**
→ `gcloud auth configure-docker europe-west1-docker.pkg.dev`

**"tippecanoe: command not found"**
→ Already in the prebuilt backend image. Make sure you used `docker run` (not bare `python -m`).

**"PMTiles 404 in browser after upload"**
→ Check `Cache-Control` header (§8.2 sets it explicitly)
→ Run §8.3 to invalidate CDN
→ Wait 30s for invalidation to propagate

**"OSM PBF import hangs at 99%"**
→ db-f1-micro = 0.6GB RAM. Scale up temporarily:
```bash
gcloud sql instances patch common-trails-prod --tier=db-g1-small
# wait for the instance to restart (~2min)
# run §2.3 again
# scale back when done:
gcloud sql instances patch common-trails-prod --tier=db-f1-micro
```

**"GPX folder import has many errors"**
→ Check log lines with `✗` for the reason
→ Common: invalid GPX XML (skip the file), wrong sport name (only road/gravel/mtb/offroad/running accepted)
→ "duplicate" status is normal — dedup is working

**"All operations seem to hang on first request after deploy"**
→ Cloud Run scaled to zero. First request triggers cold start (~10s). Subsequent are fast.
→ To pre-warm: `for i in 1 2 3; do curl -sf "$API_URL/healthz"; sleep 1; done`

---

## Quick-start: full bootstrap from empty DB (sequential)

For a Claude prompt: run sections in order, verifying each before next.

| Step | Action | Time | Idempotent? |
|---|---|---:|:-:|
| §0 | install + auth (one-time) | 10min | yes |
| §1 | `ct-proxy` | 5s | yes |
| §2 | OSM PBF import (full France) | 60min | yes (skip if >5M rows) |
| §3 | verify DFCI/trail (auto on container restart) | 30s | yes |
| §4 | bulk GPX import (1000 files) | 10min | yes (file_hash dedup) |
| §6.1 | rebuild heatmap | 10min | yes |
| §7 | enrich edges | 5min | yes (EXISTS pre-check) |
| §8 | build + upload PMTiles | 5min | yes |
| §9 | build + upload .fgraph | 5min | yes |
| §10 | verify end-to-end | 1min | yes |

**Total: ~90min wall time, ~30min if §2 PBF skipped.**

> **Cost on GCP:** ~$0.50 per full bootstrap (Cloud SQL CPU during import,
> network egress for PMTiles upload). Daily routine ops: <$0.10/day.

---

## Section 14 — Post-run analysis (Claude: do this every time)

> **For Claude:** after the first end-to-end run completes, do NOT just
> declare success. Spend 5-10 minutes analyzing the logs and the resulting
> data, then propose concrete improvements before handing back to Paul.

### 14.1 Collect timing data

```bash
# Backend container logs (covers ingestion, enrichment, PMTiles build)
docker logs $(docker ps -lq --filter "ancestor=europe-west1-docker.pkg.dev/common-trails/backend/backend:latest") 2>&1 | tee /tmp/run.log

# Or capture each `docker run` output by piping to a per-step log file:
#   2>&1 | tee /tmp/step-N-rebuild_heatmap.log
```

Look for:
- `[INFO] ... loaded: N (XXs)` — per-step durations
- `Background load DONE in XXs` — total background ingest time
- `Match: N OSM, M grid-fallback` — OSM map-match success rate
- `Surface enrichment: N unenriched ... 0 edges` — wasted work signal
- `Killed`, `OOM`, `Connection refused` — failure signals

### 14.2 Query the resulting DB state

```bash
psql "$PROD_DATABASE_URL" <<'EOF'
-- Activity ingestion stats
SELECT provider, COUNT(*) AS activities,
       ROUND(SUM(distance_m)/1000.0) AS total_km,
       MIN(activity_date)::date AS first,
       MAX(activity_date)::date AS last
FROM activities GROUP BY provider;

-- Heat edges coverage
SELECT sport, COUNT(*) AS edges,
       COUNT(*) FILTER (WHERE surface_type IS NOT NULL AND surface_type <> 'unknown') AS w_surface,
       COUNT(*) FILTER (WHERE ele_delta_m IS NOT NULL AND ele_delta_m <> 0) AS w_elev,
       COUNT(*) FILTER (WHERE osm_way_id IS NOT NULL) AS w_osm
FROM heat_edges GROUP BY sport ORDER BY edges DESC;

-- Singletons (potential dedup misses)
SELECT user_count, COUNT(*) AS edges FROM heat_edges
GROUP BY user_count ORDER BY user_count LIMIT 10;

-- Activities with no geometry (parse failures or empty traces)
SELECT COUNT(*) FROM activities WHERE geometry_geojson IS NULL;
EOF
```

### 14.3 Improvement checklist — Claude must answer each

After the first run, file a short report with concrete answers:

**Ingestion quality**
- [ ] What % of edges have `surface_type` populated? If <50%, suggest: rerun §7 surfaces, or improve OSM tag coverage by importing a denser PBF region.
- [ ] What % of edges have `osm_way_id` matched? If <70%, the OSM map-match is missing geometry — suggest: tighten match radius or import additional regions.
- [ ] How many activities failed to parse (`geometry_geojson IS NULL`)? Investigate the GPX files locally if >5%.

**Performance**
- [ ] Did any single CLI step take >10min? If so, check logs for slow queries and propose adding indexes / batching.
- [ ] Did `Match: N OSM, M grid-fallback` show grid-fallback >> OSM? Map-match isn't working — the OSM PBF region may not cover the activity area.
- [ ] Did `enrich_edges surfaces` produce 0 results? Either nothing to enrich or pre-check is broken — verify EXISTS query against current data.

**Resource usage**
- [ ] Cloud SQL CPU/memory during ingest — was db-f1-micro saturated? If yes, recommend temporary scale-up to db-g1-small for next big import.
- [ ] PMTiles build RAM peak — was it >4GB? If yes, suggest tippecanoe `--cluster-distance` to keep memory in check.

**Data freshness**
- [ ] When was the last `rebuild_heatmap` run? If >24h old and there are new activities, suggest a daily cron via Cloud Scheduler.
- [ ] PMTiles file `Last-Modified` (curl -I) vs heatmap rebuild time — if PMTiles is older, propose automating §8 after §6.

**Anomalies**
- [ ] Any backend log line containing `Killed`, `OOM`, or `Traceback`? Root-cause it — don't ignore.
- [ ] Any user with abnormally high activity count (>1000)? Could indicate Strava re-sync duplicating instead of dedup'ing.

### 14.4 Output format

Claude's report should be a single markdown block:

```markdown
## Run summary — YYYY-MM-DD

**Ingested:** N activities (X km), M heat_edges across {sports}
**Coverage:** surface=X%, elevation=Y%, OSM-matched=Z%
**Total wall time:** XXmin (vs estimated 90min)

### Issues found
- [SEVERITY] Description → proposed fix

### Recommended next actions
1. Concrete CLI command or code change
2. ...

### Performance opportunities (no code change yet)
- ...
```

Then ask Paul which improvements to implement before continuing.
