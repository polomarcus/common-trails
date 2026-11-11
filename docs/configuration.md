# Configuration — variables d'environnement

Référence des variables qui comptent, avec trois colonnes :

- **Défaut code** : valeur quand la variable est absente (`os.environ.get(...)` dans `backend/app/`).
- **Dev (compose)** : valeur posée par `docker-compose.yml` (service `backend`/`frontend`) ; « — » = non posée, donc défaut code.
- **Prod** : valeur appliquée par `scripts/deploy-prod.sh` (bloc `--env-vars-file` des deux services, `job_env_for` pour les jobs, `SECRETS` pour Secret Manager). « — » = non posée.

`.env` est optionnel en dev : `docker-compose.yml` fournit tous les défauts. `.env.example`
ne reflète pas ces défauts (il met `TEST_MODE=false`) — ne pas s'y fier.

## Cœur

| Variable | Défaut code | Dev (compose) | Prod | Rôle |
|---|---|---|---|---|
| `TEST_MODE` | `false` | `true` | `false` | Stubs pour tous les appels externes ; désactive Cloud Tasks / emails |
| `DATABASE_URL` | — | `postgresql://postgres:postgres@db:5432/common_trails` | secret | Connexion Postgres |
| `JWT_SECRET` | — (refusé hors TEST_MODE si faible) | `dev-secret-change-in-production` | secret | Signe les sessions et les liens magiques (`backend/app/api/auth.py`) |
| `JWT_EXPIRE_MINUTES` | `129600` (90 j) | `10080` (7 j) | — → `129600` | Durée de session |
| `MAGIC_LINK_TTL_MIN` | `15` | — | — | Validité du lien magique |
| `FRONTEND_URL` | `http://localhost:3787` | `http://localhost:3787` | `https://chemins-communs.fr` | Origine utilisateur (redirections OAuth, liens email) |
| `SEED_DEMO_DATA` | vide | `true` | — | Seed du compte `admin@admin` en dev |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `20` / `10` | — | `5` / `3` | Pool SQLAlchemy par instance (db-f1-micro ≈ 22 connexions) |
| `RATELIMIT_ENABLED` | `true` | `false` | — → `true` | Rate limiting (slowapi) |
| `UPLOAD_RATE_LIMIT` | `200/hour` | — | — | Limite `/imports/files` |
| `SENTRY_DSN` | vide | vide | secret | Observabilité backend |
| `OVERPASS_ENABLED` | `true` | `false` | `false` | Appels Overpass (hérité matched) |

## Heatmap

| Variable | Défaut code | Dev (compose) | Prod | Rôle |
|---|---|---|---|---|
| `HEATMAP_DISPLAY_SOURCE` | `matched` | — | `raw` | Source de la heatmap (`raw_trace_display.raw_display_enabled`) |
| `HEATMAP_MIN_USERS` | `1` | — | `1` | Plancher d'utilisateurs distincts par cellule (mode raw) |
| `TRACE_MASK_METERS` | `200` | — | `200` | Extrémités masquées (`trace_privacy.mask_meters`) |
| `HEATMAP_RAW_BBOX` | vide | — | `-12;34;32;62` | Écarte les traces entièrement hors région (corrompues) |
| `HEATMAP_K_ANONYMITY` | `2` | `1` | `1` | **Hérité** : plancher du pipeline matched (`make pmtiles`, `/internal/artefacts/rebuild`) |
| `HEATMAP_KEEP_GRID_FALLBACK`, `HEATMAP_GRID_FALLBACK_MIN_UC` | — | — | `true`, vide | Hérité matched (lignes de désir) |
| `HEATMAP_RASTER_PYRAMID` | `false` | — | `true` | Publie la pyramide raster XYZ (calque) |
| `HEATMAP_GCS_BUCKET` | vide (pas d'upload) | vide | `common-trails-heatmap-prod` | Bucket de publication PMTiles / pointeur |
| `HEATMAP_ARTEFACTS_BUCKET` | vide | — | `common-trails-heatmap-prod` | Upload du rebuild worker ; doit égaler `HEATMAP_GCS_BUCKET` |
| `HEATMAP_PUBLIC_BASE_URL` | `https://storage.googleapis.com/<bucket>` | — | `https://tiles.chemins-communs.fr` | Base des URLs publiées (pointeur, raster, emails) |
| `HEATMAP_DEV_PMTILES_URL` | — | `http://localhost:3787/heatmap-display.pmtiles` | — | Redirection d'export en dev |
| `HEATMAP_EXPORT_ISOLATED` | activé (sauf sous pytest) | — | — | Export raw dans un process enfant (`build_pmtiles._run_raw_export`) |
| `MY_HEATMAP_FILES_ENABLED` | `false` | — | `true` (défaut du script de déploiement) | Fichier PMTiles privé par utilisateur pour « Ma heatmap » ([doc](ma-heatmap-fichier-prive.md)) ; coupé → `/me/activities` |
| `MY_HEATMAP_FILES_USERS` | vide (= tous) | — | vide | Liste blanche d'ids (`,` ou `;`) pour un déploiement progressif |
| `MY_HEATMAP_BUCKET` | vide | — | `common-trails-my-heatmap-prod` | Bucket **privé** des fichiers ; refusé s'il égale un bucket public de heatmap |
| `MY_HEATMAP_LOCAL_DIR` | vide | à définir pour tester | — | Stockage disque (dev/tests uniquement) |
| `MY_HEATMAP_DEBOUNCE_S` | `60` | — | `60` | Fenêtre de débounce des rebuilds par utilisateur |
| `INTERNAL_MY_HEATMAP_HANDLER_URL` | vide | — | `<worker>/internal/my-heatmap/rebuild` | Cible Cloud Tasks (file `CLOUD_TASKS_ARTEFACT_QUEUE`) |
| `MY_HEATMAP_SHARE_BASE_URL` | vide (= `X-Forwarded-Proto` + `Host`) | — | vide | Origine absolue des liens « Partager ma heatmap » (`/s/{jeton}/…`, [doc §10](ma-heatmap-fichier-prive.md)) |

## Imports, jobs et déclencheurs

| Variable | Défaut code | Dev (compose) | Prod | Rôle |
|---|---|---|---|---|
| `UPLOADS_BUCKET` | vide | — | `common-trails-common-trails-uploads-prod` | Archives uploadées par URL signée |
| `MAX_ARCHIVE_BYTES` | 512 Mo | — | `2147483648` (2 Go) | Taille max d'archive (services + job drain) |
| `MAX_GPX_SIZE_BYTES` | 25 Mo | — | — | Taille max d'un GPX isolé |
| `GCP_PROJECT` / `GCP_REGION` | vide | — | `common-trails` / `europe-west1` | Déclenchement `jobs:run` (`backend/app/services/run_jobs.py`) |
| `INGEST_ARCHIVES_JOB_NAME` | — | — | `common-trails-ingest-pending-archives-prod` | Job déclenché à `/imports/strava-archive/complete` |
| `BUILD_PMTILES_JOB_NAME` | — | — | `common-trails-build-pmtiles-prod` | Job de build déclenché après import |
| `BUILD_PMTILES_DEBOUNCE_S` | `600` | — | — | Anti-rafale du déclencheur `/imports/files` (par instance) |
| `DRAIN_PMTILES_REPING_S` | `1800` | — | — | Intervalle min. entre deux pings de build dans un même drain |
| `CLOUD_TASKS_LOCATION`, `CLOUD_TASKS_QUEUE`, `CLOUD_TASKS_INVOKER_SA` | vide | — | `europe-west1`, `common-trails-heat-compute-prod`, SA invoker | Vides → calcul en ligne (pas de Cloud Tasks) |
| `INTERNAL_HEAT_HANDLER_URL`, `INTERNAL_ARTEFACT_HANDLER_URL` | vide | — | URL `.run.app` du **worker** | Audience OIDC des tâches lourdes |
| `CLOUD_TASKS_ARTEFACT_QUEUE` | vide | — | `common-trails-artefact-rebuild-prod` | File Cloud Tasks |

Les URLs `.run.app` doivent correspondre octet pour octet à l'audience OIDC
(voir [`prod-deploy.md`](prod-deploy.md) §8).

## Email

| Variable | Défaut code | Dev (compose) | Prod | Rôle |
|---|---|---|---|---|
| `ADMIN_NOTIFY_EMAIL` | *(vide : aucune notification)* | — | `paul@epauler.fr` | Destinataire du mail « nouveau compte confirmé » (première connexion d'un compte) |
| `EMAIL_FROM` | `Chemins Communs <onboarding@resend.dev>` | — | `Chemins Communs <no-reply@chemins-communs.fr>` | Expéditeur (services + jobs drain/rappel) |
| `RESEND_API_KEY` | vide (envoi no-op) | — | secret | API Resend |

## Frontend (compilé dans l'export statique)

Les `NEXT_PUBLIC_*` sont figées **au build**. En prod elles sont posées par
`.github/workflows/deploy-gcp.yml` ; un build manuel doit les repasser.

| Variable | Dev (compose) | Prod (`deploy-gcp.yml`) | Rôle |
|---|---|---|---|
| `NEXT_PUBLIC_API_URL` | `${API_URL:-http://localhost:8787}` | `""` (même origine) | Base de l'API |
| `NEXT_PUBLIC_HEATMAP_URL` | — | `https://tiles.chemins-communs.fr/heatmap-display.pmtiles` | PMTiles commune ; le pointeur `heatmap-display.json` est lu à côté (`frontend/lib/cdn-cache.ts`) |
| `NEXT_PUBLIC_MAP_STYLE_URL` | `${MAP_STYLE_URL:-}` | — | Style MapLibre personnalisé |
| `NEXT_PUBLIC_SENTRY_DSN` | vide | — | Sentry frontend |
| `NEXT_PUBLIC_MAP_OFFLINE` | — | — (CI : `true`) | Fond de carte hors-ligne pour les E2E |

## Jobs Cloud Run (prod)

`deploy-prod.sh` synchronise l'image des 6 jobs et y **fusionne** (`--update-env-vars`)
seulement les variables qui leur sont nécessaires (`job_env_for`) :
`build-pmtiles` reçoit le jeu heatmap raw + `HEATMAP_RASTER_PYRAMID` +
`HEATMAP_PUBLIC_BASE_URL` ; `ingest-pending-archives` reçoit le jeu raw + uploads,
`MAX_ARCHIVE_BYTES`, `BUILD_PMTILES_JOB_NAME`, `EMAIL_FROM`. Mémoire assertée :
8Gi pour ces deux jobs, 512Mi pour les autres (`job_memory_for`).
