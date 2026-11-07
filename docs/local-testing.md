# Local testing — the trap-free guide

How to bring the stack up, run **every** test tier for real, rebuild the heatmap, and *see* it — without the silent traps that waste an afternoon. If a command here "passes" but you suspect it did nothing, check the **Traps** table at the bottom first.

## 1. One-time setup

```bash
make up                        # build + start db + backend + frontend
# .env is optional — dev defaults (TEST_MODE=true, HEATMAP_K_ANONYMITY=1, …) come
# from docker-compose.yml. See docs/configuration.md.
curl -s localhost:8787/healthz # → {"status":"ok"}
```

Open **http://localhost:3787** (demo account seeded automatically).

### Legacy only: OSM PBF for the matched-pipeline golden tests

Only needed for the **legacy matched** pipeline tests (map-matching to OSM). The current raw-trace heatmap does not use OSM. Without the PBF those goldens **skip** (they don't fail — see Trap #3). Import it once if you work on them:

```bash
make import-osm-roads-local    # downloads + imports the `occitanie` PBF (~10-30 min, cached 24h)
```

This populates `osm_road_edges` (~7M rows, Hérault/Gard/Cévennes). You only redo this when the PBF coverage needs to change.

## 2. Running tests — which tier, which command

| Tier | What it covers | Command | Needs |
|---|---|---|---|
| **Backend unit / fast** | pure logic, handlers (the `not slow` subset CI runs) | `make test` | clean test DB (auto) |
| **Backend golden + slow** | legacy matched ingestion → heatmap quality (continuity, per-point `osm_way_id`, `pass_count`, K gate, corpus aggregate) | `make test-golden` | **`make up` + the PBF imported** |
| **Frontend unit** | Vitest: components + `lib/` modules (jsdom) | `cd frontend && npm run test` | node 22 |
| **E2E** | real browser: home, /map layers, import, auth, i18n | `cd e2e && npx playwright test` | `make up` + a fresh `frontend` build |

- **`make test`** uses `.env.test` (a clean DB, `TEST_MODE=true`) and runs `-m "not slow"`. The golden tests **skip** here by design (no PBF).
- **`make test-golden`** runs `-m "golden or slow"` against the **dev** stack with `data/` mounted, and prints skip reasons (`-ra`). **If it reports everything skipped → the PBF isn't imported** (Trap #3), not a pass.
- Run one file directly against the dev DB + data:
  ```bash
  docker compose run --rm --no-deps -v "$PWD/data:/app/data:ro" \
      backend pytest -ra tests/test_gpx_golden_corpus_aggregate.py
  ```

## 3. Rebuild the heatmap + see it

The rendered heatmap is a **static PMTiles binary**: it is stale until you rebuild it and reload the browser.

Prod renders the **raw-trace** pipeline (`HEATMAP_DISPLAY_SOURCE=raw`), but `docker-compose.yml` does not set that variable, so the dev backend runs the code default (`matched`). To build the prod-like raw PMTiles locally, pass the env on the `exec` (the floor is `HEATMAP_MIN_USERS`, not `--min-uc`):

```bash
docker compose exec -T -e HEATMAP_DISPLAY_SOURCE=raw -e HEATMAP_MIN_USERS=1 \
    backend python -m app.jobs.build_pmtiles --output-dir /tmp
docker compose exec -T backend cat /tmp/heatmap-display.pmtiles > frontend/public/heatmap-display.pmtiles
# then hard-reload the browser (Cmd-Shift-R)
```

Write the host file **in place** (`cat … >`), never `docker compose cp` — see Trap #2. `make pmtiles` does the same two steps but in the dev default (matched) mode with `--min-uc ${HEATMAP_K_ANONYMITY}`.

Legacy matched pipeline only: `make heatmap-rebuild` runs `rebuild_heatmap` (TRUNCATE + re-ingest every activity, then `ANALYZE`) → `make pmtiles`; `make heat-quality` prints per-region spaghetti/discontinuity ratios (exits 2 on alert).

## 4. Lint before pushing

```bash
docker compose exec backend ruff check app tests   # Python
cd frontend && npx tsc --noEmit                     # TypeScript
```

## Traps (the silent ones that cost hours)

| # | Trap | Symptom | Fix |
|---|---|---|---|
| 1 | **Frontend serves a snapshot of `out/`** (not bind-mounted) | code/pmtiles change doesn't show | `docker compose up --build frontend -d` — `restart` keeps the OLD build |
| 2 | **PMTiles is a static binary** (bind-mounted by inode) | heatmap changes invisible after a data/code change, or after `docker compose cp` | rebuild it (§3) and write the host file in place (`cat … >`); the binary is NOT auto-refreshed |
| 3 | **Legacy golden tests skip without the PBF** | `make test`/`make test-golden` green but 0 ingestion goldens ran | `make import-osm-roads-local`; read the `-ra` skip lines — a skip is **not** a pass |
| 4 | **Stale planner stats right after a bulk reload** (legacy matched) | a normally-160ms tile query hangs for *minutes* during/after `rebuild_heatmap` | `rebuild_heatmap` runs `ANALYZE` automatically; if you reload heat_edges by hand, `ANALYZE heat_edges` yourself |
| 5 | **PostGIS volume upgrade** | weird PG init errors | `docker compose down -v` to drop the old PG data volume |

See also: [`development.md`](development.md), [`configuration.md`](configuration.md), [`ci.md`](ci.md) and [`CONTRIBUTING.md`](../CONTRIBUTING.md).
