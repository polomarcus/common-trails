# Code practice — Chemins Communs

How we work in this codebase. Written for the open-source release: contributors who land here from a fork should read this in 5 minutes and know what's expected.

Four pillars, in order of weight: **Test → Document → Agent docs → Monitor.**

## 1. Test

Each layer has its own gate; CI runs all of them.

### Backend (`pytest`, ~1100 tests)

- **Hit a real database.** Tests use a Postgres container started by docker-compose; we don't mock the DB. The 2026-05-14 prod incident (silent dry-run guard skipped) was a Make-bug that pytest with a real DB would have caught — pytest with a mocked DB would not.
- **Each PR that touches ingest / routing / surface / elevation MUST add a test that demonstrates the new behavior.** Style: name the bug or invariant in the test name (`test_grid_fallback_assigns_null_osm_way_id`, `test_route_og_image_returns_png`).
- **Critical-path tests** that must stay green and are explicitly cited in CLAUDE.md / agent docs:
  - `backend/tests/test_perf_regression.py` — endpoint latency budgets (MVT tile <5 s, area.pb <15 s).
  - `backend/tests/test_local_dem.py` — DEM correctness (HGT parse, void detection).
  - `backend/tests/test_k_anonymity_boundary.py` — heatmap K-anonymity gate.
  - `backend/tests/test_pipeline_patterns.py` — heat_edges ingest invariants.

### Frontend / E2E (`Playwright`)

- **Motto test gates every routing change.** [`e2e/tests/motto-montpellier-anduze.spec.ts`](../e2e/tests/motto-montpellier-anduze.spec.ts) asserts: WASM `.fgraph` loads with >1000 vertices, total route compute <5 s, distance 60-80 km (real route, not straight-line), WASM CH <50 ms when extracted. PRs that change `frontend/public/routing-worker-wasm.js`, `frontend/lib/routing-worker-client.ts`, or the WASM crate run this before merge.
- **Mobile-responsive smoke check.** Use Playwright at `width: 390` for any UI change to `/map`, `/`, `/discover`, `/stats`.
- **`tsc --noEmit` + `ruff check` are pre-CI sanity** — every PR description should include both.

### Test discipline

- **No `pytest.skip` in CI** without a tracked issue link in the skip reason.
- **No `expect.fail()`** or commented-out asserts.
- **Tests describe the invariant**, not the implementation. If you rename a function, the test name should still make sense.

## 2. Document

We write documentation when omitting it would slow a future contributor. We don't write it for the sake of it.

### Code comments — the rule

**Only write a comment when the WHY is non-obvious.** Skip:
- WHAT-comments (`# loop over rows`)
- Comments that paraphrase the next line of code
- Restatements of identifier names
- Mid-function `# ── Section ──` separators when the function is < 30 lines

Write:
- The constraint that makes this code look weird (e.g. *"COPY-FROM-STDIN avoids per-row binds; previous INSERT VALUES path was 17 h on the proxy"*).
- The bug or incident this code prevents (`# Make recipes must be single-shell — see prod incident 2026-05-14`).
- Why a less-obvious approach was rejected.

The reader should be able to read the code, hit a surprise, and find the comment that explains it.

### Docstrings on CLI modules

Every script in `backend/app/cli/` starts with a docstring covering:

1. **What it does** in 2-3 sentences.
2. **Usage** examples (`python -m app.cli.import_osm_roads occitanie --no-download`).
3. **Idempotency claim** — "safe to re-run; ON CONFLICT DO NOTHING" or "destructive, requires CONFIRM=YES".
4. **Crouzet invariant** statement when relevant (*"never modifies stored GPX coords; only writes `elevation_gain_m`"*).

See [`backend/app/cli/recompute_elevation_gain.py`](../backend/app/cli/recompute_elevation_gain.py) for the canonical shape.

### Decision records

Architectural pivots live as standalone Markdown in `docs/` or `.claude/projects/.../memory/project_*.md`. Examples:

- [`docs/prod-rebuild-runbook.md`](prod-rebuild-runbook.md) — the prod rebuild sequence + post-mortem gotchas.
- [`docs/komoot-parity-roadmap.md`](komoot-parity-roadmap.md) — drag-edit performance roadmap.
- `.claude/projects/.../memory/project_*.md` — context that should survive a session reset.

When a decision changes (e.g. we move from `full.json` to regional `.fgraph` shards), update the doc that references it AND grep for the dead reference in README + tests.

## 3. Agent docs

`.claude/agents/{name}.md` is the **contract for a subsystem**. Each agent doc is the brief a future Claude (or human contributor) reads before changing that subsystem. Today:

| Agent | Scope |
|---|---|
| [`ingest-pipeline.md`](../.claude/agents/ingest-pipeline.md) | GPX/Strava upload → heat_edges → matview/PMTiles. Owns the K-anonymity gate, the 3 matcher paths, Cloud Tasks event flow. |
| [`routing-client.md`](../.claude/agents/routing-client.md) | WASM `.fgraph` shards, the orchestrator, sport-specific cost weights, route editor panel, fallback cascade. |
| (planned) `frontend.md` | Static-export build, i18n, OG cards, mobile responsiveness. |
| (planned) `ops.md` | Cloud Run Jobs, deploy workflow, monitoring, prod-rebuild Makefile targets. |

### Agent doc structure (mirrored across all of them)

1. **Architecture in one diagram** — ASCII flowchart, file:line accurate.
2. **Your scope** — the files this agent owns end-to-end.
3. **Hard invariants** — Crouzet integrity, continuous-line, K-anonymity, idempotency, 3D coords, event-driven artefacts, edge-key paths. **Each invariant cites the file:line that enforces it.**
4. **Architectural time bombs** — known scale-out issues we haven't fixed yet (module-level state, threading.Timer + scale-to-zero, etc).
5. **Test coverage gaps** — what's underspecified, with PR numbers when closed.
6. **Common debugging entry points** — copy-pasteable `gcloud logging read` / `psql` snippets.

### Refresh cadence

- **Weekly**: the maintainer (or the agent itself, ironically) refreshes the "Recently shipped" section with the last 5-10 PRs that touched the subsystem.
- **Per-PR**: when a PR changes file:line citations in an agent doc, the PR must update the doc. CI doesn't enforce this; reviewers do.
- **Sentry alerts feed back**: when a runtime invariant violation is detected via Sentry, add a "Symptoms / fix" line to the agent doc so the next person doesn't have to re-derive.

## 4. Monitor

Built up over PR #243 → #247 → ongoing.

### What we have today

- **Sentry** for backend + frontend errors. Coord-scrub hook (`backend/app/sentry_scrub.py`) redacts raw lat/lon from URLs, span names, transactions, breadcrumbs. PR #243.
- **Heat-quality alerts** (`backend/app/services/heat_quality.py`). After every PMTiles rebuild, checks 4 monitored regions (Montpellier z17, Anduze corridor, Lyon, Marseille). Threshold `grid_fallback_ratio > 0.20` or `isolated_ratio > 0.05` → Sentry `capture_message` at WARNING. PR #247.
- **GCP uptime checks** on `/healthz` (configured in `infra/terraform/monitoring.tf`).
- **Cloud Logging** dashboards for `cloud_run_revision` + `cloud_run_job` error rates.

### What's queued (not blocking, but should ship)

- **SLO targets** in `infra/terraform/monitoring.tf`:
  - p99 `/routing` GET < 200 ms
  - p99 MVT tile served < 500 ms
  - p99 `/share/{id}/og-image` < 1 s
- **RGPD consent banner** before Sentry initialises — see open audit item.

### Alerting principles

- **Page on user-visible regressions, not infrastructure events.** A Cloud Run cold start isn't an alert; a `/routing` 5xx-rate spike is.
- **Every alert has a runbook entry**. If the on-call (= the maintainer today) opens an alert and can't find a runbook in `docs/`, the alert should be either deleted or have a runbook written.
- **Thresholds are calibrated against a baseline measurement**, never invented. The heat-quality thresholds (20% grid-fallback, 5% isolated) come from measured pre-PR-#250 baselines on Montpellier z17.

## House style

A few non-negotiable details, mostly from past incidents:

- **Never push to `main` directly.** Always PR. (CLAUDE.md project memory.)
- **Make destructive recipes MUST be single-shell.** `@if … exit 0; fi` split across two `@`-prefixed lines does NOT short-circuit — caused a prod wipe on 2026-05-14. The fix lives in [`docs/prod-rebuild-runbook.md`](prod-rebuild-runbook.md) as "Lessons learned".
- **GCP actions verify BOTH `gcloud config` AND ADC** before touching prod. The `gcp-precheck` target in the Makefile is mandatory upstream of every `prod-*` recipe.
- **Crouzet methodology** is the hard line. Stored GPX coordinates are verbatim. Map-matching results inform `match_confidence` / `match_source` columns but never overwrite the original geometry. Aggregates (elevation_gain_m, surface_type) can be re-derived from coords; coords cannot be re-derived from aggregates.
- **K-anonymity** in prod is `K=2`. Any read of `heat_edges` for a non-personal endpoint MUST gate on `user_count >= K`.

## Onboarding a contributor

If you're new here:

1. `make up` — full stack runs locally (Docker Compose + admin@admin / admin seed).
2. Read [`docs/prod-rebuild-runbook.md`](prod-rebuild-runbook.md) — even if you'll never run it, the gotchas are educational.
3. Read the agent doc for the area you'll touch first.
4. Open a draft PR early. Reviewers want to see direction, not finished work.
5. `make test` + `make motto` before pushing.

## When to ignore this doc

When it's wrong. If something here contradicts the code, the code wins and you should send a PR updating this doc.
