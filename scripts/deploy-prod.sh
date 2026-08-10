#!/usr/bin/env bash
#
# deploy-prod.sh — the SINGLE SOURCE OF TRUTH for a Common Trails prod deploy.
#
# WHY THIS EXISTS
#   Prod is deployed by the deploy-gcp.yml workflow (MANUAL workflow_dispatch
#   only since 2026-08-07 — no more auto-deploy on push), or by running THIS
#   script locally. NOT terraform: its state
#   still references the DELETED old resources, so any `apply` recreates them
#   (the ~€65/mo trap). Do NOT hand-type `gcloud run deploy` and do NOT
#   `terraform apply`. Twice, a hand-typed
#   `gcloud run deploy --set-env-vars=...` dropped half the env — specifically
#   the webhook wiring (CLOUD_TASKS_*_QUEUE, INTERNAL_*_HANDLER_URL,
#   STRAVA_WEBHOOK_*) — leaving the Strava push webhook silently OFF until the
#   weekly resync scheduler happened to backfill. `--set-env-vars` REPLACES the
#   whole env set, so anything you forget to list is gone.
#
#   This script fixes that class of bug by being DECLARATIVE: it applies the
#   COMPLETE env set every time via `--env-vars-file` (authoritative full
#   replace) + `--set-secrets`, so a var can never silently persist-stale or
#   drop. The set below is the env set of the LIVE-GOOD revision
#   (common-trails-api-prod rev 00395, working in prod right now), captured
#   1:1 from `gcloud run services describe` — NOT terraform's superset. We
#   deliberately do NOT add speculative vars that main.tf declares but the
#   live revision doesn't carry (matview queue [dropped in 0055], export
#   queue, DB_POOL_SIZE, SKIP_OSM_FETCH, …): the
#   working revision is the SSOT, and adding env that live doesn't have IS
#   itself drift. Re-verify with the "describe" one-liner in docs/prod-deploy.md
#   whenever prod's env legitimately changes, and update this block to match.
#   It also syncs EVERY Cloud Run Job to the same image (stale job images on a
#   pre-migration schema caused `bigint = text` failures — see the JOBS list).
#
#   JOB ENV IS NOW ASSERTED TOO (audit gap #6, 2026-07-20). The env-vars-file
#   above only reaches the two SERVICES; a Cloud Run JOB runs the same api image
#   but carries its OWN env, set out-of-band, which DRIFTS. Tonight the drain job
#   common-trails-ingest-pending-archives-prod ran with OVERPASS_ENABLED
#   ABSENT→true (live Overpass calls to foreign tiles = massive slowdown) while
#   the services correctly had OVERPASS_ENABLED=false; earlier a hand-typed
#   `--set-env-vars` had wiped its UPLOADS_BUCKET/BUILD_PMTILES_JOB_NAME. So the
#   JOBS loop below now DECLARATIVELY asserts the load-bearing env on the jobs
#   that need it via `--update-env-vars` (NEVER `--set-env-vars` — that replaces
#   the whole set, the exact trap documented above). Secrets (DATABASE_URL etc.)
#   are NOT touched here — jobs get those from their own --set-secrets at create.
#
#   MICROSERVICE SPLIT (2026-07-13): this script now deploys TWO Cloud Run
#   services from the SAME image with the SAME authoritative env — a light
#   512Mi public WEB and a scale-to-zero 2Gi WORKER that owns the heavy Cloud
#   Tasks /internal handlers (heat compute, PMTiles rebuild, Strava webhook).
#   See the "MICROSERVICE SPLIT" topology block below + docs/prod-deploy.md §8.
#
# USAGE
#   export STRAVA_WEBHOOK_SUBSCRIPTION_ID='349136'  # required (empty '' = allow-all)
#   scripts/deploy-prod.sh <IMAGE_TAG>              # e.g. prod-20260711-1710 or a git sha
#   # non-interactive: DEPLOY_YES=1 scripts/deploy-prod.sh <IMAGE_TAG>
#
#   The <IMAGE_TAG> must already be built+pushed to Artifact Registry, e.g.:
#     gcloud builds submit ...   OR the CI build step, OR:
#     docker build --platform=linux/amd64 -t "$REPO/api:<tag>" backend && docker push ...
#   (Frontend is baked into backend/static-frontend before the image build.)
#
# NEVER run this from an agent/CI against prod without Paul's go. It mutates
# live prod. `bash -n` / shellcheck is the repo-side validation.

set -euo pipefail

# ── Fixed prod topology (matches infra/terraform/main.tf) ────────────────────
PROJECT="common-trails"
REGION="europe-west1"
ENV="prod"
# ── MICROSERVICE SPLIT (2026-07-13) — light web + heavy worker ───────────────
# TWO Cloud Run services from the SAME image, running the SAME FastAPI app with
# the SAME env set. They differ ONLY in size + which traffic each receives:
#
#   • WEB  (${WEB_SERVICE:-common-trails-api-prod}) — public. Static frontend +
#     light API (auth, /imports, Strava OAuth, heatmap/export reads,
#     me_activities). Sized 512Mi so warm is cheap. Keeps the historical name,
#     so the domain mapping + its .run.app URL are UNCHANGED.
#
#   • WORKER (common-trails-worker-prod) — scale-to-zero, 2Gi. Owns the HEAVY
#     Cloud Tasks handlers: POST /internal/ingest/heat, /internal/artefacts/
#     rebuild (PMTiles build), /internal/strava/webhook-event. Cloud Tasks
#     POSTs to the WORKER's URL (INTERNAL_*_HANDLER_URL below), so the 2Gi
#     memory + heavy CPU live off the user-facing service.
#
# Both services carry the identical env-file; the split is realised purely by
# where the INTERNAL_*_HANDLER_URL vars point (worker) + the per-service memory
# knobs. Nothing routes public traffic to the worker; nothing enqueues heavy
# work to the web. The web's dormant /internal routes are never hit → harmless.
# (The daily Strava health-check is LIGHT — cheap SQL + one API call — so it
# STAYS on the web to avoid re-pointing its Cloud Scheduler OIDC audience.)
WEB_SERVICE="common-trails-api-${ENV}"
WORKER_SERVICE="common-trails-worker-${ENV}"
SERVICE="$WEB_SERVICE"  # back-compat alias for pre-flight / messages
REPO="${REGION}-docker.pkg.dev/${PROJECT}/${PROJECT}"
API_SA="common-trails-api@${PROJECT}.iam.gserviceaccount.com"
TASKS_INVOKER_SA="common-trails-tasks-invoker@${PROJECT}.iam.gserviceaccount.com"
# Cloud SQL connection name = project:region:instance. The instance is the
# fresh (2026-07) db-f1-micro "common-trails-prod" (the old 200 GB one is dead).
CLOUDSQL_INSTANCE="${PROJECT}:${REGION}:common-trails-${ENV}"
# GCS data bucket (DFCI cache / DEM tiles), mounted read-only at /data.
DATA_BUCKET="${PROJECT}-common-trails-data-${ENV}"
# USER-FACING origin (custom domain). Used ONLY for FRONTEND_URL, the Strava
# OAuth redirect, and the Strava webhook callback — the URLs a human/Strava
# hits.
PUBLIC_URL="https://chemins-communs.fr"
# MACHINE-TO-MACHINE origins (Cloud Run auto-assigned .run.app URLs). The
# INTERNAL_*_HANDLER_URL vars use THESE, because Cloud Tasks / Cloud Scheduler
# sign OIDC tokens whose `audience` must match the handler URL byte-for-byte —
# and the receiver (`verify_oidc_token`, internal_ingest.py) compares the
# token's `aud` claim against the SAME env var. Custom-domain here reintroduces
# the 2026-06-04 401 loop (token signed for one host, service expects the
# other).
#
# Cloud Run URLs in this project are the deterministic
# `{service}-{PROJECT_NUMBER}.{region}.run.app` form (project number
# 301616827398 — the WEB url below is VERIFIED against rev 00395). The WORKER
# url is the SAME pattern for the new service name; it resolves the moment the
# worker service exists (first deploy). ⚠️ If Cloud Run ever reassigns EITHER
# url, re-capture it from `describe` (docs/prod-deploy.md §1) AND re-issue the
# OIDC-token creators (the 3 heat/artefact/webhook Cloud Tasks queues target
# the WORKER url; the health-check scheduler targets the WEB url).
PROJECT_NUMBER="301616827398"
RUN_URL="https://${WEB_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"           # WEB (public)
WORKER_RUN_URL="https://${WORKER_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app" # WORKER (heavy /internal/*)

# ── Required operator inputs (deployment state, NOT code) ─────────────────────
# `${VAR?msg}` errors if UNSET but allows an explicit empty string. Requiring it
# means an authoritative redeploy can never silently WIPE the live value.
# (SENTRY_DSN is now wired as a Secret Manager secret — see the SECRETS block —
#  so error/perf observability actually reports; 2026-08-07.)
: "${STRAVA_WEBHOOK_SUBSCRIPTION_ID?export STRAVA_WEBHOOK_SUBSCRIPTION_ID (empty '' = allow-all subscriptions; the live value is 349136 — set it after 'app.cli.strava_subscribe create')}"

IMAGE_TAG="${1:-}"
if [[ -z "$IMAGE_TAG" ]]; then
  echo "ERROR: image tag required. Usage: scripts/deploy-prod.sh <IMAGE_TAG>" >&2
  exit 2
fi
IMAGE="${REPO}/api:${IMAGE_TAG}"

# ── ALL Cloud Run Jobs — synced to the SAME image as the service ─────────────
# A deploy must NEVER leave a job on a stale image: multiple jobs were pinned
# to git-sha 65251dfb (pre-migration-0056) and blew up with `bigint = text`
# when they ran against the new schema. Every job that shares the api image is
# listed EXPLICITLY here. Add new jobs to this list the moment they're created.
# ── MATCHED-ERA JOBS REMOVED (raw-trace pivot, 2026-07-29) ───────────────────
# The community heatmap is now built from RAW GPS traces read straight out of
# `activities` (HEATMAP_DISPLAY_SOURCE=raw); the OSM substrate (osm_road_edges /
# osm_ways / heat_edges_agg) was DROPPED. Seven jobs that ONLY ever operated on
# that now-dead substrate were removed from this sync list — they are permanently
# defunct and re-syncing their images every deploy was pure waste:
#   import-osm, group-edges-osm, rebuild-heatmap, rebuild-heat-agg,
#   verify-heat-agg, migrate-edge-clustering, recompute-elevation-gain.
# What remains below are the LIVE jobs of the raw pipeline + Strava sync.
JOBS=(
  "common-trails-import-strava-${ENV}"          # Strava full import (per-user, personal)
  "common-trails-strava-subscribe-${ENV}"       # Strava push-subscription bootstrap CLI
  "common-trails-resync-strava-${ENV}"          # weekly trailing-window reconcile (load-bearing — recovers webhook drops)
  "common-trails-build-pmtiles-${ENV}"          # heatmap-display.pmtiles from RAW activities (raw-trace pivot)
  "common-trails-ingest-pending-archives-${ENV}" # drain uploaded archives (signed-URL flow) → manual_upload activities
  "common-trails-resync-reminder-${ENV}"        # monthly "re-export + re-upload your data" reminder email (stale contributors)
)

# ── The COMPLETE, authoritative non-secret env set ───────────────────────────
# Written to a YAML and applied with `--env-vars-file` (full replace). Anything
# NOT here is removed from the service — that is the point. Secrets are applied
# separately via `--set-secrets` (also authoritative). Keep this in lockstep
# with the api `containers.env` block in infra/terraform/main.tf.
ENV_FILE="$(mktemp)"  # gcloud --env-vars-file parses by content, not extension
trap 'rm -f "$ENV_FILE"' EXIT
# ── EXACTLY the live-good revision 00395 env set (verified via `describe`) ────
# 29 literal vars below + 7 secrets (SECRETS, further down) = 36 env entries.
# (rev 00395 baseline was 23 + 6 = 29; +EMAIL_FROM +RESEND_API_KEY were added
# for the email-login feature — passwordless magic-link auth;
# +INGEST_ARCHIVES_JOB_NAME for the event-driven archive-drain trigger;
# +BUILD_PMTILES_JOB_NAME for the event-driven post-drain heatmap rebuild;
# +MAX_ARCHIVE_BYTES to admit real GB-scale Strava archives;
# +HEATMAP_KEEP_GRID_FALLBACK +HEATMAP_GRID_FALLBACK_MIN_UC for the
# desire-lines display policy, 2026-07-20.) Do not
# add/remove without re-verifying against live.
cat >"$ENV_FILE" <<YAML
TEST_MODE: "false"
ENABLE_STRAVA_INTEGRATION: "true"
# K-anonymity: single-user beta → ~99.9% of edges are user_count=1, so K=2 would
# blank the public heatmap. Live is 1. Flip to 2 once there are >=2 contributors
# (CLAUDE.md prod doctrine). See feedback_min_uc_1_for_beta.
HEATMAP_K_ANONYMITY: "1"
# Desire lines (Paul 2026-07-20: "j'ai pas envie de perdre les lignes de
# désir"): keep grid-fallback (off-OSM) edges on BOTH display paths (static
# PMTiles + live MVT — SSOT heat_aggregation.resolve_grid_fallback_display).
# MIN_UC empty = follow HEATMAP_K_ANONYMITY (1 in the beta → solo
# singletracks show; auto-tightens to 2 when K flips). The 60 m grid length
# cap stays hardwired for GPS-jump noise.
HEATMAP_KEEP_GRID_FALLBACK: "true"
HEATMAP_GRID_FALLBACK_MIN_UC: ""
# ── RAW-TRACE display pivot (2026-07-29) ─────────────────────────────────────
# The community heatmap renders precise RAW GPS traces (manual_upload only)
# instead of the OSM-matched aggregate. Privacy = endpoint masking + a
# distinct-user floor, NOT K-anonymity. build_pmtiles reads 'activities'
# directly in raw mode (heat_edges untouched → dormant until the substrate
# drop). See docs/raw-trace-cutover-runbook.md + the 'heatmap' skill.
#   • DISPLAY_SOURCE=raw   — select the raw path (default 'matched').
#   • MIN_USERS=1          — show every trace incl. solo (K=1 beta; flip to 2
#                            at ~50 users — the "K=2 later" promise in one var).
#   • TRACE_MASK_METERS=200— trim home/work off both physical ends.
#   • RAW_BBOX (Europe)    — region clip that drops WHOLLY-corrupt traces (prod
#                            had two 8-point mid-Atlantic GPS spikes raw would
#                            draw verbatim; the embedded-spike median guard runs
#                            always-on). Widen/append if riding outside Europe.
HEATMAP_DISPLAY_SOURCE: "raw"
HEATMAP_MIN_USERS: "1"
TRACE_MASK_METERS: "200"
HEATMAP_RAW_BBOX: "-12;34;32;62"
# Publish a raster XYZ tile pyramid (raster/{z}/{x}/{y}.png + raster/tiles.json)
# to the public heatmap bucket on every build so gpx.studio / VisuGPX can add the
# heatmap as a custom overlay ("calque") via one URL. Read by build_pmtiles.main
# — which runs BOTH as the build-pmtiles job AND in-process on the worker via
# /internal/artefacts/rebuild, so it must be on the SERVICES (this block) AND the
# job (job_env_for). z6-14, ~10k tiles, ~70s.
HEATMAP_RASTER_PYRAMID: "true"
DATA_DIR: "/app/data"
# User-facing origin — server-side OAuth redirects default to localhost:3787
# without it (config.py), which breaks every prod Strava connect.
FRONTEND_URL: "${PUBLIC_URL}"
# Cloud Run Job dispatch (integrations_strava.py reads GCP_PROJECT/GCP_REGION).
GCP_PROJECT: "${PROJECT}"
GCP_REGION: "${REGION}"
# Overpass stays off in prod (rate-limited, slow); OVERPASS_ENABLED=false is
# what the live revision uses (overpass.py / graph_tiles.py / ingest.py all
# honour it — a tile miss falls back to grid-snap, no 5-15 s round-trip).
OVERPASS_ENABLED: "false"
STRAVA_REDIRECT_URI: "${PUBLIC_URL}/integrations/strava/callback"
# ── Cloud Tasks queues + their /internal handler URLs (the wiring that got
#    DROPPED twice → Strava webhook silently OFF). Queue names match the
#    google_cloud_tasks_queue resources; handler URLs are OIDC-audience-bound
#    to the .run.app origin — see the RUN_URL / WORKER_RUN_URL note above.
#    ── MICROSERVICE SPLIT ── the three HEAVY handlers point at WORKER_RUN_URL:
#    Cloud Tasks signs 'audience = <this url>' and POSTs to it → the WORKER
#    (2Gi) does the heat compute / PMTiles build / Strava fetch+ingest, and
#    the WORKER verifies the same audience against this same env var. The web
#    (512Mi) only ENQUEUES (reads these vars to set the audience), never runs
#    the heavy work. The health-check STAYS on the web (RUN_URL) — see the
#    split note above.
CLOUD_TASKS_LOCATION: "${REGION}"
CLOUD_TASKS_QUEUE: "common-trails-heat-compute-${ENV}"
CLOUD_TASKS_INVOKER_SA: "${TASKS_INVOKER_SA}"
INTERNAL_HEAT_HANDLER_URL: "${WORKER_RUN_URL}/internal/ingest/heat"
CLOUD_TASKS_ARTEFACT_QUEUE: "common-trails-artefact-rebuild-${ENV}"
INTERNAL_ARTEFACT_HANDLER_URL: "${WORKER_RUN_URL}/internal/artefacts/rebuild"
CLOUD_TASKS_STRAVA_WEBHOOK_QUEUE: "common-trails-strava-webhook-${ENV}"
INTERNAL_STRAVA_WEBHOOK_HANDLER_URL: "${WORKER_RUN_URL}/internal/strava/webhook-event"
INTERNAL_STRAVA_HEALTH_HANDLER_URL: "${RUN_URL}/internal/strava/health-check"
# Public callback Strava GETs during the subscription handshake + POSTs events
# to — user-facing custom domain (read by internal_strava_health.py +
# strava_subscribe.py). Dropped by the full-replace before this fix.
STRAVA_WEBHOOK_CALLBACK_URL: "${PUBLIC_URL}/integrations/strava/webhook"
# Strava webhook subscription allow-list (second-line defence in the worker).
# Empty = allow all subscription ids (bootstrap); live value is 349136.
STRAVA_WEBHOOK_SUBSCRIPTION_ID: "${STRAVA_WEBHOOK_SUBSCRIPTION_ID}"
# Heatmap PMTiles publish target + canonical /export/heatmap redirect. BOTH
# point at the SAME bucket on live — the event-driven /internal/artefacts/rebuild
# uploads here and /export/heatmap serves here; splitting them would upload fresh
# tiles to a bucket nothing serves (silently stale heatmap).
HEATMAP_GCS_BUCKET: "common-trails-heatmap-${ENV}"
HEATMAP_ARTEFACTS_BUCKET: "common-trails-heatmap-${ENV}"
# Public base URL under which the community heatmap artefacts are SERVED (single
# source of truth, read by build_pmtiles._public_base_url + email._heatmap_public_base_url).
# An external HTTPS LB + CDN fronts the common-trails-heatmap-${ENV} bucket at this
# first-party domain (fixes ad-blocker/Firefox blocking of storage.googleapis.com +
# a clean share URL). On the SERVICES it makes email.py's "calque" URL tiles.*; the
# freshness pointer's latest_url / raster tiles.json are set by the build-pmtiles JOB,
# which carries the same var (job_env_for). Unset → default is the direct GCS object URL.
HEATMAP_PUBLIC_BASE_URL: "https://tiles.chemins-communs.fr"
# Destination for user-uploaded Strava archives (signed-URL direct-to-GCS, #455).
# The cold-start importer job streams the .zip from here. Bucket already exists.
UPLOADS_BUCKET: "${PROJECT}-common-trails-uploads-${ENV}"
# Event-driven drain: /imports/strava-archive/complete fires a best-effort
# jobs:run against this Cloud Run Job so an upload is ingested promptly instead
# of waiting for the daily backstop scheduler (run_jobs.trigger_ingest_archives_job).
INGEST_ARCHIVES_JOB_NAME: "common-trails-ingest-pending-archives-${ENV}"
# Event-driven heatmap freshness: after the archive drain ingests >=1 activity
# it fires a best-effort jobs:run against this Cloud Run Job so the static
# heatmap-display.pmtiles on GCS is rebuilt promptly (run_jobs.trigger_build_pmtiles_job).
# NOTE: for the ping to fire the DRAIN JOB (ingest-pending-archives) must ALSO
# carry BUILD_PMTILES_JOB_NAME/GCP_PROJECT/GCP_REGION + run.developer on the
# build-pmtiles job — set out-of-band on that job, not on the web service.
BUILD_PMTILES_JOB_NAME: "common-trails-build-pmtiles-${ENV}"
# Max size of an uploaded Strava archive. Default (512 MB) is too small — a real
# active athlete's export is easily 1 GB+. 2 GB admits those; the drain JOB is
# sized to match (8 Gi memory, MAX_ARCHIVE_BYTES set on the job too — the .zip is
# streamed into its tmpfs). The web only reads the object SIZE metadata (no
# download), so this cap costs the mini web service nothing.
MAX_ARCHIVE_BYTES: "2147483648"
# Passwordless magic-link login — the FROM identity for transactional email
# (Resend). Domain chemins-communs.fr verified in Resend 2026-07-14 (MX +
# SPF on 'send', DKIM at resend._domainkey — see docs/email-auth-dns.md), so
# we send from the real domain and deliver to any recipient. Revert to
# "Chemins Communs <onboarding@resend.dev>" only if the domain is unverified.
EMAIL_FROM: "Chemins Communs <no-reply@chemins-communs.fr>"
# DB connection pool — the code default is 20+10=30 connections PER instance
# (backend/app/db/session.py). With two always-on services (WEB min=1 + WORKER)
# under concurrent beta + ingest load that risks exhausting Cloud SQL
# db-f1-micro's ~22 client-connection ceiling. 5+3=8/instance is the sizing the
# session module itself recommends for prod max_instances>=2 (16 total, leaving
# headroom for the Cloud SQL proxy, admin sessions, and migrations). Applied to
# BOTH services here (they share this env-vars-file).
DB_POOL_SIZE: "5"
DB_MAX_OVERFLOW: "3"
YAML

# Secrets (Secret Manager → env). Authoritative: --set-secrets. Secret ids
# match infra/terraform/main.tf (note: strava-webhook-verify-token is lowercased).
SECRETS="JWT_SECRET=JWT_SECRET:latest"
SECRETS="${SECRETS},DATABASE_URL=DATABASE_URL:latest"
SECRETS="${SECRETS},STRAVA_CLIENT_ID=STRAVA_CLIENT_ID:latest"
SECRETS="${SECRETS},STRAVA_CLIENT_SECRET=STRAVA_CLIENT_SECRET:latest"
SECRETS="${SECRETS},STRAVA_TOKEN_ENC_KEY=STRAVA_TOKEN_ENC_KEY:latest"
SECRETS="${SECRETS},STRAVA_WEBHOOK_VERIFY_TOKEN=strava-webhook-verify-token:latest"
# Resend API key for transactional email (magic-link login). Already in Secret
# Manager (version 1). Unset → email.send_email() no-ops (safe, but no login
# email goes out).
SECRETS="${SECRETS},RESEND_API_KEY=RESEND_API_KEY:latest"
# Sentry DSN — error/perf observability (main.py inits sentry_sdk only when set).
# In Secret Manager (2026-08-07). Was previously absent → all capture_exception /
# set_tag across the app were silent no-ops. On the WEB + WORKER services here;
# the drain JOB gets it via job_secrets_for.
SECRETS="${SECRETS},SENTRY_DSN=SENTRY_DSN:latest"

# ── Pre-flight: show account/project + a live env-key DIFF, then confirm ─────
ACTIVE_ACCT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || echo '?')"
CUR_PROJECT="$(gcloud config get-value project 2>/dev/null || echo '?')"

echo "──────────────────────────────────────────────────────────────────────"
echo " Common Trails — PROD deploy (SSOT) — light WEB + heavy WORKER split"
echo "   account : ${ACTIVE_ACCT}   (must be a common-trails deployer, NOT okeiro)"
echo "   project : ${CUR_PROJECT}   (target: ${PROJECT})"
echo "   web     : ${WEB_SERVICE}     512Mi min=1 (warm) @ ${REGION}"
echo "   worker  : ${WORKER_SERVICE}  2Gi   min=0 (scale-to-zero) @ ${REGION}"
echo "   image   : ${IMAGE}   (same image → both services)"
echo "   cloudsql: ${CLOUDSQL_INSTANCE}"
echo "   SA      : ${API_SA}"
echo "──────────────────────────────────────────────────────────────────────"

# Best-effort env-key diff vs each LIVE service — this is what surfaces a drop
# (e.g. live is missing CLOUD_TASKS_STRAVA_WEBHOOK_QUEUE). Non-fatal if the
# describe fails (first deploy / no read access — the worker won't exist on the
# very first split deploy, which is expected).
NEW_KEYS="$(grep -E '^[A-Z0-9_]+:' "$ENV_FILE" | cut -d: -f1 | sort)"
NEW_KEYS="$(printf '%s\n%s\n' "$NEW_KEYS" "$(echo "$SECRETS" | tr ',' '\n' | cut -d= -f1)" | sort -u)"
env_key_diff() {  # $1 = service name
  local svc="$1" live added removed
  if live="$(gcloud run services describe "$svc" --region="$REGION" \
        --format='value(spec.template.spec.containers[0].env[].name)' 2>/dev/null | tr ';' '\n' | tr -d ' ' | sort -u)"; then
    echo "Env-var key diff vs live ${svc}:"
    added="$(comm -23 <(echo "$NEW_KEYS") <(echo "$live") || true)"
    removed="$(comm -13 <(echo "$NEW_KEYS") <(echo "$live") || true)"
    [[ -n "$added" ]]   && echo "$added"   | sed 's/^/   + (adds)   /' || echo "   (no keys added)"
    [[ -n "$removed" ]] && echo "$removed" | sed 's/^/   - (removes) /' || echo "   (no keys removed)"
    echo "   NOTE: '+ adds' that are the webhook/tasks wiring == the exact drift this script fixes."
  else
    echo "(could not describe ${svc} — skipping its env-key diff; expected if it does not exist yet)"
  fi
}
env_key_diff "$WEB_SERVICE"
env_key_diff "$WORKER_SERVICE"
echo "Full env set to be applied to BOTH services (authoritative):"
grep -E '^[A-Z0-9_]+:' "$ENV_FILE" | sed 's/^/   /'
echo "   + secrets: ${SECRETS}"
echo "──────────────────────────────────────────────────────────────────────"

if [[ "${DEPLOY_YES:-}" != "1" ]]; then
  read -r -p "Apply this deploy to PROD? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "Aborted."; exit 1; }
fi

# ── Deploy one Cloud Run service ─────────────────────────────────────────────
# Shared knobs across both services (cost-tuned on db-f1-micro): min=0
# (cold-start accepted — see the webhook caveat in docs/prod-deploy.md), max=1
# (the micro tier can't sustain more DB connections), gen2 (required for the GCS
# volume mount), 900s timeout (cold-start GPX upload + heavy worker tasks; the
# old 60s ceiling 504'd — 2026-05-18 incident), same image/env/secrets/SQL/
# volume. The ONLY per-service difference is --memory (arg $2).
#
# Both stay --allow-unauthenticated (identical to the pre-split single service):
# the /internal/* handlers are guarded at the APP layer by verify_oidc_token
# (audience + signer-email checks), NOT by Cloud Run IAM. This keeps the exact
# security posture prod runs today — the split only changes the host the token
# audience is bound to. (Hardening the worker to --no-allow-unauthenticated +
# run.invoker for the tasks-invoker SA is a compatible follow-up: the audience
# is already the worker url, so Cloud Run IAM would accept the same token.)
deploy_service() {  # $1 = service name, $2 = memory (e.g. 512Mi | 2Gi), $3 = human label, $4 = min-instances (default 0)
  local svc="$1" mem="$2" label="$3" min="${4:-0}"
  echo "→ Deploying ${label} ${svc} (memory=${mem}) → ${IMAGE} ..."
  gcloud run deploy "$svc" \
    --project="$PROJECT" \
    --region="$REGION" \
    --image="$IMAGE" \
    --platform=managed \
    --service-account="$API_SA" \
    --allow-unauthenticated \
    --execution-environment=gen2 \
    --cpu=1 \
    --memory="$mem" \
    --min-instances="${min}" \
    --max-instances=1 \
    --concurrency=10 \
    --cpu-boost \
    --timeout=900 \
    --port=8787 \
    --add-cloudsql-instances="$CLOUDSQL_INSTANCE" \
    --add-volume="name=data-gcs,type=cloud-storage,bucket=${DATA_BUCKET},readonly=true" \
    --add-volume-mount="volume=data-gcs,mount-path=/data" \
    --env-vars-file="$ENV_FILE" \
    --set-secrets="$SECRETS" \
    --startup-probe="httpGet.path=/healthz,httpGet.port=8787,initialDelaySeconds=2,periodSeconds=5,failureThreshold=24" \
    --liveness-probe="httpGet.path=/healthz,httpGet.port=8787,timeoutSeconds=30,periodSeconds=60,failureThreshold=10"
}

# ── 1. Deploy the WEB (public, light 512Mi) service ──────────────────────────
# 512Mi is safe because every heavy in-request path is deferred off the web:
# heat compute + PMTiles rebuild + Strava webhook fetch/ingest all run on the
# WORKER via Cloud Tasks; large archive uploads go direct-to-GCS (signed URL,
# #455) + the cold-start ingest-pending-archives JOB. See the PR audit for the
# residual inline export-build path (a future worker candidate).
echo "[1/3] Deploying WEB service (512Mi) ..."
deploy_service "$WEB_SERVICE" "512Mi" "WEB (public, light)" 1  # min=1: warm (kills the ~43s cold-start on a cheap 512Mi instance)

# ── 2. Deploy the WORKER (heavy /internal/*, 2Gi, scale-to-zero) service ─────
# Same image + env; only the memory differs. Cloud Tasks (heat / artefact /
# webhook queues) POST here — the INTERNAL_*_HANDLER_URL vars in the env-file
# already point at this service's url, so the OIDC audience matches byte-for-
# byte on both the enqueue (web) and verify (worker) sides.
echo "[2/3] Deploying WORKER service (2Gi, scale-to-zero) ..."
deploy_service "$WORKER_SERVICE" "2Gi" "WORKER (heavy /internal/*)" 0  # min=0: scale-to-zero (heavy jobs only, no idle cost)

# ── Per-job load-bearing env assertion (audit gap #6) ────────────────────────
# A Cloud Run JOB runs the same api image as the services but carries its OWN
# env, set out-of-band → it DRIFTS. The --env-vars-file above only touches the
# two SERVICES, so we assert the env each job actually needs HERE, declaratively,
# with `--update-env-vars` (MERGE — leaves DATABASE_URL & friends untouched).
# NEVER `--set-env-vars` (full replace → wipes the rest, the trap this whole
# script exists to kill). Values MUST match the live env-vars-file block above.
#
# Implemented as a `case` (NOT a `declare -A` associative array) because the
# script's shebang resolves to whatever `env bash` finds — and macOS system
# bash is 3.2, which has no associative arrays. Echoes the comma-separated
# KEY=VALUE list to merge for $1, or empty for a job that carries no app env.
job_env_for() {
  case "$1" in
    # The archive-drain worker: runs OSM matching + fires the post-drain PMTiles
    # rebuild. It needs Overpass OFF (else foreign-tile Overpass calls crawl —
    # the 2026-07-20 incident), the uploads bucket to stream the .zip from, GCP
    # project/region for the jobs:run dispatch, the 2 GB archive cap, and the
    # build-pmtiles job name to ping. (DATABASE_URL etc. stay secrets, untouched.)
    #
    # OSM-cache sizing for the 8 Gi drain job (2026-07-20 perf): the code
    # defaults (50 tiles / 200 grids / 10k segs) are tuned for the 512 Mi web
    # service; on 8 Gi they thrash because the archive hops between regions
    # (Montpellier ↔ Spain) and every miss reloads 17k–65k segments from the DB
    # (~1–2 min/activity). We spatial-order the archive now AND size the caches up:
    #   • OSM_TILE_CACHE_MAX=800   — segment cache. ~34 KB/tile France-average,
    #       ~4 MB/tile dense-urban WORST case → 800 tiles ≤ 3.2 GB worst, ~0.5 GB
    #       realistic (a metro is ~200–400 z14 tiles).
    #   • OSM_GRID_CACHE_MAX=150   — grid cache. Each entry is an INDEX over the
    #       already-cached segment objects (~8 MB for a big multi-tile grid, NOT
    #       the 50 MB in the code comment — that counts the shared segments) →
    #       ≤ 1.2 GB worst.
    #   • OSM_GRID_CACHE_MAX_SEGS_PER_ENTRY=200000 — let big multi-tile grids
    #       cache their (cheap) index instead of rebuilding every call.
    # Worst-case budget: 3.2 (segs) + 1.2 (grids) + 2.0 (a MAX_ARCHIVE_BYTES .zip
    # in tmpfs) + ~1.0 (python/ingest) ≈ 7.4 GB < 8 GiB. Comfortable at the
    # realistic hit rate. (rebuild-heatmap is also 8 Gi and could take the same —
    # left conservative here; the import-strava job is 512 Mi and MUST NOT.)
    "common-trails-ingest-pending-archives-${ENV}")
      # + the RAW-display env (2026-07-29 pivot): the drain runs ingest_activity,
      # which under raw MUST skip the matched OSM-match + heat_edges write (the
      # substrate is dropped). Without this the drain path would still run
      # wasteful matched work. Same values as the service env-file.
      # + EMAIL_FROM: the drain sends the "your traces are on the map" email
      # (render_archive_done_email). Without it, EMAIL_FROM defaults to Resend's
      # onboarding@resend.dev test sender (not our verified domain). The
      # RESEND_API_KEY SECRET is asserted separately in job_secrets_for.
      echo "OVERPASS_ENABLED=false,UPLOADS_BUCKET=${PROJECT}-common-trails-uploads-${ENV},GCP_PROJECT=${PROJECT},GCP_REGION=${REGION},MAX_ARCHIVE_BYTES=2147483648,BUILD_PMTILES_JOB_NAME=common-trails-build-pmtiles-${ENV},OSM_TILE_CACHE_MAX=800,OSM_GRID_CACHE_MAX=150,OSM_GRID_CACHE_MAX_SEGS_PER_ENTRY=200000,HEATMAP_DISPLAY_SOURCE=raw,HEATMAP_MIN_USERS=1,TRACE_MASK_METERS=200,HEATMAP_RAW_BBOX=-12;34;32;62,EMAIL_FROM=Chemins Communs <no-reply@chemins-communs.fr>" ;;
    # Every other job that does OSM matching / ingestion must ALSO keep Overpass
    # off (same foreign-tile slowdown if it ran with OVERPASS_ENABLED absent→true).
    "common-trails-import-strava-${ENV}"|"common-trails-rebuild-heatmap-${ENV}"|"common-trails-resync-strava-${ENV}"|"common-trails-group-edges-osm-${ENV}")
      echo "OVERPASS_ENABLED=false" ;;
    # The re-sync reminder emailer: EMAIL_FROM so the reminder is sent from our
    # verified domain (not Resend's onboarding sender). RESEND_API_KEY is a
    # SECRET (job_secrets_for). No OSM work → Overpass irrelevant, kept off.
    "common-trails-resync-reminder-${ENV}")
      echo "OVERPASS_ENABLED=false,EMAIL_FROM=Chemins Communs <no-reply@chemins-communs.fr>" ;;
    # The PMTiles builder MUST carry the raw-display env (2026-07-29 pivot) —
    # the env-vars-file above only reaches the SERVICES, and this JOB is what
    # actually writes heatmap-display.pmtiles to GCS. Same values as the service
    # block. Without this it would keep building the MATCHED aggregate.
    # + HEATMAP_PUBLIC_BASE_URL: this JOB writes the freshness pointer
    # (latest_url — the URL the client resolves the pmtiles THROUGH) AND the
    # raster tiles.json template. It MUST carry the tiles.* base so both become
    # the first-party CDN domain. The env-vars-file only reaches the SERVICES.
    "common-trails-build-pmtiles-${ENV}")
      echo "HEATMAP_DISPLAY_SOURCE=raw,HEATMAP_MIN_USERS=1,TRACE_MASK_METERS=200,HEATMAP_RAW_BBOX=-12;34;32;62,OVERPASS_ENABLED=false,HEATMAP_RASTER_PYRAMID=true,HEATMAP_PUBLIC_BASE_URL=https://tiles.chemins-communs.fr" ;;
    *)
      echo "" ;;
  esac
}

# ── Per-job memory assertion (audit gap: job memory was set out-of-band) ──────
# Job memory was only ever set with a one-off `gcloud run jobs update --memory`,
# so a job RECREATE (or any tool that rewrites the job spec) silently reverts it
# to the Cloud Run default. Assert the load-bearing size HERE, declaratively, so
# it is durable across recreates. Echoes the memory string for $1, or empty to
# leave a job at whatever it currently carries. Same `case` (not `declare -A`)
# reason as job_env_for: macOS system bash is 3.2, no associative arrays.
job_memory_for() {
  case "$1" in
    # The archive-drain worker: the ≤2 GB .zip (MAX_ARCHIVE_BYTES) streams into
    # the job's tmpfs = RAM, so it MUST stay large. 8Gi is justified/measured.
    "common-trails-ingest-pending-archives-${ENV}")
      echo "8Gi" ;;
    # The raw-trace PMTiles builder: the bounded, server-side-cursor streaming
    # build (#506/#516) is memory-bounded by occupied geography, not by point
    # count. 4Gi (was 2Gi) is belt-and-suspenders headroom for tippecanoe now
    # that the local-count regrade can grow the feature file (the #621 regrade
    # OOM-killed a 2Gi job at 1.3 M features; fix/regrade-oom-and-pool bounds the
    # split, and this raises the ceiling too — the drain job is already 8Gi).
    "common-trails-build-pmtiles-${ENV}")
      echo "4Gi" ;;
    # import-strava / strava-subscribe / resync-strava: light Strava sync jobs,
    # 512Mi each (their current live sizing, captured via `describe`).
    "common-trails-import-strava-${ENV}"|"common-trails-strava-subscribe-${ENV}"|"common-trails-resync-strava-${ENV}")
      echo "512Mi" ;;
    # The re-sync reminder emailer: one indexed query + N Resend POSTs, trivial.
    "common-trails-resync-reminder-${ENV}")
      echo "512Mi" ;;
    *)
      echo "" ;;
  esac
}

# ── Per-job SECRET assertion (audit gap 2026-08-06) ──────────────────────────
# Jobs get secrets at CREATE via --set-secrets and the sync loop only ever
# touched --image/--memory/--update-env-vars — so a secret NEEDED by a job but
# not set at create is silently absent. The drain job sends the "your traces are
# on the map" email (send_email) but had no RESEND_API_KEY → every send logged
# "RESEND_API_KEY unset" and no mail went out. Assert it here (MERGES with the
# job's existing DATABASE_URL secret; never replaces).
job_secrets_for() {
  case "$1" in
    "common-trails-ingest-pending-archives-${ENV}")
      # RESEND for the "traces on the map" email; SENTRY_DSN so the drain's
      # capture_exception calls actually report (else silent no-op).
      echo "RESEND_API_KEY=RESEND_API_KEY:latest,SENTRY_DSN=SENTRY_DSN:latest" ;;
    "common-trails-build-pmtiles-${ENV}")
      echo "SENTRY_DSN=SENTRY_DSN:latest" ;;
    # The re-sync reminder emailer sends via Resend → needs the key (else every
    # send logs "RESEND_API_KEY unset" and no mail goes out, like the drain bug).
    "common-trails-resync-reminder-${ENV}")
      echo "RESEND_API_KEY=RESEND_API_KEY:latest" ;;
    *)
      echo "" ;;
  esac
}

# ── 3. Sync EVERY job image to the same tag + assert its env ─────────────────
echo "[3/3] Syncing Cloud Run Job images to ${IMAGE_TAG} + asserting job env ..."
sync_failures=0
for job in "${JOBS[@]}"; do
  if ! gcloud run jobs describe "$job" --project="$PROJECT" --region="$REGION" >/dev/null 2>&1; then
    echo "   ! ${job}: does not exist yet — SKIPPED (create it, then it'll sync next deploy)"
    continue
  fi
  if gcloud run jobs update "$job" --project="$PROJECT" --region="$REGION" --image="$IMAGE" >/dev/null 2>&1; then
    echo "   ✓ ${job} (image)"
  else
    echo "   ✗ ${job}: image update FAILED" >&2
    sync_failures=$((sync_failures + 1))
  fi
  # Assert the job's memory so a recreate can't silently revert it to the Cloud
  # Run default (--memory only touches the size, never the env or secrets).
  job_mem="$(job_memory_for "$job")"
  if [[ -n "$job_mem" ]]; then
    if gcloud run jobs update "$job" --project="$PROJECT" --region="$REGION" --memory="$job_mem" >/dev/null 2>&1; then
      echo "     ↳ memory asserted: ${job_mem}"
    else
      echo "   ✗ ${job}: memory assertion FAILED (${job_mem})" >&2
      sync_failures=$((sync_failures + 1))
    fi
  fi
  # Assert the load-bearing env for jobs that carry it (MERGE, never replace).
  job_env="$(job_env_for "$job")"
  if [[ -n "$job_env" ]]; then
    if gcloud run jobs update "$job" --project="$PROJECT" --region="$REGION" --update-env-vars="$job_env" >/dev/null 2>&1; then
      echo "     ↳ env asserted: ${job_env}"
    else
      echo "   ✗ ${job}: env assertion FAILED (${job_env})" >&2
      sync_failures=$((sync_failures + 1))
    fi
  fi
  # Assert secrets a job NEEDS but wasn't created with (MERGE via --update-secrets,
  # never --set-secrets which would wipe DATABASE_URL). e.g. the drain's RESEND_API_KEY.
  job_secrets="$(job_secrets_for "$job")"
  if [[ -n "$job_secrets" ]]; then
    if gcloud run jobs update "$job" --project="$PROJECT" --region="$REGION" --update-secrets="$job_secrets" >/dev/null 2>&1; then
      echo "     ↳ secrets asserted: ${job_secrets%%=*}"
    else
      echo "   ✗ ${job}: secret assertion FAILED (${job_secrets%%=*})" >&2
      sync_failures=$((sync_failures + 1))
    fi
  fi
done

echo "──────────────────────────────────────────────────────────────────────"
if [[ "$sync_failures" -gt 0 ]]; then
  echo "DEPLOY DONE with ${sync_failures} job-sync failure(s) — investigate above." >&2
  exit 1
fi
echo "DEPLOY COMPLETE — ${WEB_SERVICE} (512Mi) + ${WORKER_SERVICE} (2Gi) + all jobs on ${IMAGE_TAG}."
echo "Verify web:    curl -fsS ${PUBLIC_URL}/healthz            (expect 200)"
echo "Verify worker: curl -fsS ${WORKER_RUN_URL}/healthz        (expect 200 — worker is public but only Cloud Tasks POST /internal/*)"
echo
echo "OIDC audience: NO queue-level or IAM change is needed. The task's OIDC"
echo "  audience is set per-task by the WEB from INTERNAL_*_HANDLER_URL (now the"
echo "  WORKER url), and the WORKER verifies against the SAME env var — they move"
echo "  together. Both services are --allow-unauthenticated, so the tasks-invoker"
echo "  SA needs no run.invoker binding (app-layer verify_oidc_token is the gate)."
echo "  Post-deploy end-to-end check: docs/prod-deploy.md §8 'Microservice split'."
