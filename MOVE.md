# MOVE.md — moving the Confinia stack to its own Unix user

**Status: proposal for review. Nothing here has been applied.**

From `/home/debian/projects/confinia` (user `debian`)
to `/home/confinia/projects/confinia` (user `confinia`).

## Why it is worth doing

Today every product on the VM runs under the **same** account: `orbit-poc` and
`mapmax` were verified running as `debian`, alongside Confinia. One account
compromised means all of them compromised, and any of them can read Confinia's
`deploy/secrets.env`, its database volumes and its backups.

Moving Confinia to its own user is the cheap half of "separate the VM if the
service becomes a target" (SECURITY_ANALYSIS, action 7): real isolation, no new
machine, no new cost.

**Honest cost:** a cutover with downtime, and one risky step (the operational
database, which holds customer accounts). Everything else is reversible.

The `confinia` user **already exists** (uid 1001) with its subuid range
allocated (`confinia:165536:65536`), so the groundwork is done.

## The one fact that shapes the whole procedure

**Rootless podman is per-user.** Volumes live under
`~/.local/share/containers/storage/volumes/`, and their files are owned by
*subordinate* UIDs of the owning user: `debian` maps 100000–165535, `confinia`
maps 165536–231071.

Copying volume directories from one user to the other therefore leaves every
file owned by a UID that means nothing in the new namespace. PostgreSQL refuses
to start on a data directory it does not own, and `chown -R` across namespaces
is guesswork.

So: **never copy volume directories.** Use logical dumps for databases and
`podman volume export | podman volume import` for the rest, both of which
re-own correctly.

## Inventory

| What | Where | Precious? | How it crosses |
|---|---|---|---|
| **ops database** (api_key, api_usage, polar_subscription, premium_seen, **keycloak**) | volume `confinia_opsdata`, 197 MB | **CRITICAL** — customer accounts and credentials live here | `pg_dumpall` → restore |
| geo database blue | `confinia-blue_pgdata`, 1381 MB | no, rebuildable artifact | dump/restore **one** colour, rebuild the other |
| geo database green | `confinia-green_pgdata`, 1383 MB | no, rebuildable artifact | double ingestion (`stacks.sh build`) |
| grafana | `confinia_grafana_data` | partly — dashboards are provisioned from files; any manual panel or API key is not | `volume export/import` |
| prometheus | `confinia_prom_data` | metrics history (180 d retention) | `volume export/import`, or accept the loss |
| caddy data/config | `confinia_caddy_data`, `confinia_caddy_config` | no — the platform owns 443 and the certificates | recreate empty |
| `confinia_pgdata` | legacy volume, pre-blue/green | no | **do not move**, delete after the move |
| repo mirror | `~/projects/confinia` | no (git) | rsync or fresh clone |
| **secrets** | `deploy/secrets.env`, `deploy/sandbox.env` | **CRITICAL**, gitignored | copy by hand, `chmod 600` |
| edge state | `~/confinia-edge-state` | yes — the active colour and generated caddy config | copy, then fix the absolute paths (below) |
| raw data | `~/data` (INSEE census 7 MB, GeoIP, sources) | re-downloadable | rsync |
| logs | `~/logs` | no | leave behind |
| images | `confinia-api`, `confinia-ingest` | no | rebuild under the new user |

## Code changes required (this is not only an ops task)

Only **two** hardcoded paths exist in the repo, both in `docker-compose.yml`:

```
line 21:  - /home/debian/confinia-edge-state:/etc/caddy/active:ro
line 96:  - /home/debian/confinia-edge-state:/edge-state:ro
```

Both must become `/home/confinia/confinia-edge-state`. Better: make them
relative to `${HOME}` so the file stops encoding a username at all.

`deploy/stacks.sh` uses `~/confinia-edge-state`, which resolves per-user and
needs no change. Verified by `git grep /home/debian`.

**Outside the repo**, on the founder's Mac: the `~/.ssh/config` alias
`confinia-ovh-debian` points at `debian@…`. It needs a `confinia@` entry (and
the deploy habits that rsync to `/home/debian/projects/confinia`).

**The platform Caddyfile needs no change**: it targets `127.0.0.1:8085`, a port,
not a path. It stays founder-only either way (RULES 8).

## Two SSH sessions, and they are not interchangeable

This is the detail that breaks a run if it is missed:

| Session | User | Rights | Use it for |
|---|---|---|---|
| `ssh debian@…` | `debian` | **sudo, passwordless** | everything privileged: `chown`, writing into `/home/confinia`, `loginctl`, reading the old volumes |
| `ssh confinia@…` | `confinia` | **no sudo at all** (verified: *"User confinia is not allowed to run sudo"*) | podman only: build, compose, restore, promote |

Consequences to respect in every step below:

- **`sudo loginctl enable-linger confinia` runs in the `debian` session**, not
  the `confinia` one. Putting it in the target session fails outright.
- Copying files into `/home/confinia` and fixing ownership is a `debian` task;
  the `confinia` session only ever consumes files it already owns.
- Do not add `confinia` to sudoers for the migration. Needing root inside the
  target account would defeat the isolation the move is for.

Verified already in place: `confinia` exists (uid 1001), its subuid range is
allocated, its shell is `/bin/bash`, and `~/.ssh/authorized_keys` exists, so
`ssh confinia@…` should work today.

## Prerequisites

1. **`ssh confinia@…` confirmed working** from the founder's Mac, and an entry
   added to `~/.ssh/config` next to the existing `debian` alias.
2. `podman info` succeeds **in the `confinia` session** (rootless podman needs
   no root, but it does need a working user session).
3. `sudo loginctl enable-linger confinia`, **from the `debian` session** —
   without it containers do not start at boot, because rootless podman needs a
   lingering session. `debian` already has `Linger=yes`; it is not inherited.
4. A **verified** backup of `confinia_opsdata`, taken before anything else and
   copied **off the VM** (also security action 2, still open).

## Procedure

Ports can only be bound by one user at a time, so this is a **cutover**, not a
parallel run. Nothing is deleted until the new stack is verified: the rollback
is simply to stop the new containers and restart the old ones.

### 1. Prepare, service still running (no downtime)

**Session `debian`** (owns the running stack and has sudo):

```bash
mkdir -p ~/move
podman exec confinia_ops-db_1 pg_dumpall -U confinia > ~/move/opsdata.sql
podman volume export confinia_grafana_data > ~/move/grafana.tar
podman volume export confinia_prom_data    > ~/move/prom.tar
# one colour of geo, to be back online without waiting for a re-ingestion
podman exec confinia-blue_db_1 pg_dump -U confinia -Fc confinia > ~/move/geo-blue.dump

# hand everything over to the target user (sudo lives here, not there)
sudo mkdir -p /home/confinia/move
sudo cp -a ~/move/. /home/confinia/move/
sudo cp -a ~/confinia-edge-state /home/confinia/confinia-edge-state
sudo cp -a ~/data /home/confinia/data
sudo chown -R confinia:confinia /home/confinia/move /home/confinia/confinia-edge-state /home/confinia/data
```

**Session `confinia`** (no sudo, and none needed):

```bash
git clone https://github.com/confinia/confinia-core.git ~/projects/confinia
```

Then, from the **`debian`** session, copy the two secret files across and hand
them over, since they are gitignored and exist nowhere else:

```bash
sudo cp ~/projects/confinia/deploy/secrets.env  /home/confinia/projects/confinia/deploy/
sudo cp ~/projects/confinia/deploy/sandbox.env  /home/confinia/projects/confinia/deploy/
sudo chown confinia:confinia /home/confinia/projects/confinia/deploy/*.env
sudo chmod 600 /home/confinia/projects/confinia/deploy/*.env
```

### 2. Cutover (downtime starts)

**Session `debian`** — stop everything, delete nothing:

```bash
podman-compose -f docker-compose.yml down
podman-compose -p confinia-blue  -f deploy/stack/docker-compose-blue.yml  down
podman-compose -p confinia-green -f deploy/stack/docker-compose-green.yml down
```

**Session `confinia`** — build, restore, start (no sudo anywhere here):

```bash
cd ~/projects/confinia
podman build -t localhost/confinia-api:latest ./api
podman build -t localhost/confinia-ingest:latest ./ingestion
podman-compose up -d ops-db
# wait for readiness, then:
podman exec -i confinia_ops-db_1 psql -U confinia -d postgres < ~/move/opsdata.sql
podman volume import confinia_grafana_data ~/move/grafana.tar
podman volume import confinia_prom_data    ~/move/prom.tar
podman-compose up -d
./deploy/stacks.sh up-db blue
podman exec -i confinia-blue_db_1 pg_restore -U confinia -d confinia < ~/move/geo-blue.dump
podman-compose -p confinia-blue -f deploy/stack/docker-compose-blue.yml --profile serve up -d api
./deploy/stacks.sh promote blue
```

**Session `debian`** — the one privileged step, easy to forget:

```bash
sudo loginctl enable-linger confinia
```

### 3. Verify before declaring success

- `curl -sf https://api.confinia.io/healthz` returns the expected version;
- **sign in on the account page**: this is the real test that the Keycloak
  database survived, and it is the step that must never be skipped;
- a paid account still shows its plan (ops database intact);
- `tests/smoke_prod.py` against production;
- the Grafana environments dashboard shows the colours and probes;
- reboot the VM once, and confirm everything comes back (this is what proves
  `enable-linger` was done).

### 4. Afterwards

- Rebuild the passive colour by double ingestion (`stacks.sh build green`),
  restoring the blue/green guarantee.
- Only then, as `debian`: remove the old volumes and `~/projects/confinia`.
- Delete `confinia_pgdata` (legacy) at the same time.

## After the move: `/home/debian/projects/confinia` is dead

Once the cutover is verified, that directory must never be used again. It stays
on disk only as the rollback path, and it becomes a trap: editing it, rsyncing
to it or deploying from it would touch a stack that no longer serves anything,
silently, while production runs elsewhere.

- All work goes through **`ssh confinia@…`** and `/home/confinia/projects/confinia`.
- The founder's `~/.ssh/config` should make this the default alias, so the old
  path is not reachable by muscle memory.
- Once the old stack is deleted (step 4), remove the `debian` alias entirely.

## Rollback

At any point before step 4, roll back by stopping the `confinia` containers and
running `podman-compose up -d` again as `debian`. Nothing has been deleted, so
the old stack is intact and the only cost is the downtime already spent.

## Expected downtime

Realistically **30 to 60 minutes**: the image builds and the 1.4 GB geo restore
dominate. It can be cut by building the images under `confinia` **before** the
cutover (they do not bind ports, so they can be prepared with the service live).

## Open questions for the founder

1. **Prometheus history**: carry the 180 days over, or start fresh and accept
   losing the usage trends?
2. **Sandbox**: move it in the same window, or leave it down for a day?
3. **Timing**: the account page and the API are down during the cutover. Any
   moment to avoid?
4. Should the other products move to their own users too, later? The isolation
   argument applies to them identically, and a half-isolated VM is only half
   protected.
