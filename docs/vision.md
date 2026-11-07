# Vision

## Cartographier ensemble, pédaler librement

Chemins Communs est une plateforme open source de cartographie multi-sport (route, gravel, VTT, off-road, running) construite sur des principes de communs numériques.

## Pourquoi ce projet existe

La plupart des outils de routage (Strava, Komoot, etc.) sont propriétaires. Ils collectent d'énormes quantités de données d'usage des sentiers mais ne partagent pas les connaissances qui en résultent.

Chemins Communs construit une **heatmap communautaire ouverte** : les traces consenties alimentent une carte de popularité publiée sous ODbL, que chacun peut exporter vers l'outil de routage de son choix (gpx.studio, BRouter, GraphHopper…). Le projet n'est pas (plus) un moteur de routage : le routeur in-app a été retiré.

## Principes fondateurs

### La cartographie est un bien commun

Les chemins que nous pédalons appartiennent à tous. Les données collectives que nous générons doivent rester libres et ouvertes.

### Vos traces vous appartiennent

Vos activités personnelles sont privées par défaut. Vous choisissez ce que vous contribuez à la heatmap communautaire. Contribution = opt-in explicite.

### Plus on est nombreux, plus c'est fiable

La heatmap repose sur la loi des grands nombres : chaque trace partagée renforce le signal des chemins réellement pratiqués, y compris les « lignes de désir » absentes d'OSM.

### La transparence est non négociable

Les algorithmes, les données communes et les règles de confidentialité sont publics et auditables. Le code source est sous licence AGPL-3.0, les données communautaires sous ODbL 1.0 (avec une licence commerciale en parallèle, voir le [README](../README.md#-licence)).

## Ce que nous refusons

- Scraping de services tiers ou usage d'APIs privées
- Reproduction ou import de heatmaps propriétaires (Strava, Komoot, etc.)
- Partage de données personnelles sans consentement explicite
- Classements opaques ou résultats sponsorisés

## Différence avec les outils existants

Les heatmaps des grandes plateformes sont propriétaires et payantes. Chemins Communs publie la sienne en données ouvertes, à partir de traces dont les contributeurs ont explicitement autorisé le partage, et la rend réutilisable comme calque ou export dans n'importe quel outil.
