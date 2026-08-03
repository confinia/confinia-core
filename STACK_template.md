# STACK_template.md — how a product on the shared VM is deployed

> **Canonical reference for every SaaS on the Confinia VM** (`overwatch`,
> `ecobuilding`, `confinia`, and the ones that come next). Each product keeps its
> own `STACK_<product>.md` that copies **these exact section headers**, so any two
> can be diffed and merged side by side. Fill the per-product tables in §0; leave
> the shared invariants (§2, §7, §11) unchanged unless the architecture itself
> changes.
>
> It answers one question: **where does a request go, and who owns what.**
>
> Docs and code are **English everywhere** (product/UI copy may be FR).

---

## 0. Per-product facts — FILL THIS IN

The only section that differs between products. Everything below it should reduce
to these tables.

| Field | This product |
|---|---|
| Product / Unix user | `<product>` |
| Repo | `<org>/<repo>` |
| Port band | `<8Nxx>` (see §4 for the map) |
| Apex hostname | `<product>.confinia.io` |
| Project edge router port | `127.0.0.1:<router>` |
| Sandbox entry port | `127.0.0.1:<sandbox>` |
| Staging stack port | `127.0.0.1:<staging>` |
| Isolation unit | one rootless podman user (see §3) |

**Public hostnames → local port** (as registered in the platform edge, §4):

| Hostname | → local port | Environment |
|---|---|---|
| `<product>.confinia.io` | `127.0.0.1:<router>` | production (active colour) |
| `api.<product>.confinia.io` | `127.0.0.1:<router>` | production API |
| `staging.<product>.confinia.io` | `127.0.0.1:<staging>` | staging |
| `staging.api.<product>.confinia.io` | `127.0.0.1:<staging>` | staging API |
| `sandbox.<product>.confinia.io` | `127.0.0.1:<sandbox>` | sandbox |
| `sandbox.api.<product>.confinia.io` | `127.0.0.1:<sandbox>` | sandbox API |

---

## 1. TL;DR (shared invariants)

- **One VM, one Unix user per product.** Rootless podman: volumes, images and
  networks are per-user, so products cannot reach each other by accident.
- **One compose stack per environment**, each with its own port band, its own
  database and its own identity realm. Environments never share state (target;
  see §14 where staging still shares the prod DB today).
- **Blue/green only inside production**: two colours, one live, one warm for an
  instant rollback. Promotion is a routing swap, not a rebuild — no DB copy.
- **The edge decides nothing but routing**: hostname → `127.0.0.1:port`. It is
  shared by every product and changed only by the founder, via a PR to
  `confinia/platform` — never hand-edited on the VM.
- **GitHub drives everything**: issue → branch → draft PR → tests → sandbox →
  merge → staging → *human approval* → production. Done means **promoted and
  verified**, not merged.

---

## 2. Layers — who owns what

| Layer | Owns | Rule |
|---|---|---|
| **Platform edge** (caddy, `confinia/platform`) | `:443`, certificates, hostname → port | Founder-only. Products describe the change they need; they never apply it. Edit in the repo, never on the VM (§14). |
| **Project edge router** (one per product) | host-based blue/green routing, shared concerns (`/auth`, scanner blocking) | Lives in the product repo, user `<product>`. Reloaded by the product on promote. |
| **Per-stack caddy** (one per stack) | intra-stack paths: static frontend + `/api` → the stack's API | Lives in the stack; rebuilt with it. |
| **Compose stack** (one per environment) | db, api, web, grafana, ingest, its caddy | Own port band. Own database. Own realm. Stateless app tier; state in shared services. |
| **Unix user** | podman storage, volumes, `.env` | `ssh <product>` operates it. A sudo-capable account (`debian`) exists separately and does nothing product-related. |

---

## 3. VM users (rootless podman)

| User | Role |
|---|---|
| **`debian`** | Admin. Runs the **platform edge** (Tier 1) + all `sudo`/root ops. Owns `confinia/platform`. Use `ssh debian` **only** when root is required. |
| **`<product>`** | This SaaS. Runs **every** app stack: edge router, blue, green, staging, sandbox, auth, monitoring. All app ops target `ssh <product>`. |
| others | `overwatch`, `ecobuilding`, `confinia`, `mapmax`, `indoorequal`, `maplibre`, … — other tenants, same pattern, sharing Tier 1. |

Each user has its own `~/.local/share/containers` store and subuid/subgid range,
so tenants never collide. Moving a stack between users means **exporting volumes
and fixing ownership inside the user namespace** — a plain `cp` corrupts it, and
cluster-level DB objects (roles) are not in a database dump.

---

## 4. Caddy — three tiers

**Tier 1 — Platform edge** (`platform_caddy_1`, user `debian`, repo
**`confinia/platform`**). Terminates TLS (ACME), maps *public hostnames* →
*local ports*. **Shared by all tenants.** ⚠ Edit only via PR to
`confinia/platform`; a platform redeploy overwrites VM hand-edits (§14).

**Tier 2 — Project edge router** (`<product>-edge_caddy_1`, user `<product>`,
dir `caddy_server/`, `:<router>`). Host-based routing to the active/candidate
blue/green stack, plus shared concerns:
- apex `<product>.confinia.io` → **ACTIVE** colour · `staging.…` → **CANDIDATE**
- `/auth/*` → shared Keycloak realm
- `block_scanners` (403 on `/.env`, `/.git`, `/vendor/*`, `*.php`, directory
  listings, …) — **every product must enable this** (see §14 for why)
- State: `deploy/.active` = `blue|green`; installed config = `Caddyfile.<active>`

**Tier 3 — Per-stack caddy** (`<product>-{blue,green}_caddy_1`, sandbox/staging
caddies). Intra-stack routing: static frontend + `/api` → the stack's API.

**Port map** (fill per product; keep the single home in the edge Caddyfile band table):

| Port | Service |
|---|---|
| `<router>` | project edge router (prod entry) |
| `<blue>` / `<green>` | blue / green stack entry |
| `<staging>` | staging stack entry |
| `<sandbox>` | sandbox stack entry |
| `<auth>` | Keycloak (shared auth) |
| `<prometheus>` / `<grafana>` | monitoring |

Each product reserves a **band** (`8Nxx`), recorded in the edge Caddyfile so the
table has a single home. When a band gets tight, that is the signal to move to
Kubernetes, not to add bands by hand.

---

## 5. Compose projects

| Project (`-p`) | Dir | Role | Lifecycle |
|---|---|---|---|
| `<product>-blue` | `docker-compose.yml` + `deploy/blue.override.yml` | prod/staging app stack A | blue/green |
| `<product>-green` | `docker-compose.yml` + `deploy/green.override.yml` | prod/staging app stack B | blue/green |
| `<product>-edge` | `caddy_server/` | Tier-2 router | shared |
| `<product>-auth` | `auth_stack/` | Keycloak + postgres | **shared, stateful** |
| `<product>-staging` | `staging_stack/` | dedicated staging (own db/realm — target) | always-on |
| `<product>-sandbox` | `sandbox_stack/` | isolated PR env (own KC+db+realm) | always-on |
| `<product>-monitoring` | `monitoring_stack/` | prometheus/grafana/exporter | shared |

An **app stack** = `api` + `frontend` + `otel-collector` + `caddy`, all
**stateless**. State lives only in the shared services.

---

## 6. Environments ↔ code state

| | sandbox | staging | production |
|---|---|---|---|
| Trigger | open/updated **PR** on a branch | **merged to `main`** | **promoted** (human approval) |
| Purpose | try risky things | validate before users | serve users |
| URL | `sandbox.<product>.…` | `staging.<product>.…` | `<product>.confinia.io` |
| Refreshed | every PR commit | every merge | on approval |
| Database | own, throwaway | own (target; shares prod DB today, §14) | the real one |
| Identity realm | dedicated | dedicated | production |
| Billing | provider sandbox | off | real |
| Who looks at it | the agent | **you** | users |

Sandbox and staging both sit behind **basic auth**. Two consequences bite before
you write any code: the browser then **replays `Authorization: Basic` on every
request**, which shadows your own session cookie if you read that header naively;
and any third party you proxy to (Grafana) may read it as a failed login. **Strip
it at the proxy.**

---

## 7. Blue/green mechanics (production only)

- Blue and green are **identical, stateless** app stacks; only one is *active*
  (serves prod). The other is the *candidate* (serves staging).
- `deploy.sh` rebuilds + recreates the **candidate**, health-gates it on its
  local port, and **never touches the active stack**.
- `promote.sh` copies `Caddyfile.<candidate>` → `Caddyfile`, reloads the Tier-2
  router, writes `deploy/.active`. **Router flip only** — both colours point at
  the *same* shared prod DB, so nothing is copied and rollback is instant
  (`rollback.sh` flips back).
- **No DB duplication on switch.** A dedicated staging DB is a *separate* concern
  from blue/green, not a conflict.

---

## 8. GitHub flow (issues / PRs / Actions)

```
Issue  ──▶  branch + draft PR (Closes #N)  ──▶  merge to main  ──▶  promote
   │              │                                  │                 │
   │              ▼                                  ▼                 ▼
   └── track   SANDBOX deploy                  STAGING deploy      PRODUCTION
              (sandbox.*)                      (staging.*)        (<product>.*)
```

1. **issue → branch → draft PR** — work is visible while it is still wrong.
2. **push** — unit tests run on a hosted runner with **no secrets** (forks are
   safe); the sandbox rebuilds at that commit.
3. **merge** — the gate runs on the VM, the candidate colour is built, staging is
   rebuilt from the same commit.
4. **approval** — a human swaps the colour; the workflow re-checks the security
   invariants on the live site.
5. **PR prose** (issue/PR body, review, close comment) is approved by the
   operator before posting.

**Target CI**: GitHub **Actions** on a **self-hosted runner** on the VM (deploys
need the VM's rootless podman, unreachable from hosted runners). Until then,
deploys run via `deploy/*.sh` from a workstation (documented break-glass).

---

## 9. GitHub self-hosted runner (one per product)

Deploys need the VM's **rootless podman**, which is per-Unix-user and unreachable
from GitHub-hosted runners. So each product runs **its own self-hosted runner**,
registered to that product's repo and executing as the product's Unix user — a
runner can therefore only touch its own stacks, never another tenant's. This is
the same isolation boundary as §3.

| Product | Repo | Runs as user | Runner labels |
|---|---|---|---|
| `confinia` | `confinia/confinia` | `confinia` | `self-hosted, vm, confinia` |
| `overwatch` | `<org>/overwatch` | `overwatch` | `self-hosted, vm, overwatch` |
| `ecobuilding` | `<org>/ecobuilding` | `ecobuilding` | `self-hosted, vm, ecobuilding` |
| `<next>` | `<org>/<repo>` | `<product>` | `self-hosted, vm, <product>` |

**Install — once per product, run as the product Unix user:**

```sh
ssh <product>            # land as the product Unix user (never as debian)
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -Lo runner.tar.gz https://github.com/actions/runner/releases/download/v<X.Y.Z>/actions-runner-linux-x64-<X.Y.Z>.tar.gz
tar xzf runner.tar.gz
# registration token: repo → Settings → Actions → Runners → New self-hosted runner
./config.sh --url https://github.com/<org>/<repo> \
            --token <REG_TOKEN> \
            --name <product>-vm \
            --labels vm,<product> \
            --unattended --replace
```

**Run it as a lingering service** (survives logout, exactly like the product's
rootless podman stacks):

```sh
./svc.sh install                  # user-scoped systemd service
loginctl enable-linger <product>  # keep it up with no active login session
./svc.sh start
```

**Rules**
- **One runner ↔ one repo ↔ one Unix user.** Never share a runner across
  products; it would break the per-user isolation §3 depends on.
- **The runner holds deploy secrets** (registration token, access to
  `deploy/secrets.env`). Scope it to the single product repo — never an org-wide
  runner group.
- **Unit tests stay on GitHub-hosted runners with no secrets** (fork-safe, §8).
  Only the **deploy / promote** jobs target
  `runs-on: [self-hosted, vm, <product>]`.
- **The admin `debian` user runs no product runner** — it owns Tier 1 only; a
  product runner as `debian` would have root reach over every tenant.

---

## 10. Deploy scripts (`deploy/`, run as `<product>`)

| Script | Does |
|---|---|
| `deploy.sh` | rsync working tree → build + recreate the **candidate** stack → health-gate → reload router |
| `promote.sh` | flip the router: candidate → **active** (prod) |
| `rollback.sh` | flip back to the previous active stack (instant) |
| `staging.sh` | deploy `main` to the dedicated **staging** stack |
| `sandbox.sh` | deploy a branch to the isolated **sandbox** stack |
| `test.sh` | run the suite in a clean container (hermetic + opt-in e2e) — the promotion gate |

---

## 11. What isolates what (shared guarantees)

- **Between products**: separate Unix users, separate rootless podman storage.
- **Between environments**: separate databases, separate realms, separate bands.
- **Between tenants inside one environment**: **Postgres row-level security**. A
  customer's Grafana datasource authenticates as a role that *cannot* read
  another tenant's rows — enforced by the database, not the application, and
  therefore true even if the customer edits their own dashboard SQL.

That last one is the guarantee worth selling. It is also the one to **re-verify
after every deployment, automatically**.

---

## 12. Pros

- **Cheap and legible.** One VM, plain compose files, no orchestrator. One person
  can hold the whole thing in their head.
- **Instant rollback.** The previous colour is still running; promotion and
  rollback are the same one-second operation.
- **Real isolation where it counts.** Tenant separation is enforced by Postgres,
  which survives application bugs.
- **Reproducible environments.** Sandbox/staging are the same shape as prod, so a
  problem reproduces where it is safe to.
- **Fork-safe CI.** The test job holds no secrets; outside contributions run the
  full suite.
- **No vendor lock-in + automatic TLS.** Plain Caddy + compose; ACME at the edge.

---

## 13. Cons

- **One machine (SPOF).** No HA: a VM outage is an outage. Backups exist;
  failover does not.
- **Rootless podman is per-user.** Moving a stack between users needs volume
  export + ownership fixes inside the user namespace; cluster-level DB roles are
  not in a dump.
- **podman-compose ≠ docker-compose.** `up` collides on existing container names
  instead of recreating; `${VAR}` interpolation is unreliable — hence `env_file`
  everywhere; edge recreate needs a manual reload.
- **Manual edge + manual rsync deploys** until Actions lands — a founder
  bottleneck by design, and a workstation dependency.
- **Shared singletons in production.** One Postgres, one Grafana, one Keycloak
  per prod: a schema mistake has a wide blast radius inside that environment.
- **Secrets in plaintext files** (`deploy/secrets.env`) — no vault/rotation yet.

---

## 14. Known gotchas / current issues

- **The platform edge is owned by `confinia/platform`, not the product repo.**
  Hand-editing `~/projects/platform/caddy/Caddyfile` on the VM is reverted on the
  next platform redeploy. Register/rename hostnames **via a PR to
  `confinia/platform`**.
- **Enable `block_scanners` at Tier 2.** A raw dev server behind the edge will
  serve `.git`, `.env`, source and directory listings. The edge only proxies; the
  product is responsible for not exposing its working tree.
- **rsync ships the working tree**, not just committed files (gitignored local
  files travel too). Keep the tree clean before `deploy.sh`.
- **Staging shares the prod DB today** — not truly isolated until each env gets
  its own database.
- **`/tmp` is a RAM-backed tmpfs** — never stage large files there (OOMs the VM).
- The VM's **`curl` can truncate large response bodies to 0 bytes** — use
  Python/`httpx` to fetch PDFs/PNGs from the VM.

---

## 15. Maturity checklist (standards & security)

These are the **target standards every product on the VM converges toward** — not
a snapshot of any one product. Each `STACK_<product>.md` keeps this table and
marks its own status, so "how mature is this stack" is a diff, and a new product
starts with an honest all-`⬜` column. Move an item into §14 only when the *gap*
is a live footgun someone will hit.

Legend: `✅` done · `🚧` in progress · `⬜` not started · `n/a` not applicable.

**Standards / delivery**

| Standard | Status | Notes (this product) |
|---|:--:|---|
| CI/CD via GitHub Actions on the per-product self-hosted runner (§9); no manual rsync | `⬜` | |
| Deploy from an **image digest**, not a per-colour rebuild — "the exact binary" is provable | `⬜` | |
| **Migrations as a first-class step** (versioned, forward-only), not idempotent DDL at startup | `⬜` | |
| **Health vs readiness** endpoints distinguished (a crash-looping container can't answer 200 and look healthy) | `⬜` | |
| One **`make` verb per environment**; no manual `podman-compose` in a runbook | `⬜` | |
| **Dedicated staging stack with its own DB** — true pre-prod isolation (§6) | `⬜` | |
| **Platform edge as code** — Tier-1 hostnames generated from §0 of each STACK file | `⬜` | |
| **Automated backups + restore drills** for every stateful store (auth db, leads, …) | `⬜` | |

**Security**

| Standard | Status | Notes (this product) |
|---|:--:|---|
| App **never connects as the bootstrap superuser** — dedicated owner role (NOSUPERUSER, NOBYPASSRLS, CREATEROLE). Trap: `ALTER ROLE … NOSUPERUSER` needs superuser, so set attributes at `CREATE ROLE` and never restate them | `⬜` | |
| **Least privilege per datasource** — a Grafana datasource runs arbitrary SQL for whoever reaches the dashboard (incl. anonymous); grant its DB role exactly the tables the public panels read | `⬜` | |
| **Secrets management** — no plaintext `deploy/secrets.env`; SOPS/age or a vault, with an inventory (which secret, where, what breaks on rotation; derived per-org secrets break tenants silently) | `⬜` | |
| **No provider checkout / secret URL in a served page** — it ships to every environment, including the one meant to cost nothing | `⬜` | |
| **Image hygiene** — pin digests, scan (Trivy), SBOM/dependabot, drop caps, read-only rootfs | `⬜` | |
| **`block_scanners` at Tier 2** — 403 on `/.env`, `/.git`, dir listings, … (§4); no dev server serving its own working tree | `⬜` | |
| **Invariants re-verified after each deploy** — re-prove anonymous SQL cannot read private tables, every time | `⬜` | |
| **HA / SPOF** — managed or replicated prod DB; a second VM / apex failover | `⬜` | |

---

## 16. Reuse as a template for the next SaaS

1. Create a rootless user `<product>`; assign a subuid/subgid range; pick a free
   **port band** (`8Nxx`) and record it in the `confinia/platform` Caddyfile table.
2. Add its hostnames to Tier 1 **by PR to `confinia/platform`**:
   `<product>.confinia.io` + `api.` + `staging(.api).` + `sandbox(.api).` →
   the router/staging/sandbox ports.
3. Clone the stack dirs: `auth_stack/` (own realm), `caddy_server/` (Tier-2
   router with `block_scanners`), `docker-compose.yml` +
   `deploy/{blue,green}.override.yml`, `staging_stack/`, `sandbox_stack/`.
4. Copy `deploy/*.sh` (set `HOST=<product>`) and this file as
   `STACK_<product>.md` (same headers); fill §0.
5. `deploy.sh` → `staging.sh` → `promote.sh`. Then wire Actions.

---

## Merging two STACK files

Compare in this order — the first difference that matters usually stops the
discussion: **isolation model** (what guarantees tenant separation) → **secret
handling** → **deployment unit** (image digest vs rebuild) → **rollback story** →
then **port bands** and **naming**. Keep the section headers identical across
products so differences reduce to the tables in §0 and §3–§6; cosmetic
differences below that line are not worth reconciling.
