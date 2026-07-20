# DEV.md — environnements & règles de développement

*(interne — relire avant tout passage du repo en public)*

## Règle n°1 — l'environnement de dev, c'est la VM

**Tous les processus (base, ingestion, API, proxy) tournent sur la VM OVH,
jamais sur le poste macOS local.** Le poste local sert uniquement à éditer les
fichiers (VS Code) et à les synchroniser vers la VM :

```sh
rsync -az --delete --exclude '.git/' --exclude 'business/' --exclude 'data/' \
  --exclude '__pycache__/' --exclude '.DS_Store' --exclude '.venv/' \
  ~/project/confinia/ <vm>:projects/confinia/    # alias ssh : business/INFRA.md
```

Raisons : bande passante datacenter (téléchargements IGN en secondes),
x86_64 (les images PostGIS officielles n'existent pas en arm64), et le même
environnement qu'en production.

## Règles

1. **Jamais de `python` direct sur l'hôte** (VM comme poste local). Tout
   s'exécute en conteneur — sur la VM via **podman + podman-compose**.
2. **Les gros téléchargements de données se font directement depuis la VM**
   (`wget` depuis data.geopf.fr, insee.fr…), jamais via le poste local puis
   rsync — la VM est en datacenter, le poste sur une connexion résidentielle.
3. Sur la VM, le service `ingest` est derrière un profil compose :
   `podman-compose --profile tools run --rm ingest …` — ou les cibles du
   `Makefile` avec `COMPOSE="podman-compose --profile tools"`.
   Images en noms qualifiés (`docker.io/…`) : podman n'a pas d'alias courts.
   Attention : **podman-compose n'interpole pas `${VAR:-défaut}`** dans les
   valeurs d'environnement — les identifiants passent par `env_file:
   deploy/secrets.env` (gitignoré). Et **`build --no-cache` obligatoire pour
   l'api** : le cache de couches podman rate les COPY modifiés.
4. **Déploiement :** la **démo web sera servie en GitHub Pages** (statique —
   elle appelle l'API publique) ; **la VM sert l'API et le reverse proxy** :
   - `db` — PostGIS, port 5432 en localhost uniquement ;
   - `api` + `api-b` — FastAPI/uvicorn en **bleu/vert** (8000 et 8001,
     localhost uniquement) ; caddy équilibre avec health checks actifs
     (`/healthz`) ET passifs (`fail_duration`) ;
   - `caddy` — ports 80/443 publics, HTTPS automatique Let's Encrypt ;
     config montée en répertoire (`deploy/caddy/`), vhosts tiers dans
     `deploy/sites/`.

   **Mises à jour SANS coupure (vérifié sonde à 300 ms : 0 échec) :**
   - **API** : `./deploy/deploy-api.sh [stage|promote|rollback|full]`.
     `stage` = build + le VERT seul reçoit la nouvelle version, testable
     par un humain sur **https://staging.api.confinia.io** pendant que le
     public reste sur le BLEU ; `promote` = bascule du public ;
     `rollback` = retour à l'image `:previous` ; `full` (défaut) = roulant
     direct. `SKIP_BUILD=1` pour re-bascule sans rebuild.
     Le script n'utilise PAS podman-compose pour les bascules : compose
     suit `depends_on` et peut supprimer les deux instances pour recréer
     la db dès que le hash de `secrets.env` change. podman pur.
   - **Edge caddy** : `./deploy/deploy-edge.sh` (validation dans un
     conteneur ÉPHÉMÈRE — jamais dans le conteneur en marche, qui voit
     d'anciens inodes après rsync — puis `caddy reload` gracieux).
   - Seule opération à coupure restante : recréer le conteneur caddy
     lui-même (changement de montages ou de commande), quelques secondes, rare.
5. (Historique macOS : Apple `container` + socktainer restent utilisables pour
   un one-shot local — règles d'origine : BuildKit désactivé, `container run`
   pour les commandes ponctuelles — mais ce n'est plus la voie documentée.)

## Environnements

### VM OVH (dev + déploiement)

- **Debian 13, 8 CPU, 32 GB RAM, 1.8 TB** — VM dédiée OVH (compte personnel).
  **IP, hostname et alias ssh : voir `business/INFRA.md` (privé, jamais commité).**
- Runtime : podman 5.4 + podman-compose (linger activé — les conteneurs
  survivent à la déconnexion ; `restart: unless-stopped` sur api/caddy).
- Projet : **`~/projects/confinia/`** (miroir rsync du poste local).
- **DNS : wildcard `*.confinia.io` + apex → la VM** (zone OVH) ; l'API est
  exposée sur `https://api.confinia.io` via caddy.
- Legacy : l'ancienne stack monitoring (influxdb/telegraf/grafana/caddy,
  conteneurs `docker-compose_*`) est **arrêtée** depuis 2026-07-18 —
  conteneurs conservés, à supprimer quand on est sûr.

### Poste local (macOS) — édition uniquement

- VS Code, git, rsync. Aucun service, aucune donnée de prod.
- Le repo git est ici (`~/project/confinia`) ; la VM reçoit une copie rsync
  (sans `.git/`, sans `business/`).

## Arborescence

Poste local `~/project/confinia/` (sessions VS Code ouvertes ici) :

- code du repo `confinia/confinia-core` à la racine — dont `TODO.md`
  (build track, interne : à relire avant passage public) ;
- **`business/` — documents business privés** (PLAN, STORY, TODO business,
  modèle financier, interviews) : **gitignoré, ne doit jamais être commité** —
  le repo passera public à la beta ;
- `data/` — données locales (gitignoré aussi).

VM `~/projects/confinia/` : même arborescence, sans `business/` ni `.git/`.

## Données

`./data/` (gitignoré, monté `/data` dans les conteneurs) :

```
data/raw/insee/     commune_YYYY.csv, mvtcommune_YYYY.csv (COG INSEE)
data/raw/aeYYYY/    éditions Admin Express (7z + extract/, ou .parquet)
data/out/           GeoJSON produits (fixtures de test dept 01, démo)
```

Sources : INSEE COG (insee.fr), IGN Admin Express COG édition via
`data.geopf.fr/telechargement/resource/ADMIN-EXPRESS-COG` (SHP ≤ 2024,
GeoParquet/GPKG/FlatGeobuf ≥ 2025). Attribution : « IGN — Admin Express »,
Licence Ouverte 2.0. URLs directes utilisées :

```
…/ADMIN-EXPRESS-COG_1-1__SHP__FRA_2018-04-03/ADMIN-EXPRESS-COG_1-1__SHP__FRA_2018-04-03.7z
…/ADMIN-EXPRESS-COG_2-0__SHP__FRA_2019-09-24/ADMIN-EXPRESS-COG_2-0__SHP__FRA_2019-09-24.7z
```

(2018 : n'extraire que `*_SHP_LAMB93_FR/*` — l'archive contient aussi 5 DROM
dans des projections locales, et le glob `**/COMMUNE.shp` prend le premier
match.)
