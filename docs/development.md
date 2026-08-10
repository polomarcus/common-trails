# Guide de développement

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| Backend | FastAPI + Python 3.13-slim |
| Base de données | PostgreSQL 17 + PostGIS 3.6 + pgRouting |
| Frontend | Next.js 16 + TypeScript + MapLibre GL |
| Tests | pytest (backend) + Playwright (E2E) |
| CI/CD | GitHub Actions + Docker Compose |
| Infra cible | GCP serverless (Cloud Run, Cloud SQL) |

## Ports de développement

| Service | Port |
|---------|------|
| Backend API | 8787 |
| Frontend | 3787 |
| PostgreSQL (host) | 5487 |

## Démarrage local

```bash
cp .env.example .env
make up
curl http://localhost:8787/healthz
open http://localhost:3787
```

Compte admin auto-créé : `admin@admin` / `admin`

## Lancer les tests

```bash
# Backend (pytest) — fast subset, ~616 tests, 1–2 min
make test
# ou directement :
docker compose exec backend pytest -q -m "not slow"

# Backend complet (slow tests inclus) — uniquement sur DB fraîche
make test-slow

# Frontend build (static export)
cd frontend && npm install && npm run build

# E2E Playwright
cd e2e && npx playwright test

# Tests unitaires client-graph (sans runner)
npx tsx frontend/lib/__tests__/client-graph.test.ts
```

**Pourquoi `-m "not slow"` par défaut ?** Les ~277 tests marqués `slow` exercent
des scans complets de `heat_edges` (publish_heatmap_cache, MVT pre-generation,
perf regression sur prod-sized data). Ils OOM-kill le container backend dès
qu'il y a > ~10 k edges en DB de dev — ce qui arrive vite avec un import Strava
réel. Pour les lancer proprement, il faut une DB neuve (`docker compose down -v`)
ou un environnement isolé.

## Principes de développement

### Simplicité et explicabilité

- Algorithmes déterministes (Dijkstra, pas de ML)
- Données locales (pas de dépendance runtime à des APIs externes)
- Routage en graphe simple avec modèle de coût multiplicatif

### Intégrité des traces (méthodologie Crouzet)

- Ne JAMAIS altérer une trace GPX importée sans consentement explicite
- Pas d'auto-reroute, pas de simplification, pas de snap
- Les analyses (surface, élévation) sont des compléments, pas des remplacements

### Frontend statique

- `output: 'export'` dans next.config — pas de `next start`
- Servir `out/` avec un serveur statique (python3 http.server, Caddy, etc.)
- Pas de SSR, pas d'API routes Next.js

### TEST_MODE

En `TEST_MODE=true` (défaut Docker Compose), tous les appels externes sont remplacés par des stubs. Aucun appel réseau vers Strava, Overpass, etc.

## Gotchas fréquents

- PostGIS upgrade nécessite `docker compose down -v` pour supprimer le volume PG
- `next start` ne fonctionne pas avec `output: 'export'`
- FastAPI `RedirectResponse` retourne 307 par défaut (pas 302)
- MapLibre 4.x : `attributionControl: true` invalide → utiliser `{}` ou omettre
- Next.js 16 met à jour automatiquement `tsconfig.json` et génère `next-env.d.ts`

## CI / Branches

- Ne jamais pusher directement sur `main`
- Branches de feature : `feat/<nom>`
- Branches CI debug : `ci-debug/<run_id>-<slug>`
- Toujours valider localement avant de pusher (build + tests + E2E)
