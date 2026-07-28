# PORTS.md — Confinia's reserved port range on the shared VM

The VM hosts several products (confinia, overwatch/orbit-poc, ecobuilding,
indoorequal, maplibre…), each with its own app caddy behind the platform
upstream caddy. To avoid grabbing generic ports that another product may want,
**Confinia only binds host ports inside its reserved ranges.**

## Reserved ranges (Confinia)

| Range | Purpose |
|---|---|
| **8085–8099** | HTTP services (loopback `127.0.0.1` only, except where a public bind is required and firewalled) |
| **5440–5449** | PostgreSQL (ops + color geo databases) |
| **2085** | app caddy admin address (unique-admin VM rule) |

Anything Confinia adds must take the next free port **inside these ranges**.
Never bind a generic port (8000, 3000, 4318, 8080, 8180…) on the host.

## Allocation

| Service | Host port | Status |
|---|---|---|
| app caddy | 8085 (+ admin 2085) | grandfathered — platform upstream routes here |
| sandbox API | **8089** | in range |
| API blue / green / staging | 8000 / 8001 / 8002 | TO MIGRATE → 8091 / 8092 / 8093 |
| Keycloak | 8180 | TO MIGRATE → 8087 |
| application Grafana | 3000 | TO MIGRATE → 8086 |
| OTel collector | 4318 | TO MIGRATE → 8088 (loopback) |
| ops-db | 5440 | in range |
| blue / green geo db | 5441 / 5442 | in range |
| demo preview (profile tools) | 0.0.0.0:8080 | TO FIX → loopback, in range |

## Migration note

Moving the PROD services (API blue/green/staging, Keycloak, Grafana, OTel)
touches the blue/green stacks, the deploy scripts (`port_of`, `wait_ok`),
the generated edge state (`~/confinia-edge-state/*.caddy` upstreams), and the
app Caddyfile at once. It must be done as a deliberate step (not mid-test),
verifying `/healthz` on each color and a clean blue/green switch afterwards.
Databases (5440–5442) already sit in range and do not need to move.
