# Runbook — reconstruire la heatmap commune (prod)

Quand l'utiliser : la carte commune ne reflète pas des imports récents, après
une suppression RGPD, ou après un changement de code du build. Durée : un
build complet prend de l'ordre de 30 à 40 min (job 8Gi).

Contexte : le build est déclenché par des événements (fin de lot
`/imports/files`, drain d'archive, tâche Cloud Tasks `artefact-rebuild`,
suppressions) — voir [`../architecture.md`](../architecture.md). Le seul
filet périodique est **hebdomadaire** (samedi 11:00 heure de Paris). Un déclenchement
manqué ou absorbé par l'anti-rafale peut donc laisser la carte en retard
jusqu'à une semaine : relancer à la main (ci-dessous).

## 1. État actuel du pointeur

```bash
curl -s https://tiles.chemins-communs.fr/heatmap-display.json | python3 -m json.tool
```

Champs utiles (`_upload_to_export_bucket`, `backend/app/jobs/build_pmtiles.py`) :

- `snapshot_started_at` — début du snapshot DB du build publié. C'est la clé du
  compare-and-set. Absent sur un pointeur écrit avant l'introduction du CAS :
  il est alors traité comme plus ancien que tout nouveau build.
- `captured_at` — heure de publication.
- `latest` / `latest_url` — snapshot immuable que `/map` charge.
- `k_anonymity` — plancher réellement appliqué (`HEATMAP_MIN_USERS` en raw).

## 2. Lancer le build à la main

Compte GCP du projet `common-trails` uniquement.

```bash
gcloud run jobs execute common-trails-build-pmtiles-prod --region=europe-west1
# ajouter --wait pour bloquer jusqu'à la fin
gcloud run jobs executions list --job=common-trails-build-pmtiles-prod --region=europe-west1 --limit=3
```

Le job porte déjà l'env raw (`job_env_for` dans `scripts/deploy-prod.sh`) :
`HEATMAP_DISPLAY_SOURCE=raw`, `HEATMAP_MIN_USERS=1`, `TRACE_MASK_METERS=200`,
`HEATMAP_RASTER_PYRAMID=true`, `HEATMAP_PUBLIC_BASE_URL`. Ne pas passer
d'overrides d'env à la main : le prochain déploiement réasserte ces valeurs.

## 3. Builds concurrents : sans danger

Lancer un build pendant qu'un autre tourne (ou qu'un drain en déclenche un)
est sûr. La publication est un **compare-and-set** sur `snapshot_started_at`
(`_publish_pointer_cas`) : un build ne publie que si son snapshot est plus
récent que celui du pointeur ; un build plus ancien qui finit en dernier
téléverse son propre snapshot épinglé mais ne touche ni au pointeur, ni à la
copie mutable, et ne purge rien. Seuls la pyramide raster et le GeoJSONL
(« dernier qui écrit gagne ») peuvent rester un build en retard jusqu'au
suivant.

## 4. Vérifier

```bash
curl -s https://tiles.chemins-communs.fr/heatmap-display.json | python3 -m json.tool
# snapshot_started_at / captured_at doivent être postérieurs au lancement
curl -sI "$(curl -s https://tiles.chemins-communs.fr/heatmap-display.json | python3 -c 'import sys,json;print(json.load(sys.stdin)["latest_url"])')" | head -5
```

Puis ouvrir `https://chemins-communs.fr/map` (rechargement forcé) : `/map`
suit le pointeur (`frontend/lib/cdn-cache.ts`).

Si le pointeur n'a pas bougé :

- lire les logs de l'exécution (`gcloud run jobs executions describe …`,
  Cloud Logging) : un message `pointer NOT updated: live snapshot … is not
  older than ours` signifie qu'un build plus récent a publié avant — normal ;
- un `signal 9` / OOM indique une mémoire du job inférieure à 8Gi — la
  réassertion se fait via `deploy-prod.sh` (`job_memory_for`), pas à la main.

## Notes

- Chaque build réussi purge les anciens snapshots immuables (garde le sien,
  celui du pointeur et ceux créés après son départ) : une trace supprimée ne
  survit pas dans un vieux snapshot sur notre bucket.
- Dev local : voir [`../local-testing.md`](../local-testing.md) §3.
