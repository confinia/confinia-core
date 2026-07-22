# TEST_POLAR — le parcours d'abonnement à un compte Pro

Ce document décrit COMMENT le parcours « un utilisateur s'abonne au palier
Pro » est validé, et porte les DERNIERS résultats de la validation
automatisée. Polar (le Merchant of Record) est simulé par des **webhooks
signés avec les mêmes mathématiques que la production** (Standard Webhooks :
HMAC-SHA256 de `id.timestamp.corps`, secret base64) ; l'application, la
vérification de signature et le provisioning testés sont EXACTEMENT le code
qui tourne en production. Secret et identifiants produits sont des valeurs
de test dédiées au CI.

## Le parcours couvert

1. **Sécurité d'abord** : un webhook non signé est refusé (401), une
   signature altérée est refusée (401), un produit inconnu est ignoré sans
   effet de bord.
2. **Achat** : webhook `subscription.active` (produit Pro) pour un email →
   la clé EXISTANTE de cet email passe en palier `pro`.
3. **Ordre indifférent** : une clé créée APRÈS l'achat naît directement
   en `pro` (l'acheteur peut payer d'abord et créer sa clé ensuite).
4. **Effet du palier** : avec une clé pro, `/v1/changes` répond
   `remaining: unlimited` (plus de quota).
5. **Hiérarchie** : un abonnement Enterprise actif prime sur le Pro ; sa
   résiliation seule redescend au Pro encore actif.
6. **Résiliation** : webhook `subscription.revoked` du Pro → l'email
   retombe en `free`, ses clés redeviennent des appelants sous quota.

Non couvert ici (dépend de l'infrastructure réelle, vérifié à la main lors
du câblage du 2026-07-21) : la livraison des webhooks par Polar jusqu'à
`https://api.confinia.io/polar/webhook` (endpoint configuré chez Polar,
secret partagé dans `deploy/secrets.env`) et le checkout hébergé
(`buy.polar.sh`, liens sur `/pricing`).

## Où et quand ça tourne

- **CI GitHub Actions** : workflow
  [`subscription-tests`](.github/workflows/subscription-tests.yml), étape
  « Polar pro journey ». Déclencheurs : chaque push sur `main`, chaque pull
  request, un passage hebdomadaire (lundi 05:17 UTC), et à la demande.
- **Fichiers** : [`tests/test_polar.py`](tests/test_polar.py), fixtures
  communes avec le parcours d'inscription.

## Derniers résultats

| Date (UTC) | Déclencheur | Résultat | Détail |
|---|---|---|---|
| 2026-07-22 10:16 | pull request #24 (premier passage) | ✅ **8/8 réussis** (0.06 s) | [run 29911277810](https://github.com/confinia/confinia-core/actions/runs/29911277810) : refus non signé/signature altérée, produit inconnu ignoré, achat → pro (clés existantes ET futures), premium illimité, enterprise > pro, résiliation → free |

Historique complet : onglet Actions du dépôt, workflow `subscription-tests`.
