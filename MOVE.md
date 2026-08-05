# MOVE.md — moving the Confinia stack to its own Unix user

**Status: DONE — the cutover completed on 2026-08-11.**
Production runs as `confinia`. About **23 minutes of downtime**, behind the
maintenance page. Every success criterion matched on the first attempt. See
"What actually happened on the successful run" below; the 2026-08-01 failure is
kept because its lessons are what made this one work.

The cutover was attempted and failed on the database restore. Production was
down for about **15 hours** (17:42 UTC to 08:59 UTC) before rollback, against a
30 to 60 minute estimate. No data was lost: the old stack was never deleted, and
its 10 Keycloak users, 12 API keys and 1 subscription were verified intact
afterwards.

Read "What actually happened" before planning another attempt.

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

## What actually happened (2026-08-01)

### The technical cause

**`podman exec -i` silently truncates its stdin** when a large file is
redirected into it from a non-interactive SSH session. The restore reported no
error and exited cleanly, but only part of the dump had been read.

The damage was invisible at first glance: the business database looked right
(12 API keys, 1 subscription) while the Keycloak database had **87 tables and
zero realms, zero users**. A superficial check would have declared success and
lost every customer account.

Copying the file into the container with `podman cp` and running `psql -f`
removes the stdin path and is the right fix in principle, but that variant also
failed, with **empty logs and a non-zero exit code** that was never explained.
That is where the attempt should have stopped.

### The process failures, which cost more than the bug

1. **No time limit was agreed in advance.** The cutover ran for hours instead of
   being abandoned after twenty minutes. Rollback was available the whole time
   and cost two commands.
2. **Success was measured by "no error shown" rather than by counting rows.**
   The restore was called clean while Keycloak was empty.
3. **The procedure had never been rehearsed.** It went straight to production on
   its first ever run.

### Rules for any future attempt

- **Rehearse first, with no downtime**: restore the dump into a throwaway
  database and verify it there. Production is touched only once the restore is
  *proven*, not hoped for.
- **Success criteria are counts, not the absence of messages** (see step 3).
- **A declared decision point, not a stopwatch** (revised by the founder,
  2026-08-05: *"take the time needed to do it properly"*). The original rule was
  a hard 30-minute limit. Rushing a cutover is its own way of breaking things, so
  the window may be as long as the work honestly needs — but the failure mode the
  limit protected against is **persisting without deciding**, and that one is
  real: the 2026-08-01 attempt ran for hours because no one ever stopped to ask
  whether to continue.

  So, before starting, write down **one checkpoint** — a step and a wall-clock
  time — at which you stop and answer, out loud, a single question: *do I know
  what is wrong, or am I guessing?* Guessing means roll back and investigate with
  the service up. Knowing means continue and set the next checkpoint. Rollback is
  two commands and costs nothing; an unbounded night costs fifteen hours.

  The maintenance page (`./deploy/maintenance.sh up`) is what makes a longer
  window acceptable: visitors get an explanation and a `Retry-After` instead of a
  bare 502, and automated clients recover on their own.
- **Never restore through `podman exec -i < file`.** Use `podman cp` then
  `psql -f`, and verify the row counts afterwards regardless.
- Announce the maintenance window beforehand: the service is public.
- **Put the maintenance page up before stopping anything**, and take it down
  only once the new stack answers. See `deploy/maintenance.sh`; it refuses to
  start while the real caddy still holds `:8085`, so the two cannot fight.

## Rehearsal of the restore — done 2026-08-03, passed

The step that failed is now proven, with production untouched and no downtime.

Method, exactly as the rules require:

```sh
# on debian, with umask 077 so the dump is never world-readable
podman exec confinia_ops-db_1 pg_dumpall -U confinia --clean --if-exists > dump.sql
grep -c "PostgreSQL database cluster dump complete" dump.sql   # 1 = the dump is whole

# hand it over already owned by the target user, never via a readable path
sudo install -o confinia -g confinia -m 600 dump.sql /home/confinia/rehearsal.sql
shred -u dump.sql

# as confinia: throwaway database, podman cp, psql -f  (NEVER podman exec -i < file)
podman run -d --name rehearsal-db -e POSTGRES_PASSWORD=... postgres:16
podman cp /home/confinia/rehearsal.sql rehearsal-db:/tmp/restore.sql
podman exec rehearsal-db stat -c %s /tmp/restore.sql    # compare with the host size
podman exec rehearsal-db psql -U confinia -d postgres -q -f /tmp/restore.sql
```

Result — counts, not the absence of messages:

| Criterion | Expected | Got |
|---|---|---|
| Keycloak users | 10 | **10** |
| Keycloak users **with credentials** | 10 | **10** |
| Keycloak tables | 87 | **87** |
| Keycloak realms | 3 | **3** (`confinia`, `master`, `confinia-sbx`) |
| API keys | 12 | **12** |
| Subscriptions | 1 | **1** |
| `premium_seen` | 16 | **16** |
| Errors during restore | 0 | **0** |

**87 tables *and* 10 users with credentials is the point.** On 2026-08-01 the
restore produced 87 tables and **zero** users, exited cleanly, and printed no
error. Counting tables alone would have declared that success. The two numbers
together are what distinguishes a real restore from an empty schema.

Dump, container and volume were shredded and removed immediately afterwards: the
file carries customer e-mails, API keys and Keycloak credentials.

**What this does and does not clear.** The database restore — the step that cost
15 hours — is no longer a gamble. Everything else in the cutover (volume
export/import, edge-state paths, image rebuilds, caddy) is unrehearsed. The hard
30-minute limit still applies, and the maintenance window must still be
announced.

## Phase 1 done, and the second mechanism rehearsed — 2026-08-04

**No downtime taken. Production untouched.** Everything below is staged and
verified; only the cutover remains.

**Artefacts prepared and handed over**, checksums compared on both sides rather
than sizes — an early `ls` showed `prom.tar` at 137 MB while it was still being
written, which is the same shape as the truncation that caused the outage:

| Artefact | Size | sha256 matches |
|---|---|:--:|
| `opsdata.sql` (cluster dump, completion marker present) | 49 MB | ✅ |
| `geo-green.dump` (passive colour, so production carried no load) | 675 MB | ✅ |
| `grafana.tar` | 48 MB | ✅ |
| `prom.tar` | 315 MB | ✅ |

All `-rw-------`, owned by `confinia`, in `/home/confinia/move` (mode 700).

**Second risky mechanism rehearsed: cross-user `podman volume import`.**
The concern was ownership across user namespaces (`debian` maps 100000-165535,
`confinia` maps 165536-231071). Imported `grafana.tar` into a throwaway volume as
`confinia` and read it back from a container: **543 files, `grafana.db`
1 667 072 bytes**. Content verified, not the exit code. Throwaway volume removed.

**Target user prerequisites, checked so nothing is discovered during the window:**

| | |
|---|---|
| podman / podman-compose | 5.4.2 / 1.3.0 ✅ |
| git · curl · rsync · python3 | ✅ |
| `psql` on the host | absent — not needed, everything goes through `podman exec` |
| subuid/subgid | `confinia:165536:65536` ✅ |
| repo clone | `~/projects/confinia` at `main` ✅ |
| `deploy/secrets.env`, `deploy/sandbox.env` | present, `600`, byte-identical ✅ |
| `~/confinia-edge-state` | copied and owned ✅ |

Two traps met and worth knowing before the window:

- **The clone already existed** from the 2026-08-01 attempt, three days stale on
  branch `move-to-own-user-99`. A `[ -d .git ] || git clone` guard skips
  silently. Reset to `main` explicitly.
- **podman-compose and relative paths** — the trap `deploy/stacks.sh` already
  documents. Use absolute `-f` paths in every cutover command.

**What is still unrehearsed:** the image rebuilds under the new user, the caddy
start on `:8085` (which is where the downtime actually begins, since both
caddies cannot bind it at once), and the edge-state paths in practice.

## What actually happened on the successful run (2026-08-11)

**~23 minutes of downtime** (caddy stopped ~20:52 UTC, new caddy up 21:16), of
which the first two minutes were a bare 502 before the maintenance page went up.
Within the declared decision point of 25 minutes.

Success criteria, all matched on the first attempt — and note they are **not**
the numbers written in this file a week earlier:

| Criterion | Expected | Got |
|---|---|---|
| Keycloak users | 11 | **11** |
| …with credentials | 11 | **11** |
| Keycloak tables | 87 | **87** |
| Realms | 3 | **3** |
| API keys | 13 | **13** |
| Subscriptions | 1 | **1** |
| `premium_seen` | 24 | **24** |
| `visitor_daily` | 1306 | **1306** |
| `commune_version` | 205 370 | **205 370** |
| Production smoke | 11 passed | **11 passed** |

### Five things that went wrong, and none of them cost the window

- **The staged artefacts were six days stale.** Between them and the cutover the
  ops database had gained *a Keycloak account, an API key and eight
  `premium_seen` rows*. Restoring them would have deleted a real customer while
  every count still looked plausible. Re-dumped inside the window.
- **This document told me to do the thing it forbids**: the cutover block used
  `podman exec -i … < file`, the exact pattern that caused the 15-hour outage.
  Fixed before following it.
- **`podman-compose down` did not stop the colour stacks** ("network is being
  used"), so the old blue kept holding `:8091` and the new one could not bind.
  Stop the containers explicitly, not just the project.
- **Postgres restores race with `initdb`.** `pg_isready` answers during the
  container's *initialisation* server, which then shuts down mid-restore. Wait
  for the container to report **healthy**, not for `pg_isready`.
- **The volume rehearsal tested reading, not writing.** Grafana came up and
  failed with `attempt to write a readonly database`. Reading 543 files proved
  nothing about the process that has to write them. A `chown -R 472:0` and a
  restart fixed it — but the rehearsal should have written.

### Still to do after the move

- Rebuild **green** by double ingestion (`stacks.sh build green`). Until then
  blue serves production **with no rollback target**.
- Move the GitHub Actions runner to the `confinia` user (#114) — this migration
  is what unblocked it.
- Only then, as `debian`: delete the old volumes, `~/projects/confinia`, and the
  legacy `confinia_pgdata`.

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

⚠️ **Re-dump inside the window. Never restore artefacts staged days earlier.**
The phase-1 artefacts prove the *method*; they are not the data to ship. Between
2026-08-05 and 2026-08-11 the ops database gained **a Keycloak account, an API
key and eight `premium_seen` rows**. Restoring the staged dump would have
deleted a real customer — silently, with every count still looking plausible.

Re-read the success criteria immediately before starting; they are whatever the
live database says at that moment, not what is written in this file.

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
# wait for readiness, then restore — podman cp + psql -f, NEVER `exec -i < file`,
# which is what silently truncated stdin and cost 15 hours (see the rules above).
# This procedure block used to contradict its own rule; it no longer does.
podman cp ~/move/opsdata.sql confinia_ops-db_1:/tmp/opsdata.sql
podman exec confinia_ops-db_1 stat -c %s /tmp/opsdata.sql   # compare with the host size
podman exec confinia_ops-db_1 psql -U confinia -d postgres -f /tmp/opsdata.sql
podman volume import confinia_grafana_data ~/move/grafana.tar
podman volume import confinia_prom_data    ~/move/prom.tar
podman-compose up -d
./deploy/stacks.sh up-db blue
podman cp ~/move/geo-blue.dump confinia-blue_db_1:/tmp/geo.dump
podman exec confinia-blue_db_1 pg_restore -U confinia -d confinia /tmp/geo.dump
podman-compose -p confinia-blue -f deploy/stack/docker-compose-blue.yml --profile serve up -d api
./deploy/stacks.sh promote blue
```

**Session `debian`** — the one privileged step, easy to forget:

```bash
sudo loginctl enable-linger confinia
```

### 3. Verify before declaring success

- **count the rows before anything else**: `SELECT count(*) FROM user_entity`
  in `keycloak` must return **10**, `api_key` **12**, `polar_subscription` **1**.
  An empty Keycloak with a full schema is exactly what the failed run produced,
  and it looks like success until someone tries to log in;
- `curl -sf https://api.confinia.io/healthz` returns the expected version;
- **sign in on the account page**: the real test that the Keycloak database
  survived, and the step that must never be skipped;
- a paid account still shows its plan (ops database intact);
- `tests/smoke_prod.py` against production;
- the Grafana environments dashboard shows the colours and probes;
- **do NOT reboot the VM to test linger**: it is shared with other products and
  another session works on it. Check `loginctl show-user confinia | grep Linger`
  instead.

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
