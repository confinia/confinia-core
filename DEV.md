# DEV.md — development environments & rules

*(internal — re-read before any switch of the repo to public)*

## Rule #1 — the dev environment is the VM

**All processes (database, ingestion, API, proxy) run on the OVH VM,
never on the local macOS machine.** The local machine is only used to edit
files (VS Code) and sync them to the VM:

```sh
rsync -az --delete --exclude '.git/' --exclude 'business/' --exclude 'data/' \
  --exclude '__pycache__/' --exclude '.DS_Store' --exclude '.venv/' \
  ~/project/confinia/ <vm>:projects/confinia/    # ssh alias: business/INFRA.md
```

Reasons: datacenter bandwidth (IGN downloads in seconds), x86_64 (the
official PostGIS images do not exist for arm64), and the same environment
as production.

## Rules

1. **Never run `python` directly on the host** (VM or local machine).
   Everything runs in a container — on the VM via **podman + podman-compose**.
2. **Large data downloads are done directly from the VM**
   (`wget` from data.geopf.fr, insee.fr…), never via the local machine then
   rsync — the VM is in a datacenter, the local machine on a residential
   connection.
3. On the VM, the `ingest` service sits behind a compose profile:
   `podman-compose --profile tools run --rm ingest …` — or the `Makefile`
   targets with `COMPOSE="podman-compose --profile tools"`.
   Fully qualified image names (`docker.io/…`): podman has no short aliases.
   Beware: **podman-compose does not interpolate `${VAR:-default}`** in
   environment values — credentials go through `env_file:
   deploy/secrets.env` (gitignored). And **`build --no-cache` is mandatory
   for the api**: podman's layer cache misses modified COPYs.
4. **Deployment:** the **web demo will be served on GitHub Pages** (static —
   it calls the public API); **the VM serves the API and the reverse proxy**:
   - `db` — PostGIS, port 5432 on localhost only;
   - `api` + `api-b` — FastAPI/uvicorn in **blue/green** (8000 and 8001,
     localhost only); caddy load-balances with active health checks
     (`/healthz`) AND passive ones (`fail_duration`);
   - `caddy` — public ports 80/443, automatic Let's Encrypt HTTPS;
     config mounted as a directory (`deploy/caddy/`), third-party vhosts in
     `deploy/sites/`.

   **ZERO-downtime updates (verified with a 300 ms probe: 0 failures):**
   - **API**: `./deploy/deploy-api.sh [stage|promote|rollback|full]`.
     `stage` = build + only GREEN receives the new version, testable
     by a human on **https://staging.api.confinia.io** while the
     public stays on BLUE; `promote` = switches the public over;
     `rollback` = back to the `:previous` image; `full` (default) = direct
     rolling update. `SKIP_BUILD=1` to switch back without rebuilding.
     The script does NOT use podman-compose for switchovers: compose
     follows `depends_on` and can delete both instances to recreate
     the db as soon as the hash of `secrets.env` changes. Pure podman.
   - **APPLICATION caddy edge**: `./deploy/deploy-edge.sh` (validation in
     an EPHEMERAL container — never in the running container, which sees
     old inodes after an rsync — then graceful `caddy reload`).
     Since 2026-07-20, the edge is LAYERED: the upstream caddy
     (80/443 + certificates + grafana.confinia.io = VM) lives in
     `~/project(s)/platform/` (dedicated compose, local git); this repo's
     caddy is the APPLICATION one (HTTP 127.0.0.1:8085) and serves site,
     api (historical hostname + www.confinia.io/api), staging and the
     application grafana (www.confinia.io/grafana). Other apps' vhosts go
     into `platform/sites/` (reload: `platform/deploy-edge.sh`).
   - The only remaining operation with downtime: recreating the caddy
     container itself (change of mounts or command), a few seconds, rare.
5. (macOS history: Apple `container` + socktainer remain usable for a
   local one-shot — original rules: BuildKit disabled, `container run`
   for one-off commands — but this is no longer the documented path.)
6. **VM MULTI-CADDY RULE (founder, 2026-07-20): every host-network caddy
   must declare a UNIQUE admin address** (`admin localhost:2085`
   for confinia; convention: 2000 + application port modulo 10000).
   By default, they all share `localhost:2019`: a `caddy reload` launched
   in one container can then load its config INTO THE PROCESS OF ANOTHER
   caddy (cause of the general outage of 2026-07-20: the upstream ended up
   serving the application config on 8085 and nothing on 443 anymore).
   To be replicated in platform, overwatch and ecobuilding (their sessions).
7. **Every frontend change (demo, site) is verified in MOBILE rendering
   (~440 px) AND desktop before publishing** — playwright capture from the VM
   (`mcr.microsoft.com/playwright/python` container, demo served on :8080).
   Founder rule of 2026-07-20: the mobile rendering must be impeccable.

## Environments

### OVH VM (dev + deployment)

- **Debian 13, 8 CPU, 32 GB RAM, 1.8 TB** — dedicated OVH VM (personal account).
  **IP, hostname and ssh alias: see `business/INFRA.md` (private, never committed).**
- Runtime: podman 5.4 + podman-compose (linger enabled — containers
  survive disconnection; `restart: unless-stopped` on api/caddy).
- Project: **`~/projects/confinia/`** (rsync mirror of the local machine).
- **DNS: wildcard `*.confinia.io` + apex → the VM** (OVH zone); the API is
  exposed on `https://api.confinia.io` via caddy.
- Legacy: the old monitoring stack (influxdb/telegraf/grafana/caddy,
  `docker-compose_*` containers) has been **stopped** since 2026-07-18 —
  containers kept, to be deleted when we are sure.

### Local machine (macOS) — editing only

- VS Code, git, rsync. No services, no production data.
- The git repo is here (`~/project/confinia`); the VM receives an rsync copy
  (without `.git/`, without `business/`).

## Directory layout

Local machine `~/project/confinia/` (VS Code sessions open here):

- code of the `confinia/confinia-core` repo at the root — including `TODO.md`
  (build track, internal: re-read before going public);
- **`business/` — private business documents** (PLAN, STORY, business TODO,
  financial model, interviews): **gitignored, must never be committed** —
  the repo will go public at the beta;
- `data/` — local data (gitignored too).

VM `~/projects/confinia/`: same layout, without `business/` or `.git/`.

## Data

`./data/` (gitignored, mounted as `/data` in the containers):

```
data/raw/insee/     commune_YYYY.csv, mvtcommune_YYYY.csv (INSEE COG)
data/raw/aeYYYY/    Admin Express editions (7z + extract/, or .parquet)
data/out/           produced GeoJSON (dept 01 test fixtures, demo)
```

Sources: INSEE COG (insee.fr), IGN Admin Express COG edition via
`data.geopf.fr/telechargement/resource/ADMIN-EXPRESS-COG` (SHP ≤ 2024,
GeoParquet/GPKG/FlatGeobuf ≥ 2025). Attribution: "IGN — Admin Express",
Licence Ouverte 2.0. Direct URLs used:

```
…/ADMIN-EXPRESS-COG_1-1__SHP__FRA_2018-04-03/ADMIN-EXPRESS-COG_1-1__SHP__FRA_2018-04-03.7z
…/ADMIN-EXPRESS-COG_2-0__SHP__FRA_2019-09-24/ADMIN-EXPRESS-COG_2-0__SHP__FRA_2019-09-24.7z
```

(2018: extract only `*_SHP_LAMB93_FR/*` — the archive also contains 5
overseas territories (DROM) in local projections, and the `**/COMMUNE.shp`
glob takes the first match.)
