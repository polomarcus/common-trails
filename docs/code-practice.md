# Code practice — Chemins Communs

How we work in this codebase. Written for the open-source release: contributors who land here from a fork should read this in 5 minutes and know what's expected.

Four pillars, in order of weight: **Test → Document → Subsystem docs → Monitor.**

## 1. Test

Each layer has its own gate; CI runs all of them.

### Backend (`pytest`)

- **Hit a real database.** Tests use a Postgres container started by docker-compose; we don't mock the DB. The 2026-05-14 prod incident (silent dry-run guard skipped) was a Make-bug that pytest with a real DB would have caught — pytest with a mocked DB would not.
- **Each PR that touches ingest / routing / surface / elevation MUST add a test that demonstrates the new behavior.** Style: name the bug or invariant in the test name (`test_grid_fallback_assigns_null_osm_way_id`, `test_route_og_image_returns_png`).
- **Critical-path tests** that must stay green:
  - `backend/tests/test_perf_regression.py` — endpoint latency budgets (MVT tile <5 s, area.pb <15 s).
  - `backend/tests/test_local_dem.py` — DEM correctness (HGT parse, void detection).
  - `backend/tests/test_k_anonymity_boundary.py` — heatmap distinct-user / K gate.
  - `backend/tests/test_pipeline_patterns.py` — heat_edges ingest invariants.

### Frontend unit (`Vitest`) / E2E (`Playwright`)

- **Vitest** (`cd frontend && npm run test`) covers components and `lib/` modules (jsdom). Runs in the CI job "Frontend unit" (not a required check yet — see [`ci.md`](ci.md)).
- **Playwright** specs live in `e2e/tests/`; the two E2E shards are required checks.
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
2. **Usage** examples (`python -m app.cli.merge_dup_accounts --apply`).
3. **Idempotency claim** — "safe to re-run; ON CONFLICT DO NOTHING" or "destructive, requires CONFIRM=YES".
4. **Crouzet invariant** statement when relevant (*"never modifies stored GPX coords; only writes `elevation_gain_m`"*).

See [`backend/app/cli/merge_dup_accounts.py`](../backend/app/cli/merge_dup_accounts.py) for the canonical shape.

### Decision records

Architectural pivots live as standalone Markdown in `docs/` (index: [`docs/README.md`](README.md)). Superseded designs move to [`docs/archive/`](archive/) with a banner rather than being silently left stale. Examples:

- [`docs/privacy-model.md`](privacy-model.md) — the raw-trace privacy decision.
- [`docs/archive/prod-rebuild-runbook.md`](archive/prod-rebuild-runbook.md) — the matched-era prod rebuild sequence + post-mortem gotchas (historical).

When a decision changes, update the doc that references it AND grep for the dead reference in README + tests.

## 3. Subsystem docs

There are no in-repo agent briefs (`.claude/` is not part of the public
repository). The subsystem contracts are the docs in [`docs/`](README.md) —
[`architecture.md`](architecture.md), [`heatmap-pipeline.md`](heatmap-pipeline.md),
[`privacy-model.md`](privacy-model.md), [`prod-deploy.md`](prod-deploy.md).
When a PR changes behaviour or a `file:line` those docs cite, update them in
the same PR.

## 4. Monitor

Built up over PR #243 → #247 → ongoing.

### What we have today

- **Sentry** for backend + frontend errors. Coord-scrub hook (`backend/app/sentry_scrub.py`) redacts raw lat/lon from URLs, span names, transactions, breadcrumbs. PR #243.
- **Heat-quality alerts** (`backend/app/services/heat_quality.py`) — matched-era (they measure `heat_edges` continuity); not meaningful in raw mode.
- **CD post-deploy healthchecks** in `deploy-gcp.yml` (web `/api/health/live`, worker `/readyz`).
- **`/admin` freshness panel** fed by `heatmap_metrics` (see [`prod-deploy.md`](prod-deploy.md) §6-7).
- **Cloud Logging** dashboards for `cloud_run_revision` + `cloud_run_job` error rates.

### What's queued (not blocking, but should ship)

- **SLO targets / uptime checks** — the former terraform `monitoring.tf` is archived (`infra/terraform/monitoring.tf.archived`) and not applied.
- **RGPD consent banner** before Sentry initialises — see open audit item.

### Alerting principles

- **Page on user-visible regressions, not infrastructure events.** A Cloud Run cold start isn't an alert; an upload 5xx-rate spike is.
- **Every alert has a runbook entry**. If the on-call (= the maintainer today) opens an alert and can't find a runbook in `docs/`, the alert should be either deleted or have a runbook written.
- **Thresholds are calibrated against a baseline measurement**, never invented. The heat-quality thresholds (20% grid-fallback, 5% isolated) come from measured pre-PR-#250 baselines on Montpellier z17.

## House style

A few non-negotiable details, mostly from past incidents:

- **Never push to `main` directly.** Always PR — see [`CONTRIBUTING.md`](../CONTRIBUTING.md).
- **Make destructive recipes MUST be single-shell.** `@if … exit 0; fi` split across two `@`-prefixed lines does NOT short-circuit — caused a prod wipe on 2026-05-14. Lessons learned: [`docs/archive/prod-rebuild-runbook.md`](archive/prod-rebuild-runbook.md).
- **GCP actions verify BOTH `gcloud config` AND ADC** before touching prod. The `gcp-precheck` target in the Makefile is mandatory upstream of every `prod-*` recipe.
- **Crouzet methodology** is the hard line. Stored GPX coordinates are verbatim. Map-matching results inform `match_confidence` / `match_source` columns but never overwrite the original geometry. Aggregates (elevation_gain_m, surface_type) can be re-derived from coords; coords cannot be re-derived from aggregates.
- **Privacy gate**: prod runs the raw display with `HEATMAP_MIN_USERS=1` (K=1 during the beta) + extrémités masquées (`TRACE_MASK_METERS`, 200 m). Any non-personal read path MUST go through the same gate (`raw_trace_display.min_users()`, `trace_privacy`) and the provenance filter (`provenance.is_community_source`).

## Onboarding a contributor

If you're new here:

1. `make up` — full stack runs locally (Docker Compose + admin@admin / admin seed).
2. Read [`docs/architecture.md`](architecture.md) and the doc for the area you'll touch first.
3. Open a draft PR early. Reviewers want to see direction, not finished work.
4. `make test` + `cd frontend && npm run test && npx tsc --noEmit` + `ruff check backend/` before pushing.

## When to ignore this doc

When it's wrong. If something here contradicts the code, the code wins and you should send a PR updating this doc.
