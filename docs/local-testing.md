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

## 2. Running tests — which tier, which command

| Tier | What it covers | Command | Needs |
|---|---|---|---|
| **Backend unit / fast** | pure logic, handlers (the `not slow` subset CI runs) | `make test` | clean test DB (auto) |
| **Backend slow** | the `slow`-marked tests (throughput, DFCI store) | `make test-slow` | clean test DB (auto) |
| **Frontend unit** | Vitest: components + `lib/` modules (jsdom) | `cd frontend && npm run test` | node 22 |
| **E2E** | real browser: home, /map layers, import, auth, i18n | `cd e2e && npx playwright test` | `make up` + a fresh `frontend` build |

- **`make test`** uses `.env.test` (a clean DB, `TEST_MODE=true`) and runs `-m "not slow"`.
- Run one file directly against the dev DB:
  ```bash
  docker compose run --rm --no-deps backend pytest -ra tests/test_archive_import_e2e.py
  ```

## 3. Rebuild the heatmap + see it

The rendered heatmap is a **static PMTiles binary**: it is stale until you rebuild it and reload the browser.

Prod renders the **raw-trace** pipeline (`HEATMAP_DISPLAY_SOURCE=raw`). That is also the code default and what `docker-compose.yml` sets, so the dev backend builds the same thing as prod (the floor is `HEATMAP_MIN_USERS`, not `--min-uc`):

```bash
docker compose exec -T -e HEATMAP_MIN_USERS=1 \
    backend python -m app.jobs.build_pmtiles --output-dir /tmp
docker compose exec -T backend cat /tmp/heatmap-display.pmtiles > frontend/public/heatmap-display.pmtiles
# then hard-reload the browser (Cmd-Shift-R)
```

Write the host file **in place** (`cat … >`), never `docker compose cp` — see Trap #2. `make pmtiles` does the same two steps (it still passes `--min-uc ${HEATMAP_K_ANONYMITY}`, which is only stamped into `stats.json`).

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
| 3 | **PostGIS volume upgrade** | weird PG init errors | `docker compose down -v` to drop the old PG data volume |

See also: [`development.md`](development.md), [`configuration.md`](configuration.md), [`ci.md`](ci.md) and [`CONTRIBUTING.md`](../CONTRIBUTING.md).
