# Import ref:FR:DFCI — Hérault (34)

## Source

- **Dataset** : [Pistes DFCI Hérault](https://www.herault-data.fr/explore/dataset/pistes-dfci-herault/)
- **Éditeur** : Département de l'Hérault
- **Licence** : [Licence Ouverte v2.0 (Etalab)](https://www.etalab.gouv.fr/licence-ouverte-open-licence/)
- **Format** : GeoJSON via API OpenDataSoft
- **Nombre de pistes** : ~4 238

## Compatibilité licence

La Licence Ouverte v2.0 (Etalab) est explicitement compatible avec ODbL 1.0.
Voir : https://wiki.openstreetmap.org/wiki/Import/Catalogue#France

## Tags ajoutés

| Tag | Valeur | Exemple |
|-----|--------|---------|
| `ref:FR:DFCI` | Référence DFCI de la piste | `D34-A1-T2` |
| `source:ref:FR:DFCI` | Source de la donnée | `data.herault.fr` |

Aucun autre tag n'est modifié. Le tag `ref:FR:DFCI` n'est ajouté que si absent.

## Méthode

### Matching géographique automatique

1. Pour chaque piste DFCI du dataset Hérault :
   - Requête Overpass `around:50m` sur les points de la géométrie → ways candidats
   - Calcul de la **distance de Hausdorff** entre la géométrie DFCI et chaque way OSM (projection Lambert-93)
   - Calcul du **ratio de recouvrement** : proportion de la géométrie DFCI couverte par un buffer de 30m autour du way OSM

2. Critères d'acceptation :
   - Distance de Hausdorff < **30 mètres**
   - Ratio de recouvrement > **70%**

3. Si un way OSM a déjà un tag `ref:FR:DFCI` → **skip** (pas d'écrasement)

4. Si aucun way ne correspond → enregistré comme **no_match** dans le rapport

### Vérification manuelle

- Le script génère un rapport CSV avec toutes les correspondances
- Colonnes : `dfci_ref`, `status`, `osm_way_id`, `hausdorff_m`, `overlap_pct`, `existing_tag`
- Les cas `ambiguous` et `no_match` sont à vérifier manuellement dans JOSM/iD

## Exécution

```bash
# 1. Dry-run : génère le rapport sans toucher OSM
python scripts/osm_dfci_import.py --dry-run --bbox 3.3,43.3,4.2,43.9 --output rapport_dfci.csv

# 2. Vérification manuelle du rapport

# 3. Application (après review communauté)
python scripts/osm_dfci_import.py --apply --bbox 3.3,43.3,4.2,43.9 \
    --osm-user MON_USER --osm-password MON_PASS
```

## Changesets

- Taille : ~50 ways par changeset
- Commentaire : `Ajout ref:FR:DFCI depuis données ouvertes Hérault (data.herault.fr)`
- Source : `data.herault.fr (Licence Ouverte v2.0)`
- Tag `import=yes`

## Statistiques

*(À remplir après exécution du dry-run)*

| Statut | Nombre |
|--------|--------|
| Matched | — |
| Already tagged | — |
| No match | — |
| Ambiguous | — |
| **Total** | — |

## Période de review

- **Annonce** : forum.openstreetmap.fr (section Imports)
- **Durée minimale** : 14 jours
- **Contact** : [à compléter]

## Références

- [Wiki OSM : Import/Guidelines](https://wiki.openstreetmap.org/wiki/Import/Guidelines)
- [Wiki OSM : Automated Edits code of conduct](https://wiki.openstreetmap.org/wiki/Automated_Edits_code_of_conduct)
- [Wiki OSM : Key:ref:FR:DFCI](https://wiki.openstreetmap.org/wiki/Key:ref:FR:DFCI)
- [Dataset Hérault Data](https://www.herault-data.fr/explore/dataset/pistes-dfci-herault/)
