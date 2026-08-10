# Prod flip runbook — activer le pivot conformité (upload = commun)

> Opération **supervisée** (Paul présent). Autorisée par Paul (2026-07-12 :
> « ok pour le flip prod »). NON exécutée la nuit à dessein — voir « pourquoi
> supervisé » plus bas. Objectif : passer la heatmap commune prod d'une source
> API (non conforme) à une source **uploads manuels consentis** (#451 + #453),
> sans jamais laisser le site pire.

## Pourquoi supervisé (pas exécuté en autonomie nocturne)
1. **La rebuild truthful exige l'upload consenti de Paul** — le sens même du
   pivot est un consentement réel, pas fabriqué. Requiert Paul.
2. **Aucune preuve locale possible** au moment du staging (Docker Desktop
   wedged) → le tout premier ingest de la vraie archive (1406 fichiers :
   853 gpx + 545 fit.gz + 6 fit) ne doit pas se faire directement en prod sans
   dry-run (règle repo : reproduce-locally-first).
3. `rebuild_heatmap` fait **TRUNCATE puis rebuild depuis `activities WHERE
   source='manual_upload'`** (#453, rebuild_heatmap.py:142). Le lancer AVANT
   qu'il y ait des activités `manual_upload` en prod ⇒ **heatmap VIDE**.

## 🚨 Bloquant pré-vol découvert (à corriger AVANT le flip)
Le worker `app.jobs.ingest_pending_archives` (drain de la file
`pending_archive_files`, #451) **n'a pas de job Cloud Run prod** (absent du
tableau `JOBS` de `scripts/deploy-prod.sh`). Sans lui, un upload d'archive
s'enfile mais rien ne l'ingère.
- **Fix (petite PR infra)** : ajouter `common-trails-ingest-pending-archives-prod`
  au tableau `JOBS`, créer le job (`gcloud run jobs create … --image=$IMAGE
  --command python --args -m,app.jobs.ingest_pending_archives,--limit,N,--pace,S`),
  et un déclencheur (scheduler périodique OU exécution manuelle post-upload).
- Vérifier aussi qu'aucun scheduler équivalent n'existe déjà.

## Séquence (ordre = jamais de heatmap vide en ligne)
0. **Preuve locale d'abord** (Docker réparé) : `make heatmap-restore` puis
   ingérer l'archive réelle via le flux #451 en local → heatmap → export ;
   confirmer **0 perte FIT** (les 545 fit.gz ingérés) + sports classés.
1. **Build image** (pas de Docker local requis) :
   `gcloud builds submit backend --tag $REPO/api:prod-<date>-<hhmm>`
   (inclut #451 + #453 + le nouveau job + la PR copie « pourquoi » si mergée).
2. **Deploy** : `scripts/deploy-prod.sh prod-<date>-<hhmm>` — vérifier le diff
   d'env, confirmer. Sync tous les jobs (dont le nouveau).
   → À ce stade : nouveaux syncs API ne nourrissent plus le commun ; la heatmap
   commune reste l'ANCIENNE (API-built) — non truthful mais **pas vide**. Safe.
3. **Paul uploade son archive** via l'écran d'import déployé (case consentement
   cochée) → lignes `pending_archive_files`.
4. **Drainer la file** : exécuter le job `ingest-pending-archives` (pacé) →
   activités `manual_upload` → `heat_edges` + `heat_edges_agg` (incrémental).
   Surveiller : `processed/imported/skipped/failed` ; viser ~1406 traités,
   pertes FIT = 0.
5. **Rebuild + republish** (tier bump requis) :
   - bump `common-trails-prod` → `db-custom-2-7680` (pré-autorisé, puis
     downgrade).
   - job `common-trails-rebuild-heatmap-prod` (TRUNCATE+rebuild depuis
     manual_upload) — MAINTENANT il y a de la donnée manual_upload.
   - job `common-trails-build-pmtiles-prod` → republie
     `gs://common-trails-heatmap-prod/heatmap-display.pmtiles` (+ exports).
   - **downgrade** DB → `db-f1-micro`.
6. **Vérifier** : `/readyz` (heat_edges_agg count > 0), heatmap prod se charge,
   export GeoJSON/KML non vides, comparer le volume au pré-flip.

## Rollback
- Garder l'**ancien tag d'image** (rollback service = redeploy tag précédent).
- Garder l'**ancien PMTiles** (copier avant republish :
  `gsutil cp gs://…/heatmap-display.pmtiles gs://…/heatmap-display.prev.pmtiles`).
  Restaurer = recopier l'ancien.
- Ne PAS downgrade la DB tant que la heatmap n'est pas vérifiée non-vide.

## Références
- `scripts/deploy-prod.sh` (SSOT deploy, NON terraform).
- `docs/strava/community-contribution-ux.md` (le pourquoi produit).
- Mémoire : `project_landing_plan_2026_06_18`, `reference_strava_api_program_changes_2026`.
