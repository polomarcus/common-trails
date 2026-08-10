# Vie privée et données

## Principes

- Les activités personnelles sont **privées par défaut**
- La contribution à la heatmap communautaire est **opt-in** (case à cocher à l'import)
- Les données communautaires sont **anonymisées** par K-anonymité
- Aucune trace individuelle ne peut être reconstituée à partir des données publiques

## Modèle de données : privé vs commun

```
┌─────────────────────────────────────────────────────────┐
│  DONNÉES PRIVÉES (jamais publiques)                     │
│  ─────────────────────────────────────────────────────  │
│  activities        : vos activités importées            │
│  activity_cells    : vos cellules de couverture         │
│  activity_edges    : vos segments GPS privés            │
│  integration_accounts : vos tokens OAuth                │
└─────────────────────────┬───────────────────────────────┘
                          │
                   opt-in │ contribution
               K-anonymité (K≥2 prod, K=1 dev)
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  DONNÉES COMMUNES (ODbL 1.0)                            │
│  ─────────────────────────────────────────────────────  │
│  heat_cells        : heatmap communautaire (tuiles Z14) │
│  heat_edges        : segments populaires (graphe)       │
│  routes            : itinéraires partagés               │
└─────────────────────────────────────────────────────────┘
```

## K-anonymité

Un segment n'apparaît dans la heatmap publique que si **K utilisateurs distincts** y ont contribué.

| Environnement | Valeur K | Raison |
|---------------|----------|--------|
| Production | K=2 | Un seul utilisateur ne peut pas être identifié |
| Développement | K=1 | Visible dès le premier import pour les tests |

Le routage interne utilise K=1 pour préserver la connectivité du graphe, mais les données exportées respectent toujours K=2.

## Intégrations OAuth

### Strava

- Import via l'API officielle OAuth2 uniquement
- Seules vos activités personnelles sont importées (traces GPS)
- La heatmap globale Strava n'est **jamais** importée (propriétaire)
- En `TEST_MODE=true`, tous les appels sont des stubs

### Garmin

- Export manuel ZIP depuis Garmin Connect → import GPX
- Aucune API tierce, aucun token stocké
- Vos données transitent uniquement par votre navigateur

## Ce que le routage expose

Les résultats de routage ne révèlent pas de données personnelles :

- Le graphe de routage est construit à partir de données agrégées (heat_edges avec K-anonymité)
- Les traces personnelles ne sont utilisées que pour l'utilisateur connecté lui-même
- Aucune trace individuelle ne peut être reconstituée depuis les heat_edges

## Licences

- **Code source** : MIT
- **Données communautaires** : ODbL 1.0 (Open Database Licence)
