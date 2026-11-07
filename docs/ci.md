# CI / CD

Trois workflows GitHub Actions dans `.github/workflows/`.

## `ci.yml` — sur chaque PR vers `main` et chaque push sur `main`

| Job (nom affiché) | Contenu | Requis pour merger |
|---|---|---|
| **Lint** | `ruff check backend` (`astral-sh/ruff-action`) | oui |
| **Backend tests** | Build de l'image via `docker-compose.backend.yml`, attente `/healthz` + `/startup-status`, puis `pytest -q -m "not slow"` (`TEST_MODE=true`) — timeout 25 min | oui |
| **E2E tests (shard 1/2)**, **E2E tests (shard 2/2)** | Backend en compose + `npm run build` du frontend + fixture `e2e/fixtures/heatmap-display.pmtiles` copiée dans `out/`, puis Playwright (`e2e/`) en 2 shards — timeout 20 min | oui |
| **Frontend unit** | `npx tsc --noEmit` + Vitest (`npm run test`), sans Docker, ~1 min | **non** (pas encore dans la protection de branche) |

Import d'archive complète : un export Strava/Garmin **synthétique** passe
par tout le pipeline dans `Backend tests` (`test_archive_import_e2e.py`) et dans
les shards E2E (`archive-import-synthetic.spec.ts`, qui lance le vrai drain via
`E2E_ARCHIVE_DRAIN_CMD`) — voir [archive-import-testing.md](archive-import-testing.md).

Checks requis par la protection de `main` : `Lint`, `Backend tests`,
`E2E tests (shard 1/2)`, `E2E tests (shard 2/2)`. La branche est en mode
strict (à jour avec `main` avant merge).

### Runners figés sur `ubuntu-24.04`

`ci.yml` et `deploy-gcp.yml` utilisent `runs-on: ubuntu-24.04` (PR #32).
`ubuntu-latest` pointe désormais sur 26.04, que Playwright 1.51 ne supporte
pas (« Playwright does not support chromium on ubuntu26.04-x64 ») : les shards
E2E tombaient à l'installation du navigateur. Le CD est figé aussi pour qu'un
merge ne déploie pas depuis une image de runner non testée. (`release-please.yml`
reste sur `ubuntu-latest`.) Ne dé-figer qu'après montée de version de Playwright.

### Piège `docker save` (cache d'image)

Les jobs Backend et E2E mettent l'image backend en cache (`docker save` →
`actions/cache`). Les Docker récents refusent un ID d'image hexadécimal nu
comme nom de dépôt (« cannot specify 64-byte hexadecimal strings ») ;
`docker compose images -q` renvoie justement un ID nu. Le workflow le
re-préfixe :

```bash
IMAGE_ID=$(docker compose -f docker-compose.backend.yml images -q backend)
docker save "sha256:${IMAGE_ID#sha256:}" -o /tmp/docker-image.tar
```

Sans ce préfixe, les jobs tombaient avant le premier test dès que le cache
était froid.

## `deploy-gcp.yml` — CD en production

Déclenché sur **chaque push sur `main`** (donc chaque PR mergée) et en
`workflow_dispatch` (paramètre `tag` optionnel). Concurrence `deploy-prod`,
sans annulation d'un déploiement en cours. Étapes :

1. Auth GCP (`GCP_SA_KEY`), Node 22.
2. Build de l'export statique avec `NEXT_PUBLIC_API_URL=""` et
   `NEXT_PUBLIC_HEATMAP_URL=https://tiles.chemins-communs.fr/heatmap-display.pmtiles`,
   copié dans `backend/static-frontend`.
3. Build + push de l'image `api:<sha>` (linux/amd64) dans Artifact Registry.
4. `DEPLOY_YES=1 bash scripts/deploy-prod.sh <sha>` : services web + worker et
   synchro des 6 jobs (image, mémoire, env, secrets). Pas de terraform.
5. Healthchecks post-déploiement : web `https://chemins-communs.fr/api/health/live`,
   worker `/readyz` (vérifie la connexion DB). Un échec rend le CD rouge.

Doctrine de déploiement : [`prod-deploy.md`](prod-deploy.md).

## `release-please.yml`

Sur push `main`, `googleapis/release-please-action` maintient une PR de release
(versions + `CHANGELOG.md`) à partir des messages de commit — d'où les
**conventional commits** (`feat:`, `fix:`, `docs:`, `chore:`…). Configuration :
`release-please-config.json`, `.release-please-manifest.json`.

## Reproduire en local

Voir [`local-testing.md`](local-testing.md) et [`CONTRIBUTING.md`](../CONTRIBUTING.md) :
`ruff check backend/`, `make test`, `cd frontend && npx tsc --noEmit && npm run test`,
puis les specs Playwright concernées.
