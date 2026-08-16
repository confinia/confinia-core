# MOVE.md — migrations of the Confinia stack

## The move to a dedicated Unix user is DONE (2026-08-11/12, issue #99)

The stack runs as `confinia`, with its own podman store, lingering enabled and
no sudo. `ssh confinia` is the working account; `ssh cka-ovh-dedicated-01`
(the `debian` account) is for maintenance and fixes only.

~23 minutes of downtime, every row count matching first time
(205 370 / 2 128 / 1 285 119). The full procedure, its rehearsals and the two
incidents it was written around are in this file's git history — they are not
repeated here, because a procedure kept after its migration gets followed by
mistake.

# The 1PESI port migration (band 11xxx) — for the platform session

A second move, unrelated to the Unix-user one above: every Confinia host port
moves into the band the platform allocated us, **11000–11999**, digits spelling
`1 · Product · Env · Service · Instance` (platform RULES §3). Confinia's product
digit is **1**. Read a port as a sentence: `11320` = confinia(1) · staging(3) ·
api(2) · main(0).

Delivered by platform PR #8, step 2 of 5, as a **dual publish**: every service
answers on its legacy port *and* its 11xxx port at the same time, so the edge can
be flipped without a flag day and rolled back by doing nothing.

## State on 2026-08-16 — 9 of 10, verified with `ss`

`podman ps` is **not** evidence. It reported `confinia-green_db_1` mapping both
5442 and 11230 while `ss` showed neither listening: the rootless port-forwarder
had died. Every line below was checked with `ss -ltn`, and the vhosts with a
real HTTP request.

| Service | Legacy | 1PESI | Live | How it got there |
|---|---|---|---|---|
| app caddy (all six vhosts) | 8085 | **11000** | ✅ | `./deploy/deploy-edge.sh` — graceful `caddy reload`, **zero downtime**, no recreate |
| app caddy admin | 2085 | 11090 | — | at flip |
| grafana | 8086 | **11040** | ✅ | compose `--force-recreate` |
| otel OTLP http / grpc | 8088 / 4317 | **11060 / 11062** | ✅ | compose `--force-recreate`; stays on all interfaces, ufw-shielded |
| otel prometheus exporter | 8094 | **11061** | ✅ | same recreate |
| keycloak | 8095 | **11070** | ✅ | compose `--force-recreate`; **~2 min identity downtime** — Quarkus re-augments its image on recreate, which a plain restart does not do |
| green api / geo db | 8402 / 5442 | **11220 / 11230** | ✅ | systemd unit restart (api) + compose recreate (db) |
| staging api | 8403 | **11320** | ✅ | `./deploy/staging-up.sh` |
| sandbox api | 8089 | **11420** | ✅ | `./deploy/sandbox-up.sh` — **direct swap**, no dual publish: the stack was down at migration time |
| blue api / geo db | 8091 / 5441 | **11120 / 11130** | ❌ | **waits on the founder** — blue is the ACTIVE colour |
| ops-db | ~~5440~~ | none | ✅ | publishes nothing since 2026-08-12; reached by container name |

**11434 is BURNED inside our own band**: the VM-level `ollama` service (own Unix
user, systemd, binds 0.0.0.0) sits there. Never bind it.

## Evidence the platform can re-check

All six vhosts, both ports, identical answers:

```
www.confinia.io              8085=200  11000=200
api.confinia.io              8085=200  11000=200
staging.confinia.io          8085=401  11000=401     (401 = the auth gate, edge answering)
staging.api.confinia.io      8085=401  11000=401
time-slider.confinia.io      8085=301  11000=301
sandbox.confinia.io          8085=401  11000=401
```

## What is left

1. **Blue → 11120 / 11130.** Blue is active, so the clean path is: promote green
   (`promote-production`, manual and reviewed by the founder), recreate blue as
   the passive colour, promote back if blue should be active again.
2. **The platform flips the six hostnames to 11000**, then asks us to drop the
   legacy publishes.
3. **Legacy drop and decommission** — removing the second `ports:` line from each
   service and the legacy receivers from `deploy/otel-collector.yaml`.

## Three traps, paid for once each

**A merge opens no ports.** Dual-publish landing on `main` changed nothing on the
VM: each container keeps its old publisher until it is **recreated**. Between the
merge and the recreates, zero 11xxx ports were listening while the declaration
said otherwise.

**A systemd unit publishes what it declares, and nothing else.** A green Quadlet
unit was already installed on the VM carrying `8402` alone, and `deploy-api.sh`
prefers the unit over compose. Green would never have reached 11220 however many
times it was recreated. The units now carry both bands, and a test refuses one
that does not.

**A recreate is not a reload.** The caddy step was held back for fear of the 46
seconds of downtime a *recreate* cost once. `deploy-edge.sh` does a graceful
`caddy reload` instead: validated in an ephemeral container first, then reloaded
in place, with production answering 200 throughout.

---

## Prompt for the platform Claude Code session

Copy everything between the rules below and paste it into the platform session.

---

Confinia is ready for the edge flip to band 11xxx. Step 2b is complete on our
side: **9 of our 10 services answer on their 1PESI port**, dual-published
alongside the legacy ports, so the flip is reversible by doing nothing.

**The app caddy now listens on `:11000`.** It was brought up with a graceful
`caddy reload` (validated in an ephemeral container first), not a recreate —
production answered 200 throughout. All six Confinia vhosts return identical
status codes on both ports:

```
www.confinia.io              8085=200  11000=200
api.confinia.io              8085=200  11000=200
staging.confinia.io          8085=401  11000=401     (401 = our auth gate; the edge is answering)
staging.api.confinia.io      8085=401  11000=401
time-slider.confinia.io      8085=301  11000=301
sandbox.confinia.io          8085=401  11000=401
```

Also listening, verified with `ss -ltn` rather than `podman ps`: grafana
`11040`, otel `11060`/`11061`/`11062`, keycloak `11070`, green api/db
`11220`/`11230`, staging api `11320`, sandbox api `11420`. The ops database
publishes no host port at all since 2026-08-12 and is reached by container name.

**Please proceed with the flip**: verify with `ss`, probe `11000` per vhost, then
point all six `confinia` hostnames at `11000`.

Two things to know before you do:

1. **Blue is not on the band yet** (`11120`/`11130`). Blue is our ACTIVE colour,
   and moving it goes through a production promotion that only the founder can
   approve. Our caddy falls back between colours, so the flip does not depend on
   it — but do not expect `11120` to answer until the founder has run the
   promotion.
2. **`11434` is burned inside our own band.** The VM-level `ollama` service (own
   Unix user, systemd, binds `0.0.0.0`) sits there. We will never bind it; it
   needs relocating on your side to make our band whole.

After the flip, tell us and we will drop the legacy publishes — the second
`ports:` line on each service and the legacy receivers in
`deploy/otel-collector.yaml` — so you can close the decommission step.

One request for the band table: record that **`8092`, `8093`, `8096` and `8098`
are burned for us** (taken by `maplibre`, `overwatch` and an nginx outside their
own bands), and that `8501`–`8503` are panoramax's, which we self-assigned from
by mistake on 2026-08-12 and vacated the same day.

---
