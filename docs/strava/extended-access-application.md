# Strava — demande d'accès (réponses de formulaire) + checklist screenshots

> **But de ce doc :** brouillon prêt-à-copier des réponses au formulaire Strava, +
> la liste exacte des captures d'écran à joindre. Rédigé pour être **vrai** sous
> le nouveau *API Agreement / API Policy* 2026 (voir la mémoire
> `reference_strava_api_program_changes_2026`).

---

## 🔴 À LIRE AVANT DE SOUMETTRE — 3 verrous

Ne PAS envoyer ce formulaire tant que ces 3 conditions ne sont pas VRAIES, sinon
on atteste une conformité qu'on n'a pas (motif de rejet **et** de révocation
d'accès API — Strava audite exactement ça) :

1. **Pipeline séparé, déployé en prod.** La heatmap communautaire + l'export ODbL
   ne lisent QUE les contributions `source='manual_upload'`. L'API Strava
   (webhook/resync) ne nourrit QUE la vue perso de l'utilisateur — jamais le
   commun. → pièces ②/③ (PR provenance-aware) mergées ET déployées.
2. **Heatmap prod rebuild depuis les uploads.** La heatmap actuelle a été
   construite depuis des données API (les ~2 676 activités de Paul via l'API).
   Il faut la **reconstruire depuis les archives uploadées** (Paul re-contribue
   sa propre archive Strava via le flux #451) pour que « uniquement des uploads
   manuels » soit **vrai** sur les captures. → pièce ⑤.
3. **Seuil des 10 athlètes atteint** (pour l'*Extended Access* uniquement). Le
   formulaire *Extended Access* n'est examiné que si l'app a **déjà atteint la
   limite de 10 athlètes en Standard Tier**. En dessous → refus automatique.
   → self-upgrade dashboard + quelques amis connectés d'abord.

**Distinction des deux démarches Strava (ne pas confondre) :**
| Démarche | Formulaire ? | Pré-requis | Ce que ça débloque |
|---|---|---|---|
| **Self-upgrade → 10 athlètes** (Standard) | ❌ non, juste le dashboard | abonnement Strava (code `bfc4126f6a`, 3 mois) avant le 30 juin | tes amis peuvent se connecter ; limites ×2 (200/15min · 2000/j) |
| **Extended Access** (>10 athlètes) | ✅ ce doc | avoir atteint 10 athlètes + conformité vraie | capacité au-delà de 10 ; pas d'abonnement requis |

Pour l'immédiat (quelques amis), **le self-upgrade suffit** — pas de formulaire.
Ce doc sert quand on vise l'*Extended Access*.

---

## Réponses au formulaire (brouillon)

Champs `⟨…⟩` = à compléter par Paul (données perso / à vérifier au moment du dépôt).

| Champ | Réponse |
|---|---|
| **First name** | ⟨Paul⟩ |
| **Last name** | ⟨Leclercq⟩ |
| **Email** | ⟨email du compte développeur Strava⟩ |
| **Application name** | Chemins Communs (Common Trails) |
| **Client ID** | 28707 |
| **Website / URL** | https://chemins-communs.fr |
| **Support / contact URL** | https://chemins-communs.fr ⟨+ email de contact si demandé⟩ |
| **Current number of connected athletes** | ⟨valeur du dashboard au moment du dépôt⟩ |
| **Requested athlete capacity** | ⟨ex. 100 — la capacité communautaire visée⟩ |

### Application description

> Chemins Communs (Common Trails) est un projet **open-source et à but non
> lucratif** de cartographie cyclable (route / gravel / VTT / tout-terrain).
>
> **Usage de l'API Strava = strictement personnel.** Nous utilisons l'API Strava
> uniquement pour afficher à chaque utilisateur **ses propres** activités
> récentes dans **sa propre** vue (webhook + resync). Les données obtenues via
> l'API Strava ne sont **jamais** agrégées, ni affichées à d'autres utilisateurs,
> ni exportées, ni intégrées à la heatmap communautaire — conformément aux §2.3,
> §5.4, §5.10 de l'API Policy.
>
> **La heatmap communautaire provient exclusivement de fichiers GPX que les
> utilisateurs déposent eux-mêmes.** Chaque utilisateur demande à Strava
> l'export de **ses propres données** (droit d'export personnel affirmé par
> Strava), puis dépose son archive chez nous avec un **consentement explicite**.
> Ces traces — la donnée personnelle de l'utilisateur, exportée par lui — sont
> anonymisées et contribuées volontairement à une heatmap ouverte sous licence
> **ODbL**. Ce n'est pas de la « Strava Data » extraite par notre application via
> l'API : c'est la donnée de l'utilisateur, exportée et partagée par son choix.
>
> Cette séparation est appliquée au niveau du code : la provenance de chaque
> contribution est marquée (`manual_upload` vs `strava_api`) et les artefacts
> communautaires (heatmap, tuiles, export ODbL) ne lisent que les contributions
> `manual_upload`.

### « How do you use Strava data? » (si champ distinct)

> API Strava : lecture des activités personnelles de l'utilisateur pour les lui
> afficher dans sa propre vue (usage personnel §2.3). Aucune rétention au-delà du
> nécessaire opérationnel, aucune agrégation, aucune divulgation à des tiers,
> aucun usage IA/ML. La heatmap publique n'utilise pas de données API : uniquement
> des GPX déposés manuellement par les utilisateurs (leur propre export).

### Cases de conformité (TOS / API Policy)

> Cochables **seulement une fois les 3 verrous ci-dessus VRAIS.** À ce moment,
> l'attestation est exacte : l'usage API est personnel-only, le commun vient des
> uploads. Ne rien cocher avant.

---

## 📸 Checklist screenshots — « all places Strava data is shown »

Le formulaire exige des captures de **tous les endroits où la donnée Strava
apparaît**. À capturer **une fois le pivot déployé** (verrous 1 & 2), en FR
cohérent (après le fix i18n), site prod :

- [ ] **Écran de connexion Strava** (bouton « Se connecter avec Strava » + scope OAuth demandé).
- [ ] **Vue perso de l'utilisateur** — là où ses propres activités API s'affichent (la SEULE surface qui montre de la donnée API). Montrer que c'est privé/personnel.
- [ ] **Écran d'import d'archive** (#451) — le dépôt GPX + le **texte de consentement** + le « how-to » d'export + les liens. C'est la preuve que le commun vient d'uploads consentis.
- [ ] **Heatmap communautaire** — en indiquant (légende/mention) qu'elle est construite depuis les uploads ODbL, pas depuis l'API.
- [ ] **Page méthode / licence** — la mention ODbL + « traces anonymisées ».
- [ ] **Déconnexion / suppression** — l'utilisateur peut se déconnecter (et idéalement révoquer côté Strava).

> ⚠️ Ne PAS capturer l'état actuel : le mélange FR/EN (fix en cours) et la heatmap
> encore alimentée par l'API donneraient des preuves **fausses**.

---

## Références

- API Policy : https://www.strava.com/legal/api_policy
- API Agreement : https://www.strava.com/legal/api
- API FAQ : https://developers.strava.com/docs/getting-started/
- Export perso (how-to utilisateur) : https://support.strava.com/hc/en-us/articles/216918437-Exporting-your-Data-and-Bulk-Export
- Mémoire projet : `reference_strava_api_program_changes_2026` (clauses + deadlines + archi décidée)
