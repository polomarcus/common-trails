# Runbook — cache de géométrie de « Ma heatmap » (`display_geojson`)

## À quoi ça sert

« Ma heatmap » (`/map`, couche cyan) charge toutes les traces de l'utilisateur
d'un coup via `GET /me/activities?limit=5000`. Servies en pleine précision, elles
pèsent des centaines de Mo pour un gros compte et le chargement n'en finit pas.

Chaque activité garde donc, à côté de sa trace d'origine (`geometry_geojson`,
**jamais modifiée**), une version allégée pour l'affichage
(`activities.display_geojson`, migration 0067) :

1. coupure aux trous > 2,5 km (pas de trait droit entre deux segments) ;
2. Douglas-Peucker 2 m, altitude pondérée (5 m de dénivelé ≡ 2 m à plat) ;
3. coordonnées à 5 décimales.

Code : `backend/app/services/display_geometry.py`. Ordre de grandeur mesuré :
1 317 sorties = 20,8 Mo brut / 6,3 Mo gzip. En prod (backfill du 2026-11-09),
les gros comptes sont passés de 290 → 39 Mo, 361 → 33 Mo et 369 → 20 Mo
(JSON brut servi), soit ×7 à ×18 selon la densité des traces.

## Qui le remplit, et quand

| Cas | Ce qui se passe |
|---|---|
| **Nouvel import** (fichier, archive Strava/Garmin, Strava API) | calculé à l'écriture, `ingest._display_geojson` — rien à faire |
| **Géométrie remplacée** (flux Strava plus précis, promotion d'un doublon fichier ↔ Strava) | recalculé au même endroit |
| **Fusion de comptes** | rien : les lignes changent de propriétaire, pas de géométrie |
| **Changement de sport** | rien : le cache ne dépend que de la géométrie |
| **Activités importées avant le 2026-11-07** (avant #40) | **NULL** → backfill une fois (ci-dessous) |
| **Changement de l'algorithme** (seuils, précision) | backfill avec `--recompute` |

Tant qu'une ligne est NULL, l'API sert la trace d'origine découpée aux trous :
correct, juste lourd. Rien ne casse, c'est seulement lent.

## Vérifier l'état en prod (lecture seule)

Le job `common-trails-ingest-pending-archives-prod` a l'image et l'accès DB ;
sa commande est `python`, on remplace seulement les arguments
(`--command` n'existe pas sur `jobs execute`) :

```bash
gcloud run jobs execute common-trails-ingest-pending-archives-prod \
  --region=europe-west1 --account=paleclercq@gmail.com --project=common-trails \
  "--args=^@^-c@import sys,time;sys.stdout.reconfigure(line_buffering=True)
from sqlalchemy import text
from app.db.session import SessionLocal
db=SessionLocal()
print('DISPSTAT', db.execute(text('SELECT count(*), count(*) FILTER (WHERE display_geojson IS NULL) FROM activities WHERE geometry_geojson IS NOT NULL')).one())
time.sleep(10)" --wait
```

Lire la ligne `DISPSTAT` dans les logs de l'exécution (Cloud Logging, filtre
`labels."run.googleapis.com/execution_name"`). Sans le `line_buffering` + le
`sleep` final, une partie de la sortie se perd.

## Lancer le backfill (prod)

```bash
gcloud run jobs execute common-trails-ingest-pending-archives-prod \
  --region=europe-west1 --account=paleclercq@gmail.com --project=common-trails \
  --args=-m,app.cli.backfill_display_geojson,--pace,0.5 --wait
```

- **Idempotent et reprenable** : seules les lignes NULL sont traitées ; relancer
  après une coupure reprend où ça s'est arrêté.
- **Sans risque pour les traces** : seul `display_geojson` est écrit, avec un
  garde md5 sur `geometry_geojson` (une trace modifiée entre la lecture et
  l'écriture n'est pas écrasée).
- Le job a un timeout de 6 h et 1 retry ; le backfill complet de 8 335 lignes a pris 9 min. S'il est coupé, le relancer reprend où il s'est arrêté.
- `--pace 0.5` laisse respirer la base db-f1-micro entre deux lots de 100.
- Options : `--user-id <uuid>` (un seul compte), `--limit N`, `--recompute`.
- Pas de rebuild PMTiles à faire : la carte publique n'utilise pas ce cache.

Après : recharger `/map` connecté, « Chargement de tes traces… » doit passer
de plusieurs dizaines de secondes à quelques secondes.
