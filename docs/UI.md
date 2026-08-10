# Interface utilisateur

## Principes

L'interface est **map-first** : la carte est l'élément central, les panneaux sont des compléments.

## Pages

| Page | Route | Description |
|------|-------|-------------|
| Landing | `/` | Carte hero plein écran, auth, sport pills, CTA |
| Carte | `/map` | Carte interactive principale, sidebar traces, routage |
| Mes itinéraires | `/me/routes` | Bibliothèque personnelle, groupes, corbeille |
| Méthode | `/methode` | Explication transparente du moteur de routage |
| Discover | `/discover` | Exploration des itinéraires publics |
| Stats | `/me/stats` | Statistiques personnelles (style Statshunter) |
| Strava callback | `/strava` | Page de retour OAuth Strava |

## Carte interactive (`/map`)

### Interactions de routage

- Clic pour ajouter un waypoint
- Drag d'un waypoint pour le déplacer
- Insert d'un waypoint sur la route (clic sur le tracé)
- Recalcul instantané (< 100ms, Dijkstra côté client)
- Alt+clic ou segments > 15km : 3 propositions diversifiées

### Sélecteur de sport

5 profils avec icônes : Route, Gravel, VTT, Off-road, Running. Change le modèle de coût et recharge le graphe.

### Sidebar gauche

- Toggle : icône (collapsed 36px) / expanded (260px)
- Onglets : "Mes traces" | "Publiques"
- Tri par proximité au centre de la carte (haversine)
- Activité sélectionnée : détails, lien Strava, badge GPX, profil d'élévation
- Profil d'élévation SVG avec gradient de pente (vert → jaune → orange → rouge → violet)

### Couches carte

- **Heatmap communautaire** : lignes colorées par heat_score (rose → violet)
- **Pistes DFCI** : tirets rouge/blanc
- **Traces personnelles** : lignes colorées par sport
- **Overlay de surface** : segments colorés asphalte/gravel/terre/roche (si data_quality suffisante)

## Landing page (`/`)

- Carte MapLibre plein écran en arrière-plan (scroll désactivé)
- Sport pills pour filtrer la heatmap affichée
- Panneau auth en bas à droite (si non connecté)
- Feature cards : routage communautaire, heatmap, invitation club
- Section méthode + CTA contribution

## Composants partagés

| Composant | Usage |
|-----------|-------|
| `TopNav` | Navigation avec liens actifs (« Mes itinéraires » visible si connecté) |
| `RouteCard` | Carte de route partagée (discover + /me/routes, modes public/owner) |
| `Map` | Wrapper MapLibre (dynamic import, SSR disabled) |
| `PlaceSearch` | Recherche de lieu (Nominatim) avec variante dark/light |
| `ElevationProfile` | SVG interactif pente/altitude |
| `ProfileSelector` | Sélecteur de sport pour le routage |
| `HeatmapLayer` | Couche heatmap avec filtrage sport |

## Design tokens

- Couleur principale : `#1a4731` (vert foncé)
- Couleur secondaire : `#2d6a4f` (vert moyen)
- DFCI : rouge `#c0392b` + blanc
- Heatmap : gradient rose → violet (`#f4b8d8` → `#3d1a5c`)
