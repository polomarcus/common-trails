.PHONY: up down logs test test-slow test-golden heatmap-rebuild heat-quality backend frontend dev dev-frontend lint format dfci-download dfci-extract dfci-upload pmtiles heat-edges heatmap-deploy heatmap-status gcp-precheck prod-import-osm prod-import-osm-south import-osm-roads-local heatmap-clean-rebuild import-trails-pbf prod-import-trails-south prod-recompute-elevation-gain prod-group-edges-osm-job

## GOLDEN RULE: any GCP action must verify BOTH gcloud config AND ADC
## (Application Default Credentials — what terraform/gsutil/google-cloud
## SDKs actually use) are on the personal account
## (paleclercq@gmail.com), NOT the work account
## (paul.leclercq@okeiro.com).
##
## ADC is the gap that bit us once: ``gcloud config`` was right but
## ADC was still on the work account, terraform tried to read GCS via
## ADC, hit a 403, and almost-but-didn't-quite mutated the wrong
## project. Now we check both. Don't trust ``gcloud config`` alone.
##
## Chain ``gcp-precheck`` as a dependency on ANY target that touches
## GCP. Fails loudly with a non-zero exit if any of (gcloud account,
## gcloud project, ADC account) isn't the expected pair. Don't try
## to switch — let Paul fix it manually.
gcp-precheck:
	@account=$$(gcloud config get-value account 2>/dev/null); \
	project=$$(gcloud config get-value project 2>/dev/null); \
	if [ "$$account" != "paleclercq@gmail.com" ]; then \
	    echo "❌ ABORT: gcloud account is '$$account' (expected paleclercq@gmail.com)"; \
	    echo "   Run: gcloud config set account paleclercq@gmail.com"; \
	    exit 1; \
	fi; \
	if [ "$$project" != "common-trails" ]; then \
	    echo "❌ ABORT: gcloud project is '$$project' (expected common-trails)"; \
	    echo "   Run: gcloud config set project common-trails"; \
	    exit 1; \
	fi; \
	adc_token=$$(gcloud auth application-default print-access-token 2>/dev/null); \
	if [ -z "$$adc_token" ]; then \
	    echo "❌ ABORT: ADC missing — run: gcloud auth application-default login"; \
	    exit 1; \
	fi; \
	adc_email=$$(curl -sf "https://www.googleapis.com/oauth2/v3/userinfo?access_token=$$adc_token" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('email',''))" 2>/dev/null); \
	if [ "$$adc_email" != "paleclercq@gmail.com" ]; then \
	    echo "❌ ABORT: ADC email is '$$adc_email' (expected paleclercq@gmail.com)"; \
	    echo "   Run: gcloud auth application-default login (pick paleclercq@gmail.com)"; \
	    echo "   ADC is what terraform/gsutil/google-cloud SDKs actually use —"; \
	    echo "   gcloud config being right is NOT enough."; \
	    exit 1; \
	fi; \
	echo "✅ GCP context: gcloud=$$account / $$project / ADC=$$adc_email"

## Import a single Geofabrik region into prod osm_road_edges by running
## the existing CLI in the LOCAL backend container, with DATABASE_URL
## redirected at prod via cloud-sql-proxy. Cheaper than Cloud Run Jobs:
## no per-run vCPU bill, no egress (ADC + cloud-sql-proxy use Google's
## internal mesh), and the container already has the PBF cached in
## ``/data/<region>.osm.pbf`` so we skip the Geofabrik re-download.
##
## Usage: make prod-import-osm REGION=occitanie
##
## Caveat: parsing happens on YOUR laptop. INSERT batches go over
## the proxy → db-f1-micro. Expect ~30 min for Occitanie (262 MB
## PBF, 6 M segments). Idempotent — re-running with the same PBF is
## safe (ON CONFLICT DO NOTHING).
prod-import-osm: gcp-precheck
	@if [ -z "$(REGION)" ]; then echo "❌ Set REGION=occitanie|paca|… (see app/cli/import_osm_roads.py)"; exit 1; fi
	@docker compose ps backend --format json 2>/dev/null | grep -q '"State":"running"' || (echo "❌ local backend container not running — make up"; exit 1)
	@DB_PWD=$$(gcloud secrets versions access latest --secret=DATABASE_URL --project=common-trails 2>/dev/null | sed -E 's|.*://[^:]+:||; s|@.*||') ; \
	if [ -z "$$DB_PWD" ]; then echo "❌ couldn't read db password from the DATABASE_URL secret (gcloud auth as paleclercq@gmail.com?)"; exit 1; fi ; \
	echo "→ starting cloud-sql-proxy on host port 5433…" ; \
	pkill -f "cloud-sql-proxy.*common-trails-prod" 2>/dev/null || true ; \
	cloud-sql-proxy --port 5433 common-trails:europe-west1:common-trails-prod > /tmp/csqlp-prod.log 2>&1 & \
	PROXY_PID=$$! ; \
	sleep 4 ; \
	echo "→ importing $(REGION) (DATABASE_URL → 127.0.0.1:5433/common_trails)…" ; \
	docker compose exec -T \
	    -e DATABASE_URL="postgresql://api:$$DB_PWD@host.docker.internal:5433/common_trails" \
	    -e SKIP_OSM_FETCH=true \
	    backend python -m app.cli.import_osm_roads $(REGION) || EXIT=$$? ; \
	kill $$PROXY_PID 2>/dev/null || true ; \
	exit $${EXIT:-0}

## Import South-France PBFs sequentially into prod. Covers everywhere
## the soft-launch friends are likely to ride:
##   - occitanie  (Languedoc-Roussillon, Montpellier-Toulouse axis)
##   - paca       (Marseille, Nice, Provence, Alpes du Sud)
##   - ara        (Rhône-Alpes — Lyon, Annecy, Grenoble, Alpes du Nord)
##   - auvergne   (Massif Central — Clermont, Cantal)
##
## ~30 min each on db-f1-micro = ~2 h total. Idempotent on retry —
## the CLI uses ON CONFLICT DO NOTHING so re-running is safe. Sequential
## (not parallel) to keep db-f1-micro from OOMing on concurrent INSERTs.
##
## ⚠️ DEPRECATED — measured ~330 ways/sec on 2026-05-15 over cloud-sql-proxy,
## projecting whole south France at 24-30 h. The bottleneck is the
## per-COPY-batch network round-trip. Prefer ``prod-import-osm-job``
## which runs INSIDE GCP (db-internal connection): 10-30× faster,
## ~$0.04 per region.
prod-import-osm-south: gcp-precheck
	$(MAKE) prod-import-osm REGION=occitanie
	$(MAKE) prod-import-osm REGION=paca
	$(MAKE) prod-import-osm REGION=ara
	$(MAKE) prod-import-osm REGION=auvergne
	@echo "✅ South + Alps + Massif Central imported. Sanity check: gcloud sql connect …"

## ⚠️ DESTRUCTIVE — wipe prod heat_edges, heat_edge_contributors, activities.
## Preserves users, integration_accounts, oauth_states (so JWTs + Strava
## links survive the wipe). Use before a full re-ingest from clean GPX
## source files, when the existing heat_edges are known-broken (e.g. all
## grid-fallback due to missing OSM data at ingest time).
##
## Requires CONFIRM=YES. Without it, prints what would be wiped and exits.
##
##   make prod-wipe-user-data                    # dry-run preview
##   make prod-wipe-user-data CONFIRM=YES        # actually wipe
##
## Implementation note: the entire body MUST be one shell invocation
## (one ``\`` per line, no blank lines splitting it) so a non-CONFIRM
## ``exit 0`` actually short-circuits the whole recipe. A previous
## version split the dry-run check from the wipe across two ``@``
## recipe lines — Make treats each ``@`` as a separate shell, so the
## first shell's ``exit 0`` returned 0 to Make, and Make happily ran
## the second shell, executing the wipe even without CONFIRM=YES.
## This single-shell form prevents that.
prod-wipe-user-data: gcp-precheck
	@if [ "$(CONFIRM)" != "YES" ]; then \
	    echo "⚠  Dry-run. Would TRUNCATE: heat_edges, heat_edge_contributors, activities"; \
	    echo "   Preserved: users, integration_accounts, oauth_states, route_*, trip_*"; \
	    echo "   Run with CONFIRM=YES to actually wipe."; \
	    exit 0; \
	fi ; \
	DB_PWD=$$(gcloud secrets versions access latest --secret=DATABASE_URL --project=common-trails 2>/dev/null | sed -E 's|.*://[^:]+:||; s|@.*||') ; \
	if [ -z "$$DB_PWD" ]; then echo "❌ couldn't read db password from the DATABASE_URL secret"; exit 1; fi ; \
	echo "→ starting cloud-sql-proxy on host port 5433…" ; \
	pkill -f "cloud-sql-proxy.*common-trails-prod" 2>/dev/null || true ; \
	cloud-sql-proxy --port 5433 common-trails:europe-west1:common-trails-prod > /tmp/csqlp-prod.log 2>&1 & \
	PROXY_PID=$$! ; sleep 4 ; \
	echo "→ TRUNCATE on prod heat_edges + heat_edge_contributors + activities…" ; \
	PGPASSWORD="$$DB_PWD" psql -h 127.0.0.1 -p 5433 -U api -d common_trails -c "BEGIN; TRUNCATE TABLE heat_edge_contributors RESTART IDENTITY; TRUNCATE TABLE heat_edges RESTART IDENTITY CASCADE; TRUNCATE TABLE activities RESTART IDENTITY CASCADE; COMMIT;" || EXIT=$$? ; \
	kill $$PROXY_PID 2>/dev/null || true ; \
	exit $${EXIT:-0}

## Re-run group_edges_osm on prod via cloud-sql-proxy. Backfills
## osm_way_id on heat_edges that drifted to grid-fallback during ingest.
## Useful after a fresh OSM import populates osm_road_edges with new
## coverage — existing heat_edges in those tiles get their osm_way_id
## set so PMTiles aggregation collapses them onto smooth OSM ways.
##
## Idempotent. ~5-30 min depending on heat_edges count.
prod-group-edges-osm: gcp-precheck
	@docker compose ps backend --format json 2>/dev/null | grep -q '"State":"running"' || (echo "❌ local backend container not running — make up"; exit 1)
	@DB_PWD=$$(gcloud secrets versions access latest --secret=DATABASE_URL --project=common-trails 2>/dev/null | sed -E 's|.*://[^:]+:||; s|@.*||') ; \
	if [ -z "$$DB_PWD" ]; then echo "❌ couldn't read db password from the DATABASE_URL secret"; exit 1; fi ; \
	echo "→ starting cloud-sql-proxy on host port 5433…" ; \
	pkill -f "cloud-sql-proxy.*common-trails-prod" 2>/dev/null || true ; \
	cloud-sql-proxy --port 5433 common-trails:europe-west1:common-trails-prod > /tmp/csqlp-prod.log 2>&1 & \
	PROXY_PID=$$! ; sleep 4 ; \
	echo "→ running group_edges_osm on prod…" ; \
	docker compose exec -T \
	    -e DATABASE_URL="postgresql://api:$$DB_PWD@host.docker.internal:5433/common_trails" \
	    backend python -m app.cli.group_edges_osm || EXIT=$$? ; \
	kill $$PROXY_PID 2>/dev/null || true ; \
	exit $${EXIT:-0}

## Re-apply Strava-style D+ smoothing to existing prod activities.
## After PR #259 introduced the rolling median + 3 m noise threshold in
## ``gpx.py:_smoothed_elevation_gain``, rows uploaded before that PR
## still carry the OLD inflated naive-sum D+. This iterates prod
## ``activities`` in batches, re-reads the z from stored
## ``geometry_geojson`` coords, and UPDATEs only ``elevation_gain_m``.
##
## Crouzet-safe — never reads/writes ``geometry_geojson``. Idempotent
## (deterministic smoothing).
##
## DRY_RUN=1 → preview only; USER_ID=<uuid> → scope to one user;
## BATCH=200 → tune rows-per-commit for db-f1-micro.
##
## Usage:
##   make prod-recompute-elevation-gain                    # full run
##   make prod-recompute-elevation-gain DRY_RUN=1          # preview
##   make prod-recompute-elevation-gain USER_ID=abc-...    # one user
prod-recompute-elevation-gain: gcp-precheck
	@docker compose ps backend --format json 2>/dev/null | grep -q '"State":"running"' || (echo "❌ local backend container not running — make up"; exit 1)
	@DB_PWD=$$(gcloud secrets versions access latest --secret=DATABASE_URL --project=common-trails 2>/dev/null | sed -E 's|.*://[^:]+:||; s|@.*||') ; \
	if [ -z "$$DB_PWD" ]; then echo "❌ couldn't read db password from the DATABASE_URL secret"; exit 1; fi ; \
	echo "→ starting cloud-sql-proxy on host port 5433…" ; \
	pkill -f "cloud-sql-proxy.*common-trails-prod" 2>/dev/null || true ; \
	cloud-sql-proxy --port 5433 common-trails:europe-west1:common-trails-prod > /tmp/csqlp-prod.log 2>&1 & \
	PROXY_PID=$$! ; sleep 4 ; \
	ARGS="" ; \
	[ -n "$(DRY_RUN)" ] && ARGS="$$ARGS --dry-run" ; \
	[ -n "$(USER_ID)" ] && ARGS="$$ARGS --user-id $(USER_ID)" ; \
	[ -n "$(BATCH)" ] && ARGS="$$ARGS --batch $(BATCH)" ; \
	echo "→ running recompute_elevation_gain on prod ($$ARGS)…" ; \
	docker compose exec -T \
	    -e DATABASE_URL="postgresql://api:$$DB_PWD@host.docker.internal:5433/common_trails" \
	    backend python -m app.cli.recompute_elevation_gain $$ARGS || EXIT=$$? ; \
	kill $$PROXY_PID 2>/dev/null || true ; \
	exit $${EXIT:-0}

## Re-ingest local data/gpx/*.gpx into prod activities for the
## specified user. Use this AFTER prod-wipe-user-data + prod-import-osm
## so each activity ingests with OSM matching available — every
## heat_edge gets a proper osm_way_id, no grid-fallback spaghetti.
##
## Required: USER_ID=<uuid> (same as prod-import-strava).
## SPORT=<gravel|mtb|road|offroad|running> defaults to gravel.
##
## Idempotent — file_hash dedup.
prod-import-gpx: gcp-precheck
	@if [ -z "$(USER_ID)" ]; then echo "❌ Set USER_ID=<uuid>"; exit 1; fi
	@test -d data/gpx || (echo "❌ data/gpx/ folder missing"; exit 1)
	@docker compose ps backend --format json 2>/dev/null | grep -q '"State":"running"' || (echo "❌ local backend container not running — make up"; exit 1)
	@DB_PWD=$$(gcloud secrets versions access latest --secret=DATABASE_URL --project=common-trails 2>/dev/null | sed -E 's|.*://[^:]+:||; s|@.*||') ; \
	if [ -z "$$DB_PWD" ]; then echo "❌ couldn't read db password from the DATABASE_URL secret"; exit 1; fi ; \
	echo "→ starting cloud-sql-proxy on host port 5433…" ; \
	pkill -f "cloud-sql-proxy.*common-trails-prod" 2>/dev/null || true ; \
	cloud-sql-proxy --port 5433 common-trails:europe-west1:common-trails-prod > /tmp/csqlp-prod.log 2>&1 & \
	PROXY_PID=$$! ; sleep 4 ; \
	SPORT_ARG="$${SPORT:-gravel}" ; \
	echo "→ ingesting data/gpx/*.gpx into prod (user=$(USER_ID), sport=$$SPORT_ARG)…" ; \
	docker compose exec -T \
	    -e DATABASE_URL="postgresql://api:$$DB_PWD@host.docker.internal:5433/common_trails" \
	    backend python -m app.cli.import_gpx_folder --folder /data/gpx --user-email $(USER_ID) --sport $$SPORT_ARG || EXIT=$$? ; \
	kill $$PROXY_PID 2>/dev/null || true ; \
	exit $${EXIT:-0}

## ⚠ Long-running. Full prod heatmap rebuild from clean state:
##   1. Wipe user heat_edges + activities (CONFIRM=YES required)
##   2. Import OSM PBFs for south France (~2 h via cloud-sql-proxy)
##   3. Re-ingest data/gpx/ (~30 min)
##   4. group_edges_osm (~5 min)
## After this, the next ingest event will trigger build_pmtiles via
## Cloud Tasks. You can also force-rebuild via the /internal/artefacts/
## rebuild endpoint.
##
## Expected total: 3-4 h. Don't run on a flaky network — cloud-sql-proxy
## drops add hours.
prod-rebuild-from-gpx: gcp-precheck
	@if [ "$(CONFIRM)" != "YES" ]; then \
	    echo "⚠  Dry-run. This would:"; \
	    echo "   1. TRUNCATE heat_edges, heat_edge_contributors, activities on prod"; \
	    echo "   2. Run prod-import-osm-south (Occitanie + PACA + ARA + Auvergne, ~2h)"; \
	    echo "   3. Re-ingest data/gpx/ (~30 min) — needs USER_ID=<uuid>"; \
	    echo "   4. Run prod-group-edges-osm (~5 min)"; \
	    echo "   Run with CONFIRM=YES USER_ID=<uuid> to actually do it."; \
	    exit 0; \
	fi ; \
	if [ -z "$(USER_ID)" ]; then echo "❌ Set USER_ID=<uuid> for the GPX ingest step"; exit 1; fi ; \
	$(MAKE) prod-wipe-user-data CONFIRM=YES && \
	$(MAKE) prod-import-osm-south && \
	$(MAKE) prod-import-gpx USER_ID=$(USER_ID) && \
	$(MAKE) prod-group-edges-osm && \
	echo "✅ Rebuild complete. PMTiles will refresh on next ingest event via Cloud Tasks."

## Run group_edges_osm via Cloud Run Job (10-30× faster than the proxy
## path because the job runs in the same region as Cloud SQL — kills
## the 30-50 ms RTT per row that dominated the local-proxy run).
##
## Use this instead of ``prod-group-edges-osm`` for production rebuilds.
## The local-proxy target stays as a debugging convenience.
##
## Idempotent. Expected runtime <30 min vs 7+ h via proxy.
##
##   make prod-group-edges-osm-job          # full table
##   make prod-group-edges-osm-job BBOX=3.7,43.5,4.1,43.8   # scoped
prod-group-edges-osm-job: gcp-precheck
	@echo "→ executing Cloud Run Job common-trails-group-edges-osm-$(or $(ENV),prod)…"
	@ARGS_FLAG="" ; \
	[ -n "$(BBOX)" ] && ARGS_FLAG="--args=--bbox,$(BBOX)" ; \
	gcloud run jobs execute common-trails-group-edges-osm-$(or $(ENV),prod) \
	    --region=$(or $(GCP_REGION),europe-west1) \
	    $$ARGS_FLAG \
	    --wait

## Trigger the Cloud Run Job ``common-trails-import-osm-prod`` for one
## region. The job runs INSIDE GCP with a db-internal connection —
## no cloud-sql-proxy hop. Expected throughput is 10-30× the proxy
## path.
##
##   make prod-import-osm-job REGION=occitanie
##   make prod-import-osm-job REGION=france  # whole France (~1-2 h)
##
## Cost: ~$0.04 per region run (Cloud Run gen2, 2 vCPU × 2 Gi, ~30 min
## per region). No egress.
##
## The job downloads the PBF from Geofabrik on first run; per-execution
## DATA_DIR=/tmp means the cache resets per run but Geofabrik downloads
## are cheap (~5 min for 250 MB regional PBF on Google's network).
##
## ``--wait`` blocks the Make target on job completion so you see exit
## code and can chain follow-up steps.
prod-import-osm-job: gcp-precheck
	@if [ -z "$(REGION)" ]; then echo "❌ Set REGION=occitanie|paca|ara|auvergne|france|... (see app/cli/import_osm_roads.py REGIONS map)"; exit 1; fi
	@echo "→ executing Cloud Run Job common-trails-import-osm-$(or $(ENV),prod) with REGION=$(REGION)…"
	gcloud run jobs execute common-trails-import-osm-$(or $(ENV),prod) \
	    --region=$(or $(GCP_REGION),europe-west1) \
	    --args=$(REGION) \
	    --wait

## Same as prod-import-osm-south but via the Cloud Run Job. Sequential
## (not parallel) to keep db-f1-micro CPU bounded — the COPY-FROM-STDIN
## bursts are intensive enough that parallel runs would saturate the tier.
##
## Total cost: ~$0.16 for the 4 regions. Total time: ~1-2 h.
prod-import-osm-job-south: gcp-precheck
	$(MAKE) prod-import-osm-job REGION=occitanie
	$(MAKE) prod-import-osm-job REGION=paca
	$(MAKE) prod-import-osm-job REGION=ara
	$(MAKE) prod-import-osm-job REGION=auvergne
	@echo "✅ South France OSM imported via Cloud Run Job (~$$0.16)."

## Import a local Strava bulk export into prod ``activities`` (and
## downstream heat_edges via the ingest pipeline). Same cloud-sql-proxy
## pattern as the OSM import: parse runs locally in the backend
## container, INSERTs go over the proxy to prod db-f1-micro.
##
## Required: USER_ID=<uuid of the prod user that owns these activities>.
## Get it from: psql … "SELECT id FROM users WHERE email='you@example.com'".
##
## Strava export folder is ./data/strava-export on the host (already
## bind-mounted at /strava-export inside the backend container, ro).
##
## ~1400 activities with the in-process spatial/HMM matcher = ~30 min.
## ingest_activity does inline heat compute via the in-process matcher.
##
## Idempotent: re-running with same files is a no-op (file_hash dedup).
prod-import-strava: gcp-precheck
	@if [ -z "$(USER_ID)" ]; then echo "❌ Set USER_ID=<uuid> (psql -c 'SELECT id FROM users WHERE email=...' on prod)"; exit 1; fi
	@docker compose ps backend --format json 2>/dev/null | grep -q '"State":"running"' || (echo "❌ local backend container not running — make up"; exit 1)
	@DB_PWD=$$(gcloud secrets versions access latest --secret=DATABASE_URL --project=common-trails 2>/dev/null | sed -E 's|.*://[^:]+:||; s|@.*||') ; \
	if [ -z "$$DB_PWD" ]; then echo "❌ couldn't read db password from the DATABASE_URL secret"; exit 1; fi ; \
	echo "→ starting cloud-sql-proxy on host port 5433…" ; \
	pkill -f "cloud-sql-proxy.*common-trails-prod" 2>/dev/null || true ; \
	cloud-sql-proxy --port 5433 common-trails:europe-west1:common-trails-prod > /tmp/csqlp-prod.log 2>&1 & \
	PROXY_PID=$$! ; sleep 4 ; \
	echo "→ importing /strava-export → prod (user_id=$(USER_ID))…" ; \
	docker compose exec -T \
	    -e DATABASE_URL="postgresql://api:$$DB_PWD@host.docker.internal:5433/common_trails" \
	    -e SKIP_OSM_FETCH=true \
	    -e SKIP_MATVIEW_REFRESH=true \
	    -e SKIP_PMTILES_REBUILD=true \
	    backend python -m scripts.import_strava_export /strava-export --user-id $(USER_ID) || EXIT=$$? ; \
	kill $$PROXY_PID 2>/dev/null || true ; \
	exit $${EXIT:-0}

## Start full stack (db + backend + frontend)
up:
	docker compose up --build -d

## Stop and remove containers + volumes
down:
	docker compose down -v

## Rebuild PMTiles + write to host file IN-PLACE (preserving inode) so
## the frontend's single-file bind mount sees the new bytes.
##
## Why not just `docker compose cp`? `cp` (and `docker cp`) replace the
## destination by creating a NEW inode. The frontend container's bind
## mount latches onto the inode that existed when the container started,
## so a `cp` afterward leaves the container serving the OLD file.
## Hours-of-debugging trap (May 2026). The pipe-to-stdout below uses
## ``cat ... > host_file``, which truncates and writes the existing
## inode — the container picks up the new bytes immediately.
## --min-uc tracks the K-anonymity floor (HEATMAP_K_ANONYMITY in the backend
## container's env: 1 in dev compose for full visibility, default 2 in prod).
## Without it, build_pmtiles' argparse default (1) published single-user
## OSM-matched edges into the static binary — a K-anon bypass (June 2026 audit).
pmtiles:
	docker compose exec -T backend sh -c 'python -m app.jobs.build_pmtiles --output-dir /tmp --min-uc "$${HEATMAP_K_ANONYMITY:-2}"'
	docker compose exec -T backend cat /tmp/heatmap-display.pmtiles > frontend/public/heatmap-display.pmtiles
	@echo "PMTiles written in place: $$(ls -la frontend/public/heatmap-display.pmtiles)"
	@echo "Container sees: $$(docker compose exec -T frontend ls -la /app/out/heatmap-display.pmtiles)"
	## build_pmtiles also emits stats.json (homepage banner). Copy it next to
	## the PMTiles so the dev static export serves it at /stats.json.
	docker compose exec -T backend cat /tmp/stats.json > frontend/public/stats.json
	@echo "Community stats written: $$(cat frontend/public/stats.json)"

## Truncate + rebuild heat_edges + heat_edge_contributors from the
## ``activities`` table. Use this after: changing the ingest pipeline,
## importing a new OSM PBF, merging users, or any other mutation that
## should change which edges exist. Doesn't touch ``activities`` —
## those are the source of truth.
##
## New heat_edges get match_source = 'spatial' (or 'grid_fallback') from
## the in-process matcher.
##
## SKIP_PMTILES_REBUILD=true so we don't trigger 100s of debounced display
## rebuilds during the loop; ``make pmtiles`` runs ONCE at the end.
## (The heat_edges_display matview was dropped June 2026 — the live MVT
## endpoint now aggregates heat_edges directly via the shared
## app.services.heat_aggregation builder, so there is no matview to refresh.)
heat-edges:
	docker compose exec -T -e TRUNCATE_FIRST=true \
	    -e SKIP_PMTILES_REBUILD=true \
	    backend python -m app.jobs.rebuild_heatmap

## End-to-end heatmap deploy: heat_edges → PMTiles.
##
## Use this after any heat_edges-altering change. Doesn't run if any
## step fails — fix and re-run; each sub-step is idempotent.
##
## NB: this is the ONE command to run after ingest-pipeline edits.
## The heat_edges_display matview was dropped June 2026 — the live MVT
## endpoint aggregates heat_edges directly, so there's no matview step.
## (The .fgraph routing-shard step was removed with the WASM-routing
## decommission — routing is delegated to external tools.)
heatmap-deploy: heat-edges pmtiles
	@echo
	@echo "════════════════════════════════════════════════════════════════"
	@echo "  Heatmap deploy complete."
	@echo "  Run ``make heatmap-status`` to confirm artefacts are in sync."
	@echo "════════════════════════════════════════════════════════════════"

## Show whether each heatmap artefact is up-to-date relative to the DB.
## Doesn't mutate anything. Reports row counts in DB (source of truth)
## + sizes/mtimes of derived files. If any derived artefact is older
## than the most-recent activity ingest, run ``make heatmap-deploy``.
heatmap-status:
	@echo "── DB ──────────────────────────────────────"
	@docker compose exec -T db psql -U postgres -d common_trails -tAc " \
	  SELECT 'activities: ' || COUNT(*) || ' rows, latest=' || \
	         COALESCE(MAX(created_at)::text, 'never') FROM activities; \
	  SELECT 'heat_edges: ' || COUNT(*) || ' rows' FROM heat_edges; \
	  SELECT 'distinct contributors: ' || COUNT(DISTINCT user_id_hash) \
	    FROM heat_edge_contributors;"
	@echo
	@echo "── Frontend artefacts ──────────────────────"
	@if [ -f frontend/public/heatmap-display.pmtiles ]; then \
	   echo "PMTiles:"; \
	   ls -la frontend/public/heatmap-display.pmtiles; \
	else \
	   echo "PMTiles: MISSING — run 'make pmtiles'"; \
	fi
	@echo
	@echo "Run 'make heatmap-deploy' to refresh everything (~25-40 min)."

## Stream logs from full stack
logs:
	docker compose logs -f

## Run backend tests (pytest, fast subset) — uses .env.test with TEST_MODE=true.
## `-m "not slow"` skips ~277 tests that exercise full-DB scans / publish loops;
## those are designed for clean fixture data and OOM the container against the
## ~4.5 M heat_edges in dev DB. See `make test-slow` to opt back in (against a
## fresh DB or with PYTEST_MAX_HEAT_EDGES=10000 to skip auto-trigger).
test:
	docker compose --env-file .env.test up -d
	docker compose exec backend pytest -q -m "not slow"
	docker compose down

## Run the slow-marked tests too (full publish loops, prod-sized perf checks).
## Expect OOM-kill if run against a dev DB with > ~10 k heat_edges. Ideally
## reset dev DB first: docker compose down -v && docker compose up -d
test-slow:
	docker compose --env-file .env.test up -d
	docker compose exec backend pytest -q
	docker compose down

## Run the GOLDEN + SLOW tests against the DEV stack (real occitanie OSM PBF
## in osm_road_edges + data/ mounted). These pin the ingestion → heatmap
## quality invariants (continuity, per-point osm_way_id, pass_count
## accumulation, K-anonymity, corpus aggregate) and SKIP under `make test`
## (which uses the clean .env.test DB with no PBF). `-ra` prints the skip
## reasons so a missing PBF is VISIBLE, never a silent green.
##
## Prereqs: `make up` (stack running) + the PBF imported once via
## `make import-osm-roads-local`. If goldens report "skipped (needs PBF)",
## that's the trap — import the PBF, don't assume they passed.
test-golden:
	@docker compose ps backend 2>/dev/null | grep -qiE "up|running" || { \
	  echo "✗ backend not running — run 'make up' first"; exit 1; }
	docker compose run --rm --no-deps \
	    -v "$(PWD)/data:/app/data:ro" \
	    backend pytest -ra -m "golden or slow" tests/

## Rebuild the heatmap DISPLAY from the activities table through the CURRENT
## ingest code, end-to-end, then drop the fresh PMTiles in place.
## Use after any change to services/ingest.py / the matcher / K-anonymity.
## (heat_edges → ANALYZE → pmtiles). ~20-40 min on a full corpus.
## The heat_edges_display matview was dropped June 2026 — the live MVT
## endpoint aggregates heat_edges directly, so there's no matview step.
## Afterwards: `docker compose up --build frontend -d` + hard-reload the browser.
heatmap-rebuild:
	docker compose exec -T -e TRUNCATE_FIRST=true backend python -m app.jobs.rebuild_heatmap
	$(MAKE) pmtiles
	@echo "✅ heat_edges + pmtiles rebuilt. Now: docker compose up --build frontend -d"

## Quick heat-edge discontinuity check ("est-ce que ça part en spaghetti ?").
## Prints per-region grid_fallback / dangling / isolated ratios for the
## monitored regions and exits 2 if any region crosses the alert thresholds.
## Read-only, seconds per region. Options pass through, e.g.:
##   make heat-quality
##   make heat-quality ARGS="--sport gravel"
##   make heat-quality ARGS="--bbox 3.85 43.58 3.95 43.65 --json"
heat-quality:
	docker compose exec -T backend python -m app.cli.heat_quality $(ARGS)

## Restore the "clean continuous heatmap from Paul's rides" local state in
## ~2 min (single-user, realistic pass_counts, built pmtiles)
## instead of the ~1.5h wipe→import→rebuild. Use this any time the local DB
## got polluted (multi-user demo data) or you just want the known-good state
## back. Snapshot lives in data/clean-snapshot/. See the
## reference_clean_local_heatmap_snapshot memory.
heatmap-restore:
	bash scripts/restore-clean-heatmap.sh

## Re-capture the clean snapshot FROM the current DB + built artifacts
## (after you've intentionally improved the clean state). Overwrites
## data/clean-snapshot/.
heatmap-snapshot:
	@mkdir -p data/clean-snapshot
	# heat_edges is PARTITIONED (heat_edges_road/_gravel/_mtb/...): the 'heat_edges*'
	# glob captures the parent AND every child partition (the actual data lives in
	# the children; -t heat_edges alone dumps an empty parent). heat_edge_contributors
	# is listed separately (its name doesn't match the heat_edges* glob).
	# heat_edges_agg (migration 0057) is DERIVED from heat_edges — EXCLUDE it
	# (-T) so the snapshot stays lean; `make heatmap-restore` rebuilds it from
	# the restored heat_edges (the 'heat_edges*' glob would otherwise also
	# capture it, and restore doesn't truncate it → PK conflicts).
	docker compose exec -T db pg_dump -U postgres -d common_trails --data-only \
	  -t activities -t 'heat_edges*' -T heat_edges_agg -t heat_edge_contributors --no-owner \
	  | gzip > data/clean-snapshot/clean-heatmap.sql.gz
	cp frontend/public/heatmap-display.pmtiles data/clean-snapshot/
	@echo "✅ Clean snapshot captured → data/clean-snapshot/ ($$(du -sh data/clean-snapshot | cut -f1))"

## Import OSM road segments from a local PBF into local DB. Useful after
## ``docker compose down -v`` (fresh DB) so the spatial matcher in ingest
## has roads to snap GPS points to. Without this, every ingest falls into
## the 4dp grid-fallback path (good but coarser; produces NULL osm_way_id
## rows that break the continuous-line invariant in the heatmap render).
##
##   make import-osm-roads-local                   # default: occitanie region (Geofabrik download, cached 24h)
##   make import-osm-roads-local PBF=/data/foo.pbf # explicit local PBF (path inside the container)
##
## Default is the `occitanie` region preset (~200MB PBF, ~10-30 min) — the
## proven local substrate. Do NOT default to france-south.osm.pbf: that
## 2.5GB PBF (~9.8M segments) has OOM-killed the local import TWICE (once
## dying at 700k rows and silently gutting osm_road_edges). If you really
## need it, pass PBF=/data/france-south.osm.pbf explicitly and watch memory.
## One-shot; subsequent runs only refresh tiles covered by the PBF (idempotent).
##
## Implementation note: runs detached inside the container with output
## redirected to /tmp/osm-import.log. Without ``-d``, ``docker compose
## exec`` ties the python's stdout/stderr to a pipe that gets SIGHUP'd
## the moment the host shell loses the tty (long-running background
## tasks, IDE disconnects, etc.) — the python dies silently mid-parse
## and you end up with 0 rows in osm_road_edges with no error message.
## See learning notes 2026-05-10 in routing-client agent doc.
import-osm-roads-local:
	@if [ -n "$$PBF" ]; then IMPORT_ARGS="$$PBF --no-download"; SRC="$$PBF"; \
	else IMPORT_ARGS="occitanie"; SRC="occitanie region (Geofabrik download, cached 24h)"; fi ; \
	echo "→ importing OSM roads from $$SRC (~10-30 min, detached)…" ; \
	docker compose exec -T -d -e SKIP_OSM_FETCH=true backend bash -c \
	  "python -u -m app.cli.import_osm_roads $$IMPORT_ARGS > /tmp/osm-import.log 2>&1" ; \
	echo "→ tailing /tmp/osm-import.log inside backend container (Ctrl-C to detach; the import keeps running):" ; \
	echo "    docker compose exec backend tail -f /tmp/osm-import.log" ; \
	while docker compose exec -T backend bash -c "find /proc -maxdepth 2 -name cmdline 2>/dev/null -exec sh -c 'tr -d \"\\0\" < \"\$$1\" | grep -q import_osm' _ {} \\;" 2>/dev/null; do \
	  sleep 60 ; \
	  echo "  …still running ($$(date '+%H:%M:%S'))" ; \
	done ; \
	echo "→ done. Last log lines:" ; \
	docker compose exec -T backend tail -10 /tmp/osm-import.log

## Full clean-heatmap rebuild for local dev — fixes the "spaghetti" 11-m
## criss-cross render at zoom 17+ caused by heat_edges with NULL osm_way_id.
##
## Sequence:
##   1. import-osm-roads-local — populate osm_road_edges (~30 min, skip if done)
##   2. group_edges_osm — backfill osm_way_id on heat_edges that run parallel
##      to an OSM road within 25m (~5 min)
##   3. build_pmtiles --keep-grid-fallback --min-uc 1 — regenerate display
##      file (~16 min) and copy into frontend/public/
##
## After this, restart the frontend container so it picks up the new PMTiles
## (the static server holds the file open):
##   docker compose restart frontend
heatmap-clean-rebuild: import-osm-roads-local
	@echo "→ grouping heat_edges onto OSM ways (re-keys + propagates osm_way_id)…"
	docker compose exec -T backend python -m app.cli.group_edges_osm
	@echo "→ rebuilding PMTiles with grid fallback + min_uc=1…"
	docker compose exec -T backend python -m app.jobs.build_pmtiles \
	    --output-dir /tmp --keep-grid-fallback --min-uc 1
	docker compose cp backend:/tmp/heatmap-display.pmtiles frontend/public/heatmap-display.pmtiles
	@echo "✅ Run 'docker compose restart frontend' then hard-reload your browser."

## Import GR/GRP/GT/PR/EV trail relations from a local PBF on disk.
## Replaces the Overpass-based import_trails (which silently empties
## trail_edges when Overpass is rate-limited or down). Args:
##   PBF=path-to.pbf  (default: data/france-south.osm.pbf)
##
## ~2 minutes for france-south.osm.pbf (~65k trail edges).
import-trails-pbf:
	@PBF="$${PBF:-/data/france-south.osm.pbf}" ; \
	echo "→ importing trails from $$PBF (replaces existing trail_edges)…" ; \
	docker compose exec -T backend python -m app.cli.import_trails_pbf $$PBF

## Import the same trail edges into prod via cloud-sql-proxy. Mirrors
## the prod-import-osm pattern. Run AFTER prod-import-osm-south so
## any trails-on-osm spatial join has roads to land on.
prod-import-trails-south: gcp-precheck
	@docker compose ps backend --format json 2>/dev/null | grep -q '"State":"running"' || (echo "❌ local backend container not running — make up"; exit 1)
	@test -f data/france-south.osm.pbf || (echo "❌ data/france-south.osm.pbf missing — run merge-or-download first"; exit 1)
	@DB_PWD=$$(gcloud secrets versions access latest --secret=DATABASE_URL --project=common-trails 2>/dev/null | sed -E 's|.*://[^:]+:||; s|@.*||') ; \
	if [ -z "$$DB_PWD" ]; then echo "❌ couldn't read db password from the DATABASE_URL secret"; exit 1; fi ; \
	echo "→ starting cloud-sql-proxy on host port 5433…" ; \
	pkill -f "cloud-sql-proxy.*common-trails-prod" 2>/dev/null || true ; \
	cloud-sql-proxy --port 5433 common-trails:europe-west1:common-trails-prod > /tmp/csqlp-prod.log 2>&1 & \
	PROXY_PID=$$! ; \
	sleep 4 ; \
	echo "→ importing trails from data/france-south.osm.pbf into prod…" ; \
	docker compose cp data/france-south.osm.pbf backend:/data/ 2>&1 | tail -2 ; \
	docker compose exec -T \
	    -e DATABASE_URL="postgresql://api:$$DB_PWD@host.docker.internal:5433/common_trails" \
	    backend python -m app.cli.import_trails_pbf /data/france-south.osm.pbf || EXIT=$$? ; \
	kill $$PROXY_PID 2>/dev/null || true ; \
	exit $${EXIT:-0}

## Start backend stack only (db + backend)
backend:
	docker compose -f docker-compose.backend.yml up --build -d

## Start frontend only (Docker)
frontend:
	docker compose -f docker-compose.frontend.yml up --build -d

## Local dev: backend in Docker, frontend with Next.js dev server (HMR)
dev:
	docker compose -f docker-compose.backend.yml up --build -d
	cd frontend && npm run dev

## Local dev: frontend only (assumes backend already running)
dev-frontend:
	cd frontend && npm run dev

## Lint backend (ruff check)
lint:
	cd backend && ruff check .

## Format backend (ruff fix + format)
format:
	cd backend && ruff check --fix . && ruff format .

# ── DFCI data management ─────────────────────────────────────────────────────

# Fixed prod data bucket (was read from `terraform output`; terraform retired 2026-08-05 — see infra/terraform/README.md).
DATA_BUCKET ?= common-trails-common-trails-data-prod
DFCI_CACHE  := backend/app/data/dfci_ign_edges.json

## Download DFCI cache from GCS (for local dev matching production)
dfci-download:
	@echo "Downloading DFCI data from gs://$(DATA_BUCKET)/ ..."
	gsutil cp gs://$(DATA_BUCKET)/dfci_ign_edges.json $(DFCI_CACHE)
	@echo "Done: $$(wc -c < $(DFCI_CACHE) | tr -d ' ') bytes → $(DFCI_CACHE)"

## Extract DFCI from IGN WFS and save cache (requires geopandas)
## France entière: piste_dfci + categorie_dfci (~170K features, ~4 min)
dfci-extract:
	python scripts/extract_dfci_ign.py --include-categorie --edges-cache $(DFCI_CACHE)

## Upload local DFCI cache to GCS (updates production data)
dfci-upload:
	@echo "Uploading DFCI data to gs://$(DATA_BUCKET)/ ..."
	gsutil cp $(DFCI_CACHE) gs://$(DATA_BUCKET)/dfci_ign_edges.json
	@echo "Done. Restart Cloud Run to pick up new data."
