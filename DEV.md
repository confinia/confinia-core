# DEV.md — environnements & règles de développement

*(interne — relire avant tout passage du repo en public)*

## Règles

1. **Jamais de `python` direct sur l'hôte.** Tout s'exécute en conteneur :
   sur macOS via **Apple `container` + Socktainer** (socket compatible Docker),
   sur la VM via **podman**. Le venv local `.venv/` n'existe que pour
   l'outillage ponctuel d'exploration — pas pour les commandes documentées.
2. **Préférer les commandes `docker compose`** (fichier `docker-compose.yml` à la
   racine) : les mêmes commandes doivent fonctionner sur macOS (socktainer) et
   sur la VM Debian (podman + podman-docker / podman-compose). Les cibles du
   `Makefile` encapsulent les invocations courantes.
3. Pas de BuildKit/buildx via compose — règle, à mettre dans le shell avant
   toute commande compose (le Makefile les exporte déjà) :
   ```sh
   export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 COMPOSE_BAKE=false
   ```
   Si socktainer ne passe toujours pas, fallback :
   `container build` / `container run` en direct — la forme compose reste la
   référence documentée. (Si le build échoue avec une erreur XPC :
   `container builder stop && container builder start`.)
4. **Sur macOS, les commandes ponctuelles (`run --rm`) passent par
   `container run` directement** (règle) : `docker compose run` via socktainer
   échoue à résoudre l'image par son sha. Exemple :
   `container run --rm -v ./data:/data confinia-ingest:latest /app/ingest_cog.py …`
   Le `docker compose` complet (services + run) reste la voie normale sur la VM.
5. **Déploiement :** la **démo web est servie en GitHub Pages** (github.io,
   statique — elle appelle l'API publique) ; **seule l'API est déployée sur la
   VM Confinia**. Rien d'autre ne tourne sur la VM (pas de démo, pas de site).

## Environnements

### Poste de dev (macOS)

- Apple `container` ≥ 1.1.0, socket Socktainer :
  `DOCKER_HOST=unix:///opt/homebrew/var/run/socktainer/.socktainer/container.sock`
- `docker` + `docker-compose` CLI Homebrew branchés sur ce socket.

### VM OVH (déploiement)

- **Debian, 8 CPU, 32 GB RAM** — VM dédiée OVH (compte personnel), IP `<vm-ip>`.
- Accès : `ssh <vm-ssh>` (alias actuel, fonctionne).
- **Renommage prévu :** l'alias ssh devient `<vm-ssh>` ;
  nom OS/domaine : `debian@<vm-host>`.
- Runtime cible : podman (+ `podman-docker` pour la compat `docker compose`).
- **DNS : `confinia.io` pointera sur cette VM** (configuration en cours côté
  registrar OVH) ; l'API y sera exposée (HTTPS à prévoir, cf. TODO Step 6).
- Déploiement : `git clone` + `make db-up` + `make ingest` — mêmes commandes
  qu'en local, c'est le but.

## Arborescence locale

Tout le projet vit sous `~/project/confinia/` (sessions VS Code / Claude Code
ouvertes ici) :

- code du repo `confinia/confinia-core` à la racine ;
- **`business/` — documents business privés** (PLAN, STORY, TODO, modèle
  financier, interviews) : **gitignoré, ne doit jamais être commité** — le
  repo passera public à la beta ;
- `data/` — données locales (gitignoré aussi).

## Données locales

`./data/` (gitignoré, monté `/data` dans les conteneurs) :

```
data/raw/insee/     commune_YYYY.csv, mvtcommune_YYYY.csv (COG INSEE)
data/raw/aeYYYY/    éditions Admin Express (7z + extract/, ou .parquet)
data/out/           GeoJSON produits
```

Sources : INSEE COG (insee.fr), IGN Admin Express COG édition via
`data.geopf.fr/telechargement/resource/ADMIN-EXPRESS-COG` (SHP ≤ 2024,
GeoParquet/GPKG/FlatGeobuf ≥ 2025). Attribution : « IGN — Admin Express »,
Licence Ouverte 2.0.
