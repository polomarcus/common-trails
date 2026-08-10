# Vision

## Cartographier ensemble, pédaler librement

Chemins Communs est une plateforme open source de cartographie multi-sport (route, gravel, VTT, off-road, running) construite sur des principes de communs numériques.

## Pourquoi ce projet existe

La plupart des outils de routage (Strava, Komoot, etc.) sont propriétaires. Ils collectent d'énormes quantités de données d'usage des sentiers mais ne partagent pas les connaissances qui en résultent.

Chemins Communs construit un **moteur de routage communautaire** dont les données sont ouvertes (ODbL) et les algorithmes transparents.

## Principes fondateurs

### La cartographie est un bien commun

Les chemins que nous pédalons appartiennent à tous. Les données collectives que nous générons doivent rester libres et ouvertes.

### Vos traces vous appartiennent

Vos activités personnelles sont privées par défaut. Vous choisissez ce que vous contribuez à la heatmap communautaire. Contribution = opt-in explicite.

### Plus on est nombreux, plus c'est fiable

Le routage s'appuie uniquement sur les traces GPS de la communauté, les pistes DFCI et les sentiers balisés. Il n'y a pas de réseau routier OSM complet en fallback. Chaque trace partagée comble un trou dans le graphe.

### La transparence est non négociable

Les algorithmes, les données communes et les règles de confidentialité sont publics et auditables. Le code source est sous licence MIT, les données communautaires sous ODbL 1.0.

## Ce que nous refusons

- Scraping de services tiers ou usage d'APIs privées
- Reproduction ou import de heatmaps propriétaires (Strava, Komoot, etc.)
- Partage de données personnelles sans consentement explicite
- Routage opaque ou résultats sponsorisés

## Différence avec les outils existants

Contrairement aux routeurs classiques qui utilisent le réseau routier OSM complet, Chemins Communs ne route que sur des chemins **connus et validés** :

- Des cyclistes y sont déjà passés (heatmap)
- Le chemin est officiellement balisé (GR, GT, PR, EuroVelo)
- Le chemin est une piste forestière référencée (DFCI)

Quand aucun chemin connu ne relie deux points, le routeur trace une ligne droite plutôt que d'inventer un itinéraire non vérifié.
