# PORTS.md — Confinia's reserved port range on the shared VM

The VM hosts several products (confinia, overwatch/orbit-poc, ecobuilding,
indoorequal, maplibre…), each with its own app caddy behind the platform
upstream caddy. To avoid grabbing generic ports that another product may want,
**Confinia only binds host ports inside its reserved ranges.**

> Where these ports sit in the overall architecture — the two caddy tiers, the
> colour stacks and the shared services — is [STACK_confinia.md](STACK_confinia.md).

## Reserved ranges (Confinia)

| Range | Purpose |
|---|---|
| **8085–8099** | HTTP services (loopback `127.0.0.1` only, except where a public bind is required and firewalled) |
| **5440–5449** | PostgreSQL (ops + color geo databases) |
| **2085** | app caddy admin address (unique-admin VM rule) |

Anything Confinia adds must take the next free port **inside these ranges**.
Never bind a generic port (8000, 3000, 4318, 8080, 8180…) on the host.

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
