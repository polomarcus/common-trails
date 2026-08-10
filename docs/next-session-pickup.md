# Pickup plan for next Claude session

Written 2026-05-03 by the previous session, before Paul closed his laptop. Read this first — you have no conversation context, but everything you need is in the repo.

## State at handoff

- Branch: `fix/overnight-improvements` — **PR #218** open against main (`https://github.com/polomarcus/common-trails/pull/218`).
- Commits in this branch (`git log main..fix/overnight-improvements --oneline`):
  - test fixes, docs, runbook, soft-launch playbook
  - heatmap continuous-line fix across PMTiles + matview + live MVT fallback
  - migration 0038 (`ix_osm_road_edges_osm_way_id`)
  - Komoot-quality default (drop grid_fallback) + `make pmtiles` target
  - 32 non-regression tests for the heatmap continuity invariant
- Local heatmap **works** at street zoom — single smooth line per OSM way after `make pmtiles`. Verified by Paul at z17–z20.
- **No production yet.** Paul destroyed the prod DB to save money before the soft launch.

## Important user context

- Paul. Solo dev. Open-source cycling app.
- French-speaking but ok with English replies. Code/docs in English.
- Strong preference: no `--no-verify`, no force pushes, no destructive ops without confirming.
- Cost-conscious: target prod is `db-f1-micro` ~$10/mo. **Don't propose anything that costs more without asking.**
- Aim is to invite 2–3 friends to a soft-launch in the coming week. The plan is in `docs/migration-runbook.md` § "Soft-launch playbook — 3 friends on prod".
- **Don't push to main.** Only PR branches. Open PR via `gh pr create`. Never merge.

## What's open

### A. PR #218 status
- Run `gh pr checks 218` to see CI state. Last known: backend tests + lint should pass; E2E may still flake on Playwright/timing issues unrelated to this PR. Flag flakes to Paul, don't try to fix unrelated tests.
- Don't merge. Wait for Paul.

### B. Three Komoot quick wins (documented, ready to execute)
File: `docs/heatmap-komoot-parity.md`. Three items, each ~1–3 h:

1. **Pink/orange palette** in `frontend/app/page.tsx` `community-trails-*` layers (replace purple→lavender ramp with pink→orange). Doc gives exact color stops.
2. **Highway-type emphasis** in `heat_score` SQL (boost primary/secondary roads by 0.15, tertiary by 0.05). Update all 3 paths (`build_pmtiles.py`, `refresh_matview.py`, `heatmap.py` fallback) consistently. Update `TestPMTilesContinuousHeatmap` if you change the SQL string asserts.
3. **Path/track dashed style** — split `community-trails-line` into roads vs paths layers based on `highway_type`. Need to add `highway_type` to the PMTiles property list.

Pick whichever you have time for. Each is independent. Each needs:
- Code change
- `make pmtiles` (NOT `docker compose cp` — see § C below)
- Hard-reload the browser to bust the `Cache-Control: max-age=86400` PMTiles cache
- Visual check at <http://localhost:3787/map?lat=43.62128&lon=3.87842&zoom=17.2>

### C. Don't repeat: PMTiles bind-mount staleness
Cost us hours in the previous session. Single-file bind-mount (`./frontend/public/heatmap-display.pmtiles:/app/out/...`) latches on the inode at container start. `docker compose cp` creates a NEW inode on the host, so the container keeps serving the OLD file. **Use `make pmtiles`** — it does `docker compose exec backend cat /tmp/... > host_file` which truncates and writes the existing inode. The container picks up the new bytes immediately, no restart.

### D. Test users still in DB
```sql
SELECT COUNT(*) FROM users WHERE email LIKE 'test_%@example.com';
-- ~1222 in the dev DB at handoff
```
Cosmetic: pytest creates these via the `auth_headers` fixture and doesn't clean up. They're isolated to the Iceland test bbox via `_clean_test_edges` so they don't pollute the heatmap. Fix is "low priority" — only matters if Paul wants a clean count for the friend-invite verification step. Don't touch unless Paul asks.

### E. Soft-launch playbook execution
File: `docs/migration-runbook.md` § "Soft-launch playbook". 9 phases. Phase 0 is preflight (you can do that). Phases 1–8 need Paul's GCP credentials and his explicit "go" — DON'T run `terraform apply` or `gcloud` mutations without him. Read-only `gcloud` queries (e.g. `gcloud projects list`) are fine for situational awareness.

### F. Map-matching with Valhalla
File: `docs/heatmap-map-matching-plan.md`. 8-step concrete plan, 1–2 days. **Don't start without Paul confirming.** It changes the ingest pipeline shape, schema (migration 0039), and adds a new docker-compose service (~1 GB RAM). Worth doing before public launch but not in the friend soft-launch window.

## How to spend the next session

Recommended priorities, top of list first:

1. **Check PR #218 CI** with `gh pr checks 218`. If anything's red and trivially fixable (lint, an obvious typo), patch it. If it's a flake or unrelated, surface to Paul and stop.
2. **One of the Komoot quick wins** (B.1, B.2, or B.3). Do them one at a time, commit each, push to the branch. Don't bundle three commits without testing each.
3. **If you have spare time**: skim `docs/ingestion-pipeline.md` and `docs/heatmap-pipeline.md` for any stale references to dropped/changed code.

## What NOT to do

- Don't run `terraform apply`, `gcloud run services update`, `alembic upgrade` against prod — there is no prod, and the commands would target Paul's GCP project if he's authed.
- Don't merge PR #218.
- Don't start map-matching (F) without Paul's go-ahead.
- Don't `docker compose cp` to update PMTiles — use `make pmtiles`.
- Don't try to fix the 1222 test users (D) unless asked.
- Don't propose paid services (Mapbox, OpenAI, etc.) — Paul's running on db-f1-micro for a reason.

## How to confirm the heatmap still looks right

If Paul comes back and says "show me the current state":

```bash
# Verify what the frontend serves
curl -sI http://localhost:3787/heatmap-display.pmtiles | grep -E "Content-Length|Last-Modified"

# Verify the matview state (should be 0 rows = placeholder, that's fine for dev)
docker compose exec db psql -U postgres -d common_trails -c \
  "SELECT COUNT(*) FROM heat_edges_display"

# Confirm OSM data populated for Montpellier
docker compose exec db psql -U postgres -d common_trails -c \
  "SELECT COUNT(*) FROM osm_road_edges WHERE geometry && \
   ST_MakeEnvelope(3.85, 43.61, 3.89, 43.63, 4326)"
# Expect: 18000+ in Montpellier viewport
```

Visual check URL: <http://localhost:3787/map?lat=43.62128&lon=3.87842&zoom=17.2>. If you see floating fragments away from roads, run `make pmtiles` — it might be that the file changed but you need a fresh build.

## Files Paul cares about right now

- `docs/migration-runbook.md` — operator runbook, includes soft-launch playbook
- `docs/heatmap-komoot-parity.md` — visual polish roadmap
- `docs/heatmap-map-matching-plan.md` — bigger map-matching project (not next)
- `frontend/public/heatmap-display.pmtiles` — current served file (16 MB at handoff)
- `Makefile` — `make pmtiles` is the only sanctioned PMTiles deploy path

Good luck. Be honest about what you don't know.
