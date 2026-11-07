# Contributing to Chemins Communs

Contributions are welcome — a bug fix, better cartography, docs. Start with
[`docs/README.md`](docs/README.md) and [`docs/architecture.md`](docs/architecture.md);
`make up` brings the whole stack up locally ([`docs/local-testing.md`](docs/local-testing.md)).

## Workflow

1. **Branch from `main`, never commit or push to `main`.** Name it
   `feat/<slug>`, `fix/<slug>`, `docs/<slug>`…
2. **Commit with conventional commits** — `feat: …`, `fix: …`, `docs: …`,
   `chore: …`, `test: …`, `ci: …`. `release-please` builds the version bumps and
   `CHANGELOG.md` from these messages ([`docs/ci.md`](docs/ci.md)).
3. **Sign off** your commits (`git commit -s`, Developer Certificate of Origin).
   By submitting a PR you agree your contribution is published under AGPL-3.0
   (code) and, where it includes commons data, ODbL-1.0. No CLA.
4. **Open a PR against `main`** (`gh pr create --base main`). Keep one PR per
   coherent change rather than many tiny ones.
5. **PRs are squash-merged**, once the required checks are green (`Lint`,
   `Backend tests`, both `E2E tests` shards) and the change has been reviewed.
   Merging to `main` deploys production automatically.

## Before you push

Run locally what CI will run:

```bash
ruff check backend/                                  # Python lint
make test                                            # backend pytest (fast subset)
cd frontend && npx tsc --noEmit && npm run test      # types + Vitest
cd frontend && npm run build                         # static export builds
cd e2e && npx playwright test <relevant specs>       # if you touched the UI
```

For anything user-visible, also exercise the change in the running app
(`make up`, then `docker compose up --build frontend -d` — `restart` keeps the
old build).

## Every bug fix ships a non-regression test

A fix is not done until the same PR adds a test that **fails before the fix and
passes after it**. Drive the real code path (the real handler / component), not
a copy of the logic. UI fixes get a Playwright spec, or an explicit manual-smoke
note in the PR when a spec is impractical.

## Project rules worth knowing

- **Trace integrity:** never alter an imported GPX (no auto-reroute,
  simplification or snapping of stored coordinates).
- **Privacy:** only consented `manual_upload` traces feed the community map;
  Strava-API data stays personal. See [`docs/privacy-model.md`](docs/privacy-model.md).
- **No proprietary heatmap import, no scraping.**
- **Docs follow the code:** if your change makes a doc wrong, fix the doc in the
  same PR; superseded docs move to [`docs/archive/`](docs/archive/).
