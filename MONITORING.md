# MONITORING — ce qui est surveillé, et comment

Règle de fond (posture GDPR) : **aucune adresse IP n'est jamais stockée**.
Les dimensions sont bornées (pays, route, statut, événement) ; tout ce qui
identifierait une personne est réduit à des condensés salés irréversibles.

## Deux étages

| Étage | Outil | Ce qu'il regarde |
|---|---|---|
| **Plateforme** (autre session, repo `platform`) | grafana.confinia.io | la VM elle-même : CPU, RAM, disque, réseau, IO ; sondes blackbox des vhosts |
| **Application** (ce repo) | www.confinia.io/grafana | tout ce qui suit |

Chaîne applicative : l'API émet des métriques **OpenTelemetry** → collector
(port 4318) → **Prometheus** (rétention 180 jours, tendances longues) →
**Grafana applicatif** (provisionné par `deploy/grafana/provisioning/`,
mot de passe admin dans `deploy/secrets.env`, inscriptions fermées).

## Les données surveillées

### Trafic API — compteur `confinia.requests`
Une série par (route, méthode, statut, pays, client, keyed) :
- **route** : le gabarit FastAPI (`/v1/units`…), jamais l'URL brute ; les
  404 passent par un garde de cardinalité (`label_404`) qui regroupe les
  chemins inconnus ;
- **pays** : GeoIP (DB-IP Country Lite, CC BY 4.0) sur l'IP en transit,
  jamais persistée ;
- **client** : `demo` / `site` / `direct`, déduit d'Origin/Referer (borné) ;
- **keyed** : la requête portait-elle une clé API valide.
Panneaux : req/s par route, p50/p95 (`X-Response-Time-Ms`), répartition
des statuts, pays d'appel, top routes.

### Sécurité — la boucle 404 → filtres edge
Le panneau « 404 par chemin » (rangée Sécurité) liste ce que les scanners
sondent encore ; les motifs récurrents sont reversés à la main dans
`(block_scanners)` du Caddyfile (abort avant l'API). Les chemins déjà
filtrés n'apparaissent plus : le panneau ne montre que le reste à traiter.

### Frontend — compteur `confinia.frontend.events`
Événements UI de la démo via `/beacon` (liste blanche : load, play,
timetravel, commune_history, bascules de dept/région/pays, share, diff),
dimensionnés par événement et pays. Rangée « Frontend » du dashboard.

### Visiteurs uniques — table ops `visitor_daily`
Un visiteur = un condensé `sha256(secret + jour UTC + ip)` : irréversible,
incomparable d'un jour à l'autre. Table UNLOGGED, purge à 45 jours.
Panneau : visiteurs uniques par jour et par pays.

### Revenu et usage — tables ops (source des futurs panneaux business)
- `api_key` (+ `tier`) et `api_usage` : consommation par clé et par jour ;
- `premium_usage` : compteur à vie des rapports premium par appelant
  (le quota « 9 offerts puis 402 ») ;
- `upgrade_intent` : les intentions de paiement laissées sur /pricing ;
- `polar_subscription` : l'état des abonnements poussé par les webhooks.
Panneau à venir (issue #8/#19) : proxy MRR = abonnements actifs par palier.

### Santé des déploiements
- `/healthz` par couleur (version + nombre de versions en base) : c'est le
  contrat de la bascule bleu/vert (`deploy-api.sh` attend le healthz du
  passif avant de promouvoir) ;
- caddy applicatif : health checks actifs des upstreams couleur avec
  `fail_duration` (zéro requête vers un upstream mort pendant les bascules).

### CI (issue #18)
Le workflow `subscription-tests` rejoue chaque semaine et à chaque push les
deux parcours revenu (inscription, provisioning Polar) ; résultats dans
TEST_SUBSCRIPTION.md et TEST_POLAR.md.

## Ce qui n'est PAS collecté, à dessein
Adresses IP en base, identifiants individuels dans les métriques, URLs
brutes à cardinalité libre, cookies/trackers côté démo et site. La seule
donnée nominative du système est l'email fourni volontairement (clé API,
intention, abonnement, compte Keycloak) et vit dans la base ops.
