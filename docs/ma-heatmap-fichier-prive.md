# « Ma heatmap » en fichier privé par utilisateur (PMTiles)

**Statut : design + implémentation derrière flag (`MY_HEATMAP_FILES_ENABLED`,
désactivé par défaut).** Remplace à terme le chargement de toutes les traces
d'un utilisateur en un seul JSON (`GET /me/activities?limit=5000`) pour dessiner
« Ma heatmap » sur `/map`. `/me/activities` reste le chemin de repli tant que le
fichier d'un utilisateur est absent, périmé ou que le flag est coupé.

Docs liées : [`privacy-model.md`](privacy-model.md),
[`heatmap-pipeline.md`](heatmap-pipeline.md) (pointeur CAS de la heatmap
communautaire), [`gdpr-deletion-runbook.md`](gdpr-deletion-runbook.md).

---

## 1. Le problème

Le compte le plus lourd (~2 300 sorties) télécharge ~36 Mo bruts / ~10 Mo gzip
en une requête, et le navigateur parse puis ingère tout ce GeoJSON dans une
source MapLibre `geojson` (worker + index en mémoire) avant d'afficher quoi que
ce soit. Le cache compact `activities.display_geojson` (migration 0067) et
l'épissage du JSON en cache (branche `perf/me-activities-fast`) réduisent le
coût serveur, mais le volume reste **linéaire en nombre de sorties** : un
utilisateur à 14 000 sorties (il en existe un en bêta) dépasse le plafond
`limit=5000` et ne voit qu'une partie de ses traces.

## 2. Format retenu : un PMTiles par utilisateur

| | **PMTiles (vector tiles)** | Blob GeoJSON précompressé |
|---|---|---|
| Octets transférés à l'ouverture | uniquement les tuiles de la vue (en-tête 16 Ko + répertoires + ~10–20 tuiles) | tout le corpus, quelle que soit la vue |
| Croissance avec le nombre de sorties | ~constante par vue (tippecanoe simplifie / regroupe aux bas zooms) | linéaire (36 Mo → ~200 Mo à 14 k) |
| Coût navigateur | MapLibre décode des tuiles MVT dans ses workers, comme la heatmap communautaire | `JSON.parse` + indexation geojson-vt de tout le corpus |
| Plafond 5 000 | aucun — le fichier contient **toutes** les sorties | conservé (sinon payload ingérable) |
| Outillage | tippecanoe déjà dans l'image (job `build_pmtiles`), protocole `pmtiles://` déjà enregistré (`frontend/lib/pmtiles-protocol.ts`) | trivial |
| Patch local (correction de sport) | impossible dans la tuile → la popup lit le sport dans la liste | `setData` sur la feature |

Le blob précompressé ne règle que le coût serveur ; il ne règle ni le volume ni
le parse côté navigateur. **PMTiles**.

Contenu du fichier, construit depuis `activities.display_geojson` (géométrie
déjà découpée aux trous GPS, Douglas-Peucker 2 m, 5 décimales) :

- couche unique `activities`, une feature par sortie, propriétés **`id` et
  `sport` seulement** (le reste vient de la liste, §4) ;
- zooms 4 → 14 (au-delà, MapLibre sur-zoome la tuile 14 : la précision du cache
  est ~1 m, rien à gagner) ;
- `--drop-densest-as-needed` (budget tippecanoe de 500 Ko/tuile) : quand une
  tuile déborde, tippecanoe garde les traces les plus « isolées » et jette
  celles qui se superposent aux autres. Mesuré (§8) : à 2 300 sorties, une
  seule tuile à z9 et une à z10 sont élaguées, **rien à partir de z11** ; à
  14 000 sorties concentrées autour de 5 domiciles, élagage au cœur jusqu'à
  z12, **z13–z14 toujours complets**. Les traces jetées sont recouvertes par
  leurs voisines : visuellement identique, et la liste latérale contient
  toujours toutes les sorties.

À côté, un **résumé JSON** versionné (`summary.json`, gzip) porte la liste qui
alimente la barre latérale : par sortie `id, name, sport, distance_m,
elevation_gain_m, moving_time, activity_date, created_at, undated, provider,
provider_activity_id, total_photo_count`, plus `bbox`, `center` et `start`
(calculés au build, pour `fitBounds` / le saut vers la dernière sortie) — sans
aucune coordonnée de trace. Les coordonnées complètes d'une sortie ne sont
chargées qu'à sa sélection (`GET /me/activities/{id}`, déjà existant).

## 3. Stockage : un bucket GCS **privé et dédié**

- **Nouveau bucket `common-trails-my-heatmap-${ENV}`** — jamais le bucket
  public `common-trails-heatmap-${ENV}` derrière `tiles.chemins-communs.fr` :
  ces traces ne sont **pas masquées** (pas de `TRACE_MASK_METERS`) et partent
  du domicile des utilisateurs.
- Garde-fous, assertés à chaque déploiement par `scripts/deploy-prod.sh`
  (étape « 0b ») : création si absent, `uniform-bucket-level-access`,
  **`public-access-prevention = enforced`** (GCS refuse alors tout ACL ou
  binding `allUsers` / `allAuthenticatedUsers`), et un seul binding :
  `roles/storage.objectAdmin` pour le SA runtime `common-trails-api@`.
  Le déploiement **échoue** si le bucket n'est pas `enforced`.
- Garde-fou applicatif : le service refuse de démarrer l'écriture si
  `MY_HEATMAP_BUCKET` est égal à `HEATMAP_GCS_BUCKET` / `HEATMAP_ARTEFACTS_BUCKET`,
  n'envoie jamais `predefined_acl`, et pose `Cache-Control: private, no-store`
  sur les objets.
- Arborescence : `users/{user_id}/{version}.pmtiles` et
  `users/{user_id}/{version}.summary.json` — objets **immuables**.

## 4. Accès : proxy Range du backend (pas d'URL signée)

Deux options étaient sur la table :

| | **Proxy Range same-origin** (retenu) | URL signée V4 courte |
|---|---|---|
| Auth | cookie httpOnly existant, vérifié à **chaque** requête | porteur de l'URL (fuite = accès jusqu'à expiration) |
| Révocation (suppression de compte, fusion) | immédiate | à l'expiration de l'URL |
| CORS | aucun (même origine que l'API en prod) | CORS Range/ETag à poser sur le bucket privé |
| Bloqueurs | aucun | `storage.googleapis.com` est bloqué par certains bloqueurs / Firefox (raison de `tiles.chemins-communs.fr`) |
| Cache navigateur | URL stable par version → `Cache-Control: private, max-age=1 an, immutable` : la 2ᵉ ouverture de `/map` ne retélécharge rien | URL différente à chaque signature → pas de cache |
| Expiration à gérer côté front | non | oui (re-signer en cours de session) |
| Coût | les octets transitent par le service web (512 Mi, 1 instance) | GCS sert directement |

Le volume par vue est faible (quelques centaines de Ko), le cache navigateur
absorbe les rechargements : le proxy est le meilleur compromis à l'échelle
actuelle. Si le trafic grossit, l'URL signée reste possible sans changer le
format (le front passe par une `Source` pmtiles custom, un seul endroit à
changer).

Endpoints (`backend/app/api/me_heatmap.py`) :

- `GET /me/heatmap/manifest` → `{status, version, tiles_url, summary_url,
  activity_count, built_at}` où `status ∈ ready | stale | missing | disabled`.
  Le front n'utilise le fichier **que si `ready`** ; sinon `/me/activities`.
  Un `stale`/`missing` (flag actif) **replanifie** un build : auto-réparation
  si un chemin d'invalidation a été oublié.
- `GET /me/heatmap/{version}.pmtiles` — `Range` obligatoire, borné à 8 Mo,
  réponse `206` + `Content-Range`. Le chemin GCS est construit **depuis
  l'utilisateur authentifié**, jamais depuis l'URL : la version d'un autre
  utilisateur donne 404.
- `GET /me/heatmap/{version}.summary.json` — le résumé, gzip.

Côté front : une `Source` pmtiles (`frontend/lib/my-heatmap-source.ts`) qui
fait ses `fetch` avec `credentials: 'include'`, ajoutée au `Protocol` déjà
enregistré. Les trois couches `user-activities-{glow,line,hit}` gardent leurs
ids (handlers de survol/clic, bascule de visibilité, ordre au-dessus de la
heatmap communautaire inchangés) ; seule leur source passe de `geojson` à
`vector`.

## 5. Versionnement : objet immuable + pointeur en base

Même idée que le pointeur CAS de la heatmap communautaire
(`build_pmtiles._publish_pointer_cas`), mais le pointeur vit **en Postgres**,
dans la même base que les données, ce qui rend la détection de péremption
exacte et transactionnelle. Table `my_heatmap_files` (migration 0068) :

| colonne | rôle |
|---|---|
| `user_id` (PK) | |
| `data_rev` | compteur incrémenté par **trigger** à chaque changement visible des activités de l'utilisateur |
| `built_rev` | `data_rev` capturé au début du build publié |
| `version`, `activity_count`, `pmtiles_bytes`, `summary_bytes`, `built_at` | le fichier publié |
| `previous_version` | gardé un cycle pour les onglets ouverts sur l'ancienne version |
| `last_error`, `last_enqueued_at` | exploitation |

`status = ready` ⇔ `version IS NOT NULL AND built_rev = data_rev`.

Build (`my_heatmap_files.build_for_user`) :

1. lire `rev0 = data_rev` ;
2. streamer les activités (même filtre que `/me/activities`, sans limite) →
   NDJSON + résumé ;
3. `tippecanoe` → `.pmtiles` ; upload des deux objets sous une **nouvelle**
   version `r{rev0}-{8 hex}` ;
4. **CAS** : `UPDATE … SET version=…, built_rev=rev0 WHERE user_id=… AND
   built_rev < rev0`. Perdu (un build plus récent a publié) → on supprime nos
   objets. Gagné → on supprime les versions plus anciennes que `previous_version`.

Si les données changent pendant le build, `data_rev > rev0` : le fichier
publié est immédiatement `stale`, et le changement a lui-même planifié un
nouveau build.

## 6. Invalidation

### 6.1 Péremption : trigger Postgres (exhaustif par construction)

Trois triggers `FOR EACH STATEMENT` avec tables de transition sur `activities`
(`INSERT`, `UPDATE`, `DELETE`) incrémentent `data_rev` de chaque `user_id`
touché — ancien **et** nouveau propriétaire pour un `UPDATE` de `user_id`
(fusion de comptes). L'`UPDATE` ne compte que si une colonne affichée change
(`user_id, sport, name, display_geojson, geometry_geojson, distance_m,
elevation_gain_m, moving_time, activity_date, created_at, provider,
provider_activity_id, total_photo_count`).

C'est le point clé : **ORM, SQL brut, update en masse, cascade** — tous les
chemins d'écriture passent par là, y compris ceux qu'on oubliera d'ajouter
demain. Un fichier ne peut pas être servi `ready` avec des données périmées.

### 6.2 Reconstruction : planification hors requête, débouncée par utilisateur

- **Hook de session SQLAlchemy** (`after_flush` → `after_commit`) : toute
  `Activity` ajoutée, modifiée (colonne affichée) ou supprimée via l'ORM
  planifie un build pour son propriétaire **après commit** (rien sur rollback).
  Couvre : tous les ingests de `services/ingest.py` (fichier, archive, API
  Strava, webhook), la promotion de doublon (`_promote_activity_to_community`),
  la suppression (`activity_deletion.py`), la correction de sport
  (`activity_sport.py`), l'upgrade de géométrie Strava
  (`integrations_strava._gps_apply_batch`), la suppression via webhook Strava.
- **Appels explicites** là où l'ORM est contourné : fusion de comptes
  (`account_merge.py` : `UPDATE` en masse + `DELETE` SQL → rebuild du
  survivant, suppression du fichier de l'absorbé), suppression RGPD (CLI,
  objets **et** ligne supprimés — §7).
- **Débounce** : tâche Cloud Tasks nommée `my-heatmap-{user}-{fenêtre}` sur la
  file existante `common-trails-artefact-rebuild-${ENV}` (pas de nouvelle file
  à créer), programmée à la fin de la fenêtre (`MY_HEATMAP_DEBOUNCE_S`, 60 s par
  défaut). Une rafale d'imports dans la même fenêtre = un seul build ; le worker
  ne fait rien si `built_rev = data_rev`. Handler :
  `POST /internal/my-heatmap/rebuild` sur le **worker** (2 Gi), OIDC comme les
  autres.
- Sans Cloud Tasks (dev local) : `threading.Timer` débouncé en process. En
  `TEST_MODE` : no-op (les tests appellent le build directement).
- Filet : le manifest replanifie un build quand il voit `stale`/`missing`.

## 7. RGPD et fusion

- Suppression de compte (runbook manuel) : nouvelle étape
  `python -m app.cli.my_heatmap_files delete --user-id …` — supprime **tous**
  les objets `users/{id}/` et la ligne. Le trigger ne suffit pas (il marque
  périmé, il ne supprime rien).
- `python -m app.cli.my_heatmap_files sweep-orphans` : supprime les préfixes
  `users/{id}/` dont l'utilisateur n'existe plus (filet si l'étape est oubliée).
- Fusion : le fichier de l'absorbé est supprimé, celui du survivant reconstruit.

## 8. Mesures (données synthétiques, 2026-11-09)

Corpus : `backend/scripts/bench_my_heatmap.py` — sorties de ~40 km (~600 points
d'affichage avec altitude), toutes parties de 5 « domiciles » autour de
Montpellier (densité pire que réelle : tout se superpose). Le JSON synthétique à
2 300 sorties pèse 34,7 Mo bruts / 11,1 Mo gzip, cohérent avec le compte réel le
plus lourd (~36 / ~10 Mo). Octets d'une vue : `frontend/scripts/bench-my-heatmap-view.mjs`
(le client pmtiles du navigateur, fenêtre 1440×900, tuiles 512 px). Conteneur
amd64 émulé sur arm64 : les temps serveur absolus sont pessimistes, les ratios
sont ce qui compte.

| | 2 300 sorties | 14 000 sorties |
|---|---|---|
| **JSON** `/me/activities?limit=5000` | 34,7 Mo bruts, **11,1 Mo gzip**, 2,4 s serveur | 75,4 Mo bruts, **24,1 Mo gzip**, 4,9 s serveur — **5 000 sorties seulement** |
| **Fichier** : build (tippecanoe, worker) | 7,6 s → PMTiles 19,1 Mo | 44 s → PMTiles 79 Mo |
| manifest + résumé (gzip) | 155 Ko | 943 Ko |
| tuiles vue z13 (ouverture par défaut) | 356 Ko (9 requêtes Range) | 2,0 Mo (9 requêtes) |
| tuiles vue z10 / z8 | 900 Ko / 487 Ko | 913 Ko / 422 Ko |
| **Total ouverture `/map` (z13)** | **~0,5 Mo** (÷ 22) | **~3 Mo** (÷ 8, et les 14 000 sorties) |
| ≈ temps de transfert à 20 Mbit/s | JSON 4,4 s → fichier 0,2 s | JSON 9,6 s → fichier 1,2 s |
| Requête Range 16 Ko (proxy, serveur) | ~6 ms | ~4 ms |

Non mesuré ici mais à l'avantage du fichier : côté navigateur, le chemin JSON
fait ensuite `JSON.parse` de 35–75 Mo puis l'indexation geojson-vt de tout le
corpus ; le chemin fichier décode quelques tuiles MVT dans les workers MapLibre.
Une deuxième ouverture de `/map` sans changement ne retélécharge rien (versions
immuables, `Cache-Control: private, immutable`).

Le résumé (sidebar) devient le poste dominant à 14 000 sorties (943 Ko gzip) ;
s'il gêne, le paginer ou le charger après les tuiles est une suite possible.

## 9. Déploiement

1. Merge (flag coupé) : migration 0068 + triggers, bucket asserté, code dormant.
2. Backfill : `python -m app.cli.my_heatmap_files backfill --pace 1` (comme
   `backfill_display_geojson`), sur le worker ou via proxy SQL.
3. `MY_HEATMAP_FILES_USERS=<uuid de Paul>` → test réel sur un compte.
4. `MY_HEATMAP_FILES_ENABLED=true` pour tous.

Retour arrière : couper le flag → manifest `disabled` → le front repasse sur
`/me/activities` sans redéploiement du front.

## 10. Hors périmètre / suites

- `/me/cells` (couverture z14) reste chargé à part.
- Le cache `display_geojson` reste la source du build ; un changement
  d'algorithme = `backfill_display_geojson --recompute` (qui bumpe `data_rev`
  via le trigger, donc rebuild naturel au prochain affichage).
