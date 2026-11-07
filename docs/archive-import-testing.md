# Tester l'import d'une archive complète

Le dépôt est public : **aucun export réel n'est jamais commité**. Deux
niveaux de filet :

## 1. Automatique (CI, chaque PR) — export synthétique

`backend/tests/synthetic_exports.py` (stdlib seule, déterministe) construit à
la volée un export Strava et un export « Garmin » synthétiques qui reproduisent
tous les pièges connus : dossier racine `export_123/` + `__MACOSX/`,
`activities.csv` aux vrais en-têtes (colonnes dupliquées, description de
200 000 caractères, ligne sans `Filename`), types hors périmètre (Workout,
Swim, Surf, Weight Training), GPX / FIT / FIT.gz dont le FIT « Invalid field
size 1 for type uint32 » (repli fitdecode) et un FIT tronqué, `routes/`
(itinéraires sans horodatage, l'un typé `running`), doublon octet pour octet,
trace sans géométrie, fichier absent du CSV, fichier listé sans horodatage
(daté par `activities.csv`) ; côté Garmin, zip imbriqué, FIT avec sport /
sub_sport (dont un virtuel et un sans sport) et un GPX sans aucune date
(importé dans « Ma heatmap », jamais public, ligne dédiée dans l'email).

```bash
python3 backend/tests/synthetic_exports.py strava /tmp/strava.zip   # imprime le résultat attendu (JSON)
```

- **Backend tests** — `backend/tests/test_archive_import_e2e.py` : init → PUT →
  complete (comme le navigateur) → `app.jobs.ingest_pending_archives.main()`,
  puis comptes exacts, raison de chaque saut, sport de chaque activité,
  aucune activité issue de `routes/`, `display_geojson` rempli, emails de début
  et de fin, ping de rebuild, et re-dépôt idempotent (0 importée, email honnête).
- **E2E** — `e2e/tests/archive-import-synthetic.spec.ts` : dépôt du zip dans la
  vraie dropzone (`/strava`), récap avant envoi, carte « Archive reçue », puis
  en CI le drain réel (`E2E_ARCHIVE_DRAIN_CMD`) et les sorties dans « Ma
  heatmap » (`/me/activities`). Sans cette variable (run local), le spec
  s'arrête à la fin de l'upload : il ne vide jamais la file d'un stack de dev.

Un nouveau piège trouvé en vrai → l'ajouter au générateur + à `expected`.

## 2. Manuel (local uniquement) — un vrai export

```bash
make e2e-import-real ARCHIVE=/chemin/vers/export.zip            # REDROP=1 : re-dépôt (idempotence)
```

`scripts/e2e-import-real.sh` démarre un PostGIS et un backend jetables sur un
réseau docker `--internal` au nom unique (aucun port hôte, le stack partagé
8787/3787/5487 n'est jamais touché, tout est supprimé à la sortie), monte le
code de l'arbre de travail en lecture seule et lance le vrai drain (calcul de
heat activé, limite mémoire 8 Gi comme le job prod, `E2E_IMPORT_MEM` pour la
changer). Il affiche : comptes, sauts par raison, erreurs agrégées, activités
par sport (et sans `display_geojson`), pings de rebuild, emails, durée totale et
par fichier, pic mémoire (RSS). Rien ne sort du conteneur (emails et ping
capturés, pas de bucket). Ne jamais commiter l'archive ni la sortie.

L'image backend est taguée par un hash de `backend/pyproject.toml` +
`backend/Dockerfile` : un changement de dépendances la reconstruit
automatiquement. `E2E_IMPORT_REBUILD=1` force la reconstruction,
`E2E_IMPORT_IMAGE=…` impose une image. Sans swap (`--memory-swap` =
`--memory`, comme Cloud Run) : un dépassement est signalé « OOM-KILLED »
(code 137).
