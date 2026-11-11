.PHONY: up down logs test test-slow test-golden e2e-import-real backend frontend dev dev-frontend lint format dfci-download dfci-extract dfci-upload pmtiles heatmap-status gcp-precheck

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

## Re-ingest local data/gpx/*.gpx into prod activities for the
## specified user (e.g. after prod-wipe-user-data).
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

## Import a local Strava bulk export into prod ``activities`` (and
## downstream heat_edges via the ingest pipeline). Cloud-sql-proxy
## pattern: parse runs locally in the backend
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

## Show whether each heatmap artefact is up-to-date relative to the DB.
## Doesn't mutate anything. Reports row counts in DB (source of truth)
## + sizes/mtimes of derived files. If any derived artefact is older
## than the most-recent activity ingest, run ``make pmtiles``.
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
	@echo "Run 'make pmtiles' to refresh the display artefact."

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

## Full import of a REAL export (Strava / Garmin .zip) through the real drain,
## in THROWAWAY containers (internal network, no host port, DB deleted on exit).
## LOCAL ONLY — never in CI. Prints counts, skip reasons, sports, emails,
## timings, peak memory. REDROP=1 re-drops it (idempotence).
## Usage: make e2e-import-real ARCHIVE=/path/to/export.zip
## See docs/archive-import-testing.md.
e2e-import-real:
	@test -n "$(ARCHIVE)" || { echo "usage: make e2e-import-real ARCHIVE=/path/to/export.zip"; exit 2; }
	REDROP=$(REDROP) scripts/e2e-import-real.sh "$(ARCHIVE)"

## Run the GOLDEN + SLOW tests against the DEV stack (real occitanie OSM PBF
## in osm_road_edges + data/ mounted). These pin the ingestion → heatmap
## quality invariants (continuity, per-point osm_way_id, pass_count
## accumulation, K-anonymity, corpus aggregate) and SKIP under `make test`
## (which uses the clean .env.test DB with no PBF). `-ra` prints the skip
## reasons so a missing PBF is VISIBLE, never a silent green.
##
## Prereqs: `make up` (stack running) + an `osm_road_edges` substrate already
## in the dev DB. The OSM import CLI was removed with the matched pipeline, so
## on a fresh DB these goldens report "skipped (needs PBF)" — a skip is NOT a
## pass. They leave with the matched ingest code.
test-golden:
	@docker compose ps backend 2>/dev/null | grep -qiE "up|running" || { \
	  echo "✗ backend not running — run 'make up' first"; exit 1; }
	docker compose run --rm --no-deps \
	    -v "$(PWD)/data:/app/data:ro" \
	    backend pytest -ra -m "golden or slow" tests/

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
	@mkdir -p $(dir $(DFCI_CACHE))
	@echo "Downloading DFCI data from gs://$(DATA_BUCKET)/ ..."
	gsutil cp gs://$(DATA_BUCKET)/dfci_ign_edges.json $(DFCI_CACHE)
	@echo "Done: $$(wc -c < $(DFCI_CACHE) | tr -d ' ') bytes → $(DFCI_CACHE)"

## Extract DFCI from IGN WFS and save cache (requires geopandas)
## France entière: piste_dfci + categorie_dfci (~170K features, ~4 min)
dfci-extract:
	@mkdir -p $(dir $(DFCI_CACHE))
	python scripts/extract_dfci_ign.py --include-categorie --edges-cache $(DFCI_CACHE)

## Upload local DFCI cache to GCS (updates production data)
dfci-upload:
	@echo "Uploading DFCI data to gs://$(DATA_BUCKET)/ ..."
	gsutil cp $(DFCI_CACHE) gs://$(DATA_BUCKET)/dfci_ign_edges.json
	@echo "Done. Restart Cloud Run to pick up new data."
