# Contribution communautaire — modèle UX (« pourquoi » vulgarisé + zéro douleur)

> Décision produit (Paul, 2026-07-12). Sous la nouvelle API Policy Strava,
> l'API ne peut PAS alimenter la carte commune (§5.4/§5.10). Modèle retenu :
> **deux flux assumés**, avec un « pourquoi » vulgarisé et un dépôt le plus
> indolore possible. Cette note est la SSOT que les PR d'implémentation suivent.
> Voir `reference_strava_api_program_changes_2026` (mémoire) pour le fond légal.

## Le modèle en une phrase

> **Ta connexion Strava = ta carte perso (auto, façon statshunter). Ton export déposé = ta contribution au commun (ouvert, ODbL).**

- **Couche perso** — via l'API Strava, auto-updatée, montrée seulement à toi.
  Conforme (§2.3). C'est le « fire and forget » là où il est permis.
- **Couche commune** — uniquement des GPX que l'utilisateur exporte lui-même et
  dépose. C'est sa donnée, son choix de partage. Le seul chemin conforme.

---

## 1. Vulgariser le POURQUOI (copie user-facing, non juridique)

But : que l'utilisateur comprenne en 5 secondes pourquoi on lui demande un
fichier plutôt que « juste sa connexion Strava », sans jargon, en cadrant ça
comme un **acte positif** (reprendre la main sur SES données), pas une corvée.

**Encart court (écran de dépôt) — DRAFT FR :**
> **Pourquoi déposer un fichier plutôt que connecter Strava ?**
> Strava nous autorise à te montrer **tes** sorties — mais pas à verser
> automatiquement les données de sa communauté dans une carte ouverte.
> Tes données, elles, t'appartiennent : tu peux les exporter quand tu veux.
> En déposant ton export **toi-même**, tu choisis de les partager — anonymisées —
> dans une carte libre que personne ne peut refermer. C'est tout l'esprit de
> Chemins Communs. 🚲

**Version ultra-courte (tooltip / sous-titre) :**
> Ta connexion Strava = ta carte perso. Ton export déposé = ta contribution au commun.

**Ton :** positif, empouvoirant, mission-driven (« reprendre la main sur des
données enfermées »), jamais « à cause des règles de Strava on doit… ». EN à
fournir en parité.

---

## 2. Rendre le dépôt INDOLORE

### a. Onboarding de l'export (guidé, pas « débrouille-toi »)
- Bouton clair → panneau pas-à-pas : (1) ouvrir la page export Strava
  [lien direct], (2) « Demander mon archive », (3) « Strava t'envoie un mail
  dans quelques heures », (4) revenir déposer le `.zip` ici. (déjà dans #451)
- Rassurer sur le délai : « pas besoin d'attendre devant l'écran, on te dira
  quoi faire quand le mail arrive ».

### b. Ingestion progressive + idempotence (déjà bâti)
- Dépôt du `.zip` → worker pacé (#451). L'utilisateur n'attend pas.
- **Ré-upload = seules les nouvelles sorties sont ajoutées** (dédup par
  `file_hash` + idempotence cross-source ③). On ne redemande jamais de trier.
- Retour clair après coup : « **X sorties ajoutées** · Y déjà connues (ignorées)
  · Z hors périmètre (autres sports) ».

### c. Le nudge malin (usage CONFORME de l'API comme signal, pas comme source)
- Si l'utilisateur est connecté à Strava (couche perso), on **sait** qu'il a de
  nouvelles sorties non encore contribuées. On lui affiche, **à lui seul** :
  > « Tu as **N nouvelles sorties** depuis ton dernier dépôt. Refais un export
  >   pour les ajouter au commun → [guide]. »
- ⚠️ Conforme : on n'ingère PAS ces sorties via l'API dans le commun ; on
  utilise juste le **compte**, montré au seul utilisateur (§2.3), pour réduire
  la friction. La donnée commune vient toujours de l'upload.
- Ça transforme le « on reste dans la merde pour maintenir la carte » en un
  rappel doux et actionnable — le plus proche du « fire and forget » qu'on
  puisse faire conformément.

### d. Futur « auto » du commun (hors Strava)
- Le vrai fire-and-forget communautaire viendra des sources aux conditions plus
  amicales : **upload direct appareil / Garmin / Wahoo / Komoot**. Le stub
  Garmin gelé du repo est le candidat. Noté comme direction, hors beta.

---

## Pièces d'implémentation (ordre)

1. **#451** (en cours de merge) — dépôt + consentement + how-to + worker pacé.
2. **②③** — séparation provenance (commun = `manual_upload` only ; API =
   `strava_api`, perso) + idempotence cross-source. Prérequis du nudge.
3. **Copie « pourquoi »** — petite PR front après #451 : encart vulgarisé (§1) +
   récap post-ingestion (§2b) sur l'écran de dépôt. Parité FR/EN.
4. **Nudge « N nouvelles sorties »** — après ②③ : requête « sorties perso non
   présentes dans le commun de l'utilisateur » (compte only, montré à lui) +
   bandeau d'incitation au ré-export.
5. **⑤** — rebuild heatmap prod depuis les uploads (ops).
