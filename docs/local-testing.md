# Local testing — the trap-free guide

How to bring the stack up, run **every** test tier for real, rebuild the heatmap, and *see* it — without the silent traps that waste an afternoon. If a command here "passes" but you suspect it did nothing, check the **Traps** table at the bottom first.

## 1. One-time setup

```bash
cp .env.example .env          # dev vars: admin@admin / admin, K=1, TEST_MODE=true
make up                        # build + start db + backend + frontend
curl -s localhost:8787/healthz # → {"status":"ok"}
```

Open **http://localhost:3787** (demo account seeded automatically).

### For the ingestion/heatmap GOLDEN tests, import OSM once

The golden tests map-match real GPX against the local OSM network. Without the PBF they **skip** (they don't fail — see Trap #3). Import it once:

```bash
make import-osm-roads-local    # downloads + imports the `occitanie` PBF (~10-30 min, cached 24h)
```

This populates `osm_road_edges` (~7M rows, Hérault/Gard/Cévennes). You only redo this when the PBF coverage needs to change.

## 2. Running tests — which tier, which command

| Tier | What it covers | Command | Needs |
|---|---|---|---|
| **Backend unit / fast** | pure logic, handlers, ~1200 tests | `make test` | clean test DB (auto) |
| **Backend golden + slow** | GPX ingestion → heatmap quality (continuity, per-point `osm_way_id`, `pass_count`, K-anonymity, ~40-ride corpus aggregate) | `make test-golden` | **`make up` + the PBF imported** |
| **Frontend unit** | pure TS (cost model, helpers) | `cd frontend && npm run test` | node |
| **E2E** | real browser, routing/drag-edit/map gestures | `cd e2e && npx playwright test` | `make up` + a fresh `frontend` build |

- **`make test`** uses `.env.test` (a clean DB, `TEST_MODE=true`) and runs `-m "not slow"`. The golden tests **skip** here by design (no PBF).
- **`make test-golden`** runs `-m "golden or slow"` against the **dev** stack with `data/` mounted, and prints skip reasons (`-ra`). **If it reports everything skipped → the PBF isn't imported** (Trap #3), not a pass.
- Run one file directly against the dev DB + data:
  ```bash
  docker compose run --rm --no-deps -v "$PWD/data:/app/data:ro" \
      backend pytest -ra tests/test_gpx_golden_corpus_aggregate.py
  ```

## 3. Rebuild the heatmap + see it

After any change to `services/ingest.py`, the matcher, K-anonymity, or the OSM import, the **rendered** heatmap is stale until you rebuild the display + reload the frontend:

```bash
make heatmap-rebuild               # heat_edges → ANALYZE → pmtiles (one shot)
docker compose up --build frontend -d   # NOT `restart` — see Trap #1
# then hard-reload the browser (Cmd-Shift-R)
```

`make heatmap-rebuild` runs: `rebuild_heatmap` (TRUNCATE + re-ingest every activity through the current code, then `ANALYZE`) → `make pmtiles` (the static binary the browser loads). Individual steps also exist: `make heat-edges`, `make pmtiles`. (The `heat_edges_display` materialized view was dropped June 2026 — the live MVT tile endpoint now aggregates `heat_edges` directly via the shared `app/services/heat_aggregation.py` builder, so there is no matview step.)

To sanity-check the data layer without the browser:

```bash
docker compose exec db psql -U postgres -d common_trails -c \
  "SELECT count(*) edges, count(DISTINCT osm_way_id) ways FROM heat_edges;"
make heat-quality   # per-region spaghetti/discontinuity ratios (grid_fallback / dangling / isolated); exits 2 on alert
```

## 4. Lint before pushing

```bash
docker compose exec backend ruff check app tests   # Python
cd frontend && npx tsc --noEmit                     # TypeScript
```

## Traps (the silent ones that cost hours)

| # | Trap | Symptom | Fix |
|---|---|---|---|
| 1 | **Frontend serves a snapshot of `out/`** (not bind-mounted) | code/pmtiles change doesn't show | `docker compose up --build frontend -d` — `restart` keeps the OLD build |
| 2 | **PMTiles is a static binary** | heatmap changes invisible after a heat_edges change | `make pmtiles` (or `make heatmap-rebuild`); the pmtiles binary is NOT auto-refreshed |
| 3 | **Golden tests skip without the PBF** | `make test`/`make test-golden` green but 0 ingestion goldens ran | `make import-osm-roads-local`; read the `-ra` skip lines — a skip is **not** a pass |
| 4 | **Stale planner stats right after a bulk reload** | a normally-160ms tile query hangs for *minutes* during/after `rebuild_heatmap` | `rebuild_heatmap` now runs `ANALYZE` automatically; if you reload heat_edges by hand, `ANALYZE heat_edges` yourself |
| 5 | **PostGIS volume upgrade** | weird PG init errors | `docker compose down -v` to drop the old PG data volume |

See also: [`development.md`](development.md), the root `README.md` cookbook, and `CLAUDE.md` (agent workflow rules).
