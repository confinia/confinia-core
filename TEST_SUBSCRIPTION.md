# TEST_SUBSCRIPTION — le parcours d'inscription d'un nouvel utilisateur

Ce document décrit COMMENT le parcours « un inconnu s'inscrit et consomme
l'API » est validé, et porte les DERNIERS résultats de la validation
automatisée. Le test est un vrai bout en bout : la véritable application
FastAPI face à un PostGIS éphémère chargé d'un jeu de communes de test
(deux communes qui fusionnent en 2019), sans aucun secret de production.

## Le parcours couvert

1. **Santé** : `GET /healthz` répond `ok` et voit les données.
2. **Inscription** : `POST /v1/keys {email}` crée une clé UUID **active**, en
   palier **free** (un email invalide est refusé en 422).
3. **Metering** : une requête `/v1/units` portant la clé est comptée dans
   `/v1/keys/{key}/usage` (c'était le bug corrigé en v0.3.0 : les clés
   étaient écrites dans la mauvaise base et jamais comptées).
4. **Modèle temporel** : l'historique d'une commune de test expose bien
   l'événement `merged_into` daté du 2019-01-01.
5. **Quota premium** : 9 rapports `/v1/changes` passent avec un compteur
   décroissant (8, 7, … 0) puis la **10e requête renvoie 402** avec le
   pointeur `/pricing` (modèle : 9 offerts, payant ensuite).
6. **Rapports** : `report.svg` commence par `<svg`, `report.pdf` par `%PDF`.

## Où et quand ça tourne

- **CI GitHub Actions** : workflow
  [`subscription-tests`](.github/workflows/subscription-tests.yml), étape
  « Signup journey ». Déclencheurs : chaque push sur `main`, chaque pull
  request, un passage hebdomadaire (lundi 05:17 UTC) pour détecter les
  dérives sans commit, et à la demande (`workflow_dispatch`).
- **Fichiers** : [`tests/test_subscription.py`](tests/test_subscription.py),
  jeu de données [`tests/fixture.sql`](tests/fixture.sql).
- **En local (VM, podman)** : lancer un PostGIS jetable, charger la fixture,
  démarrer l'API avec `PG_DSN`/`OPS_DSN` pointant dessus, puis
  `pytest tests/test_subscription.py` (mêmes variables que le workflow).

## Derniers résultats

| Date (UTC) | Déclencheur | Résultat | Détail |
|---|---|---|---|
| _en attente du premier passage CI_ | | | |

Historique complet : onglet Actions du dépôt, workflow `subscription-tests`.
