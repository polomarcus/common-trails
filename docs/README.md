# Documentation

Index de la doc. Règle : en cas de désaccord entre un document et le code,
**le code a raison** — corriger le document dans la même PR.

## À jour (pipeline raw-trace)

**Comprendre**

- [`architecture.md`](architecture.md) — services, jobs, flux upload → drain → build-pmtiles → pointeur → `/map`, « Ma heatmap ».
- [`vision.md`](vision.md) — mission et principes.
- [`privacy-model.md`](privacy-model.md) — ce qui est publié, protections, expositions résiduelles, droits RGPD.
- [`heatmap-pipeline.md`](heatmap-pipeline.md) — build de la heatmap (raw actuel, matched hérité en référence), publication et versioning.
- [`prod-ingestion-flow.md`](prod-ingestion-flow.md) — d'un upload à la mise à jour de la carte, en prod.
- [`heatmap-third-party-integration.md`](heatmap-third-party-integration.md) — réutiliser la heatmap (calque raster, PMTiles, GeoJSONL, ODbL).
- [`UI.md`](UI.md) — pages et couches de la carte.

**Développer**

- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — flux PR, commits, tests avant push.
- [`development.md`](development.md) — stack, ports, démarrage.
- [`local-testing.md`](local-testing.md) — niveaux de tests, rebuild local de la heatmap, pièges.
- [`configuration.md`](configuration.md) — variables d'environnement (défaut code / dev / prod).
- [`ci.md`](ci.md) — jobs CI, checks requis, CD sur merge.
- [`code-practice.md`](code-practice.md) — conventions de test, de doc et de monitoring.

**Exploiter**

- [`prod-deploy.md`](prod-deploy.md) — doctrine de déploiement (`deploy-prod.sh`), split web/worker, admin.
- [`runbooks/heatmap-rebuild.md`](runbooks/heatmap-rebuild.md) — relancer le build, vérifier le pointeur.
- [`gdpr-deletion-runbook.md`](gdpr-deletion-runbook.md) — effacement complet d'un compte.
- [`email-auth-dns.md`](email-auth-dns.md) — domaine d'envoi Resend (fait).
- [`archive-import-cost-optimization.md`](archive-import-cost-optimization.md) — analyse coût du drain d'archives (plan, non déployé).

**Strava**

- [`strava/archive-signed-upload.md`](strava/archive-signed-upload.md) — upload d'archive par URL signée.
- [`strava/community-contribution-ux.md`](strava/community-contribution-ux.md) — modèle UX de contribution.
- [`strava/prod-flip-runbook.md`](strava/prod-flip-runbook.md) — historique (flip exécuté).

[`privacy.md`](privacy.md) n'est plus qu'un renvoi vers `privacy-model.md`.

## Archive

[`archive/`](archive/) — documents de l'ère matched / routage, gelés ou
supprimés, gardés pour l'historique (chacun porte un bandeau) :

- Routage et édition : `routing-architecture`, `routing-cost-model`, `spec-osm-routing-graph`, `drag-edit-design`, `drag-edit-e2e-testing-strategy`, `komoot-parity-roadmap`.
- Pipeline matched et OSM : `ingestion-pipeline`, `ingestion-pipeline-explained`, `heatmap-map-matching-plan`, `heatmap-komoot-parity`, `ops-osm-pbf-import`, `dataset`.
- Bascule raw (exécutée) : `raw-trace-cutover-runbook`, `raw-trace-heatmap-prototype`.
- Infra / ops d'avant le CD : `mac-to-prod`, `migration-runbook`, `prod-rebuild-runbook`, `scaling-100-users`, `spec-cdn-write-through`, `architecture-and-missing-steps`.
- Export : `heatmap-export-prd` (endpoints à la demande retirés).
