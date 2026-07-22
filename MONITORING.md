# MONITORING — what is watched, and how

Ground rule (GDPR posture): **no IP address is ever stored**. Dimensions
are bounded (country, route, status, event); anything that could identify
a person is reduced to irreversible salted hashes.

## Two layers

| Layer | Tool | What it watches |
|---|---|---|
| **Platform** (separate repo `platform`) | grafana.confinia.io | the VM itself: CPU, RAM, disk, network, IO; blackbox probes of the vhosts |
| **Application** (this repo) | www.confinia.io/grafana | everything below |

Application chain: the API emits **OpenTelemetry** metrics → collector
(port 4318) → **Prometheus** (180-day retention for long usage trends) →
**application Grafana** (provisioned from `deploy/grafana/provisioning/`,
admin password in `deploy/secrets.env`, sign-up disabled).

## What is monitored

### API traffic — `confinia.requests` counter
One series per (route, method, status, country, client, keyed):
- **route**: the FastAPI template (`/v1/units`…), never the raw URL;
  404s go through a cardinality guard (`label_404`) that buckets unknown
  paths;
- **country**: GeoIP (DB-IP Country Lite, CC BY 4.0) on the in-transit IP,
  never persisted;
- **client**: `demo` / `site` / `direct`, derived from Origin/Referer
  (bounded);
- **keyed**: whether the request carried a valid API key.
Panels: req/s per route, p50/p95 (`X-Response-Time-Ms`), status
distribution, calling countries, top routes.

### Security — the 404 → edge-filter loop
The "404 by path" panel (Security row) lists what scanners still probe;
recurring patterns are manually fed into the Caddyfile `(block_scanners)`
groups (abort before the API). Already-filtered paths no longer appear:
the panel only ever shows what remains to handle.

### Frontend — `confinia.frontend.events` counter
Demo UI events via `/beacon` (allow-list: load, play, timetravel,
commune_history, dept/region/country switches, share, diff), dimensioned
by event and country. "Frontend" dashboard row.

### Unique visitors — ops table `visitor_daily`
A visitor = `sha256(secret + UTC day + ip)`: irreversible, not comparable
across days. UNLOGGED table, purged after 45 days. Panel: unique visitors
per day and country.

### Revenue and usage — ops tables (source of the upcoming business panels)
- `api_key` (+ `tier`) and `api_usage`: consumption per key per day;
- `premium_usage`: lifetime premium-report counter per caller (the
  "9 free then 402" quota);
- `upgrade_intent`: payment intents left on /pricing;
- `polar_subscription`: subscription state pushed by the webhooks.
Planned panel (issues #8/#19): MRR proxy = active subscriptions per tier.

### Deployment health
- `/healthz` per color (version + row count): the blue/green switch
  contract (`deploy-api.sh` waits for the passive healthz before
  promoting);
- application caddy: active health checks on the color upstreams with
  `fail_duration` (zero requests to a dead upstream during switches).

### CI (issue #18)
The `subscription-tests` workflow replays both revenue journeys (signup,
Polar provisioning) on every push, every PR and weekly; results in
TEST_SUBSCRIPTION.md and TEST_POLAR.md.

## What is deliberately NOT collected
IP addresses at rest, individual identifiers in metrics, unbounded raw
URLs, cookies/trackers on the demo and site. The only nominative data in
the system is the voluntarily provided email (API key, intent,
subscription, Keycloak account), stored in the ops database.
