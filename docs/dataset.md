# Sources de données

Le graphe de routage est construit à partir de trois sources. Il **n'inclut pas** le réseau routier OSM complet.

## Heatmap communautaire (~22K segments)

Traces GPS partagées par les utilisateurs via Strava OAuth ou import GPX/ZIP.

- Table : `heat_edges`
- Anonymisation : K-anonymité (K=2 en production, K=1 en dev)
- Licence : ODbL 1.0
- Score de popularité : `heat_score = min(1.0, log₂(1 + user_count) / 5.0)`
- Saturation à ~31 utilisateurs distincts

## Pistes DFCI (~51K segments)

Réseau de Défense des Forêts Contre l'Incendie, tracé depuis OpenStreetMap (tag `ref:FR:DFCI`).

- Table : `dfci_edges`
- Sources : IGN BD TOPO (import national) + Overpass API (Hérault)
- Couverture : sud de la France (Hérault complet, extensions possibles)
- Import idempotent : skip si des edges existent déjà en base

## Sentiers balisés (~48K segments)

Réseaux de randonnée et cyclotourisme tracés depuis OpenStreetMap.

- Table : `trail_edges`
- Réseaux importés :

| Réseau | Tag OSM | Score routage |
|--------|---------|---------------|
| GT (Grande Traversée VTT) | `network=lwn` + `ref=GT*` | 1.0 |
| GR / GRP (Grande Randonnée) | `network=lwn/rwn` + `ref=GR*` | 0.80 |
| PR (Promenade et Randonnée) | `network=lwn` + `ref=PR*` | 0.50 |
| EuroVelo | `network=icn` + `ref=EV*` | 0.90 |

- Import idempotent : skip si des edges existent déjà en base

## Enrichissement des surfaces

Les surfaces (asphalte, gravier, terre, roche) sont déterminées par deux méthodes :

1. **Overpass API** : tags OSM `surface`, `highway`, `tracktype` pour les heat_edges
2. **Jointure spatiale PostGIS** : les heat_edges sans surface héritent de la surface des DFCI/trail edges proches (< 30m)

## Ce qui n'est PAS dans le graphe

- Le réseau routier OSM complet (routes départementales, nationales, etc.)
- Les données Strava heatmap (propriétaires — utilisation interdite)
- Les données Komoot ou autres services tiers
