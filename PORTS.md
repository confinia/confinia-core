# PORTS.md — Confinia's reserved port range on the shared VM

The VM hosts several products (confinia, overwatch/orbit-poc, ecobuilding,
indoorequal, maplibre…), each with its own app caddy behind the platform
upstream caddy. To avoid grabbing generic ports that another product may want,
**Confinia only binds host ports inside its reserved ranges.**

> Where these ports sit in the overall architecture — the two caddy tiers, the
> colour stacks and the shared services — is [STACK_confinia.md](STACK_confinia.md).

## Reserved ranges (Confinia)

### NEW: band 11xxx — the 1PESI scheme (platform PR #8, 2026-08-14)

The platform now allocates one **thousand-block per product**, digits
spelling `1 · Product · Env · Service · Instance` (platform RULES.md §3).
Confinia's product digit is **1** → band **11000–11999**. Read a port like
a sentence: `11320` = confinia(1) · staging(3) · api(2) · main(0).

Old → new map (dual-published during the migration; legacy dropped at the
platform's decommission step):

| Service | Legacy | 1PESI |
|---|---|---|
| app caddy (all hostnames) | 8085† | **11000** |
| app caddy admin | 2085 | **11090** (at flip) |
| grafana | 8086 | **11040** |
| otel OTLP http / grpc | 8088† / 4317† | **11060 / 11062** (stays 0.0.0.0 — pushed via `host.containers.internal`; ufw-shielded; network-join is the real fix) |
| otel prometheus exporter | 8094† | **11061** |
| keycloak | 8095 | **11070** |
| blue api / geo db | 8091 / 5441 | **11120 / 11130** |
| green web / api / geo db | 8401 / 8402 / 5442 | **11210 / 11220 / 11230** |
| staging api | 8403 | **11320** |
| sandbox api | 8089 | **11420** (direct swap — stack was down) |
| demo preview | 8097 | **11510** |
| ops-db | — | none (network-join since 2026-08-12, publishes nothing) |

**11434 is BURNED inside our band**: the VM-level `ollama` service (own
Unix user, systemd, binds 0.0.0.0) sits there. Never bind it; the platform
tracks its relocation.

### Legacy ranges (valid until the platform decommissions them)

| Range | Purpose |
|---|---|
| **8085–8099** | HTTP services (loopback `127.0.0.1` only, except where a public bind is required and firewalled) |
| **84xx** | green web/api + staging api |
| **5440–5449** | PostgreSQL (ops + color geo databases) |
| **2085** | app caddy admin address (unique-admin VM rule) |

Anything Confinia adds now takes the next free port **inside 11xxx,
following the digit scheme**. Never bind a generic port (8000, 3000, 4318,
8080, 8180…) on the host.

## Allocation (issue #75: migration DONE)

Reality check (2026-07-29): the 8085-8099 range is NOT exclusively ours on the
VM — other products already sit inside it (mapmax edge 8087, orbit-poc caddy
8090, ovw2 keycloak 8096, unknown python 8099). The platform's actual scheme is
closer to "one 80xx band slot per product". Confinia therefore only claims the
ports below, all verified free at migration time; the platform Caddyfile band
table (founder-only, RULES 8) remains the authority.

| Service | Host port | Notes |
|---|---|---|
| app caddy | **8085** (+ admin 2085) | platform upstream routes every confinia host here |
| application Grafana | **8086** (loopback) | container port stays 3000; caddy /grafana -> 8086 |
| OTel collector — OTLP | **8088** (all ifaces, ufw-blocked) | stack APIs push via host.containers.internal |
| sandbox API | **8089** (loopback) | |
| API blue | **8091** (loopback) | container port stays 8000 |
| API green | **8402** (loopback, band **84xx**) | moved off 8092 on 2026-08-11, see BURNED below |
| **STAGING API** | **8403** (loopback, band **84xx**) | dedicated staging stack (issue #113). Was 8501 for a few hours on 2026-08-12 — **85xx belongs to panoramax**, and I had only checked the port was free, not that it was ours |
| ~~staging data slot~~ | ~~8093~~ | **removed 2026-08-11**: the port is BURNED, and probing it sent a request to another tenant's service on every staging call |
| OTel collector — prometheus exporter | **8094** (all ifaces, ufw-blocked) | host-network since issue #85 |
| Keycloak | **8095** (loopback) | container port stays 8180; caddy /auth -> 8095 (8087 squatted by mapmax) |
| demo preview (profile tools) | **8097** (loopback) | was 0.0.0.0:8080 (public exposure fixed; 8090 squatted by orbit-poc) |
| ops-db | **5440** (all ifaces, ufw-blocked) | |
| blue / green geo db | **5441 / 5442** (loopback) | |

Taken by OTHER products inside 8085-8099: 8087 (mapmax), 8090 (orbit-poc),
8096 (ovw2), 8099 (unknown python). Free for future Confinia use: **8098** only —
coordinate with the platform band table before claiming anything else.

## Note

The platform Caddyfile's comment band still mentions the old notable ports
(`8000/8001 api, 8180 kc`); it is founder-only (RULES 8) — suggested new text:
`8086 grafana, 8088 otlp, 8089 sbx, 8091 api-blue, 8095 kc` + band **84xx** for GREEN. No routing change
(the platform only ever targets 8085).

## BURNED ports — never bind these again (2026-08-11)

Squatted by other tenants **outside their own bands**. Confinia does not fight
for them; the platform band table records them as burned.

| Port | Held by | What it cost us |
|---|---|---|
| **8092** | `maplibre` | the green colour could not start at all — `rootlessport listen tcp 127.0.0.1:8092: bind: address already in use`. Diagnosed as four different things over a week before the error message was actually read (issue #123) |
| **8093** | `maplibre` | was our "staging data slot", probed **first** on every staging request. It answers 404 on `/healthz` so caddy marks it down — but had it ever answered 2xx, our staging traffic would have been proxied into another product's application |
| **8096** | `overwatch` | — |
| **8098** | an nginx | bound on `0.0.0.0`, not loopback |

**GREEN now owns band `84xx`** (8401 web — reserved, unused today since colours
have no per-colour web; **8402 api**). Nothing else on the VM uses 84xx. Same
precedent as overwatch's green move to 90xx.

The lesson worth carrying: a reserved band is a *convention*, not an
enforcement. `ss -ltnp` is the only source of truth about who holds a port, and
a deploy script must consult it before assuming a port is its own.

## `confinia_ops-db_1` publishes `0.0.0.0:5440` — measured, not yet changed

Raised by the platform audit on 2026-08-12: the operational database — customer
accounts, API keys, billing state — is bound on **all interfaces**, and is
private only because `ufw` denies incoming by default. One firewall rule between
that table and the internet.

**Rebinding it to `127.0.0.1:5440` would break production.** Measured from inside
a running API container:

```
127.0.0.1                -> 127.0.0.1     5440 REFUSED   (the container's own loopback)
host.containers.internal -> 169.254.1.2   5440 OPEN
```

Every API container reaches the ops database through `host.containers.internal`,
which is **not** the host's loopback. A loopback bind makes it unreachable from
production, staging and sandbox at once. And 169.254.1.2 cannot be bound
directly either: it is an alias inside the container network namespace, not an
address on the host (`ip addr` shows only `127.0.0.1` and the public IP).

**The right fix removes the host port entirely.** Proven on 2026-08-12: connect
the ops database to a colour network and it is reachable by container name, with
nothing published:

```
podman network connect confinia-green_default confinia_ops-db_1
confinia_ops-db_1 -> 10.89.2.17:5432 OPEN
```

Staging now runs that way end to end, with `OPS_DSN=...@confinia_ops-db_1:5432/...`.

**DONE 2026-08-12.** Both colours, staging, sandbox and Keycloak reach the
database by container name; `confinia_ops-db_1` publishes nothing. `ufw` is no
longer the only thing between the customer accounts and the internet.

Each colour was switched separately, relying on caddy's fallback to the other —
production answered 200 throughout.

Three things learned doing it, worth knowing before touching this again:

- **Keycloak used the same host port**, and it holds the 11 customer accounts.
  Removing the port would have taken identity down while everything else looked
  healthy. A test caught it *before* the change was applied.
- **`podman network connect` does not survive a recreate.** Connecting the
  running container by hand and then recreating it left the new one on
  `confinia_default` only: every quota check, API-key lookup and billing read
  failed on all three environments **while `/healthz` stayed green**, because it
  reads only the geo database. The membership is now declared in
  `docker-compose.yml` and proven with `--force-recreate`.
- **Recreating the database requires restarting the API containers.** Their
  connection pools hold dead sockets and return 500s until they do.
- **`deploy/secrets.env` carries `OPS_DSN`, and I fixed the running containers
  without fixing it.** For 24 hours the declaration still said
  `host.containers.internal:5440`, a port that no longer existed. CI then
  recreated the green colour from that declaration and it **exited cleanly, code
  0, about 60 seconds after each start** — no crash, no error status, just
  `Application startup failed. Exiting.` Production was one recreate away from
  the same fate, and was only alive because it happened to be the container I
  had patched by hand. Both colours have since been recreated **from the
  declaration**, which is the only version of "it works" that counts.
