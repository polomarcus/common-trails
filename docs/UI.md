# Interface utilisateur

## Principes

L'interface est **map-first** : la carte est l'élément central, les panneaux sont des compléments.
Le frontend est un export statique Next.js (`output: 'export'`, pas de SSR) ; tout ce qui
dépend de la session (jeton, `is_admin`) est lu **après le montage**, jamais au rendu
(sinon erreur d'hydratation React #418 — voir `frontend/components/map/MapToolbar.tsx`).

Le routage in-app (routeur WASM, édition par drag, propositions d'itinéraires) a été
**retiré** ; le design historique est dans [`archive/`](archive/).

## Pages

| Page | Route | Description |
|------|-------|-------------|
| Accueil | `/` | Carte hero (heatmap PMTiles statique), connexion par lien magique, dépôt de traces (`ContributionDropzone`) |
| Carte | `/map` | Carte publique : heatmap commune, « Ma heatmap » (si connecté), recherche de lieu, fonds de carte, légende, export ODbL |
| Calque | `/calque` | Tutoriel : ajouter la heatmap comme calque dans gpx.studio / autres outils |
| Méthode | `/methode` | Explication de la méthode et des licences |
| Stats | `/stats` | Tableau de bord personnel (cartes de synthèse, records, répartition par sport) |
| Strava | `/strava` | Retour OAuth Strava |
| Vie privée | `/privacy` | Politique de confidentialité |
| Support | `/support` | Contact / soutien |
| Admin | `/admin` | Tableau de bord admin (utilisateurs, fraîcheur de la heatmap) — 403 sans `is_admin` |
| Connexion | `/auth/verify`, `/auth/confirm-email` | Validation du lien magique / confirmation d'email |

Redirections (stubs client, fonctionnalités gelées) : `/discover`, `/collections`,
`/activities` → `/map` ; `/me/stats` → `/stats`. `/routes` et `/me/routes` relèvent du
modèle social/routage gelé.

## Carte (`/map`)

La carte **n'exige pas de connexion**. Seul l'export ODbL en masse
(`ExportHeatmapModal`) affiche un appel à se connecter pour un visiteur anonyme.

### Couches

- **Heatmap commune** — source PMTiles `community-trails`, couches définies dans
  `frontend/lib/community-heatmap-layers.ts` : densité (`community-trails-heat`, type
  `heatmap` sur `heat_points`), lignes (`community-trails-glow` / `community-trails-line`
  sur `trails`) et flèches de sens (`community-trails-arrows`). Rampe prune → rose →
  orange (`#7a2058` … `#ff8c42`).
- **Ma heatmap** — les traces de l'utilisateur, servies en direct par
  `GET /me/activities` (`backend/app/api/me_activities.py`), une seule couleur cyan
  `MY_TRACES_COLOR = '#06b6d4'` (`frontend/lib/constants.ts`). Largeur, flou et opacité
  suivent des rampes de zoom (`frontend/lib/user-traces-layers.ts`) : traits fins à
  faible zoom. Pour l'affichage seulement, une trace est coupée en `MultiLineString`
  là où deux points consécutifs sont à plus de 2 500 m (pas de traits droits à travers
  la mer) ; la géométrie stockée n'est jamais modifiée.
- Les deux cases « Ma heatmap » / « Heatmap commune » sont indépendantes et
  superposables ; « Ma heatmap » n'apparaît qu'une fois connecté (après montage).
- Autres couches optionnelles : pistes DFCI, photos, fonds de carte.

### Légende

`frontend/components/map/MapLegend.tsx` : « Popularité » pour la heatmap commune,
« Ma heatmap » pour les traces personnelles.

## Import et connexion

- **Connexion par lien magique** (email). Limites : 3 demandes par email et 20 par IP
  par fenêtre de 15 min (429 au-delà). Le paramètre `?redirect=` est respecté après
  connexion s'il pointe vers un chemin interne sûr (`frontend/lib/safe-redirect.ts`).
- **Dépôt unique** (`ContributionDropzone`) : GPX/FIT isolés (sport à choisir,
  obligatoire) ou archive Strava/Garmin `.zip` (pré-contrôle de taille 2 Go,
  `MAX_ARCHIVE_BYTES` dans `frontend/lib/strava-archive-upload.ts`). Un avertissement
  `beforeunload` protège un envoi en cours. Carte d'état vide quand l'utilisateur n'a
  encore aucune trace.
- Le lien « Admin » n'est affiché qu'aux comptes `is_admin` (lu sur `/auth/me`).
- Bandeau bêta refermable en pied de page (`BetaBanner`, variable CSS `--beta-banner-h`).

## Design tokens

- Couleur principale : `#1a4731` (vert foncé)
- Couleur secondaire : `#2d6a4f` (vert moyen)
- DFCI : rouge `#c0392b` + blanc
- Heatmap commune : rampe prune → rose → orange (`#7a2058` → `#ff8c42`)
- Ma heatmap : cyan `#06b6d4`
