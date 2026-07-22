# Confinia

**An EU-hosted API for administrative boundaries with full historical versioning** — query any commune, municipality, or NUTS region *as it existed at any date*, as GeoJSON.

> Boundaries change: communes merge, districts are redrawn, codes get reused. Confinia models every administrative unit as versions valid over `[valid_from, valid_to)`, with parent/child links across mergers and splits — so `code=01033&at=2018-06-01` returns Bellegarde-sur-Valserine, and the same code a year later returns Valserhône.

Status: **public beta**. France at exact event dates back to 1870 (INSEE COG + IGN Admin Express, TRF-GIS before 1943), Germany and the Netherlands from national yearly editions, the rest of Europe via Eurostat LAU + NUTS, the UK at exact legal dates (ONS Code History Database), New Zealand from Stats NZ editions.

## Layout

| Directory | Contents |
|---|---|
| [`ingestion/`](ingestion/) | INSEE COG → temporal model + IGN geometry join → PostGIS (see its [README](ingestion/README.md)) |
| [`api/`](api/) | FastAPI service — the query endpoints |
| [`demo/`](demo/) | MapLibre GL JS time-slider playground — live at [time-slider.confinia.io](https://time-slider.confinia.io) |
| [`deploy/`](deploy/) | Caddyfile — public HTTPS routing on the VM |

## Using the API

Base URL: `https://api.confinia.io` — interactive docs at [`/docs`](https://api.confinia.io/docs).

The commune valid at a date, by INSEE code — returns a GeoJSON Feature:

```bash
curl "https://api.confinia.io/v1/communes?code=01033&at=2018-06-01"   # → Bellegarde-sur-Valserine
curl "https://api.confinia.io/v1/communes?code=01033&at=2020-06-01"   # → Valserhône
```

Same, by point (WGS84):

```bash
curl "https://api.confinia.io/v1/communes?lat=46.11&lon=5.83&at=2015-06-01"
```

Full history of a code — every version with parent/child links (add `&geometry=true` for polygons):

```bash
curl "https://api.confinia.io/v1/communes/01033/history"
```

Feature properties: `code`, `nom`, `valid_from`, `valid_to` (`null` = still valid), `parents`, `children`, `geometry_vintage` (IGN edition used), `geometry_approx` (`true` when inherited from the nearest edition). Served geometry is simplified (~50 m); point-in-polygon queries use the raw geometry server-side.

### Authentication

The API is **open and keyless during the current beta** — the calls above work as-is, subject only to per-IP rate limiting (20 req/s, 400 req/min) to keep the service healthy.

An API-key tier lands before general availability. When it does, mint a key and send it on every request:

```bash
curl -X POST "https://api.confinia.io/v1/keys" \
     -H "content-type: application/json" \
     -d '{"email": "you@example.org"}'         # → {"key": "cfn_…"}

curl "https://api.confinia.io/v1/communes?code=01033&at=2020-06-01" \
     -H "X-API-Key: cfn_…"
```

Keys are metered per request (visible in the account dashboard). A generous free allowance stays keyless; keys unlock higher limits and usage history. Existing keyless calls keep working through the beta — the restriction is additive, not a breaking change.

## Developing

Rules live in [`DEV.md`](DEV.md) — short version: **everything runs in containers** (never host python), the dev/deploy environment is the project VM (podman + podman-compose), the local machine only edits files and rsyncs them over.

```bash
# on the VM, in ~/projects/confinia
make db-up                                    # PostGIS 16 + PostGIS 3.4
make COMPOSE="podman-compose --profile tools" build      # ingest image
podman-compose --profile tools run --rm ingest /app/ingest_cog.py --help
```

Ingestion pipeline (France):

```bash
make ingest      # INSEE COG 2025 → temporal model → PostGIS (no geometry)
make load-fr     # + IGN Admin Express 2018/2019/2026 geometries, full France → PostGIS
make join-01     # dept 01 GeoJSON extract (test fixture) → data/out/
make verify-01   # non-regression: Valserhône merger checks
```

Raw data expected under `data/raw/` (gitignored): `insee/commune_YYYY.csv` + `insee/mvtcommune_YYYY.csv`, and IGN Admin Express editions under `ae2018/extract/`, `ae2019/extract/`, `ae2026/commune.parquet` — download links in [`ingestion/README.md`](ingestion/README.md). On the VM, download IGN archives directly from `data.geopf.fr` (datacenter bandwidth), never through the local machine.

## Deploying

The public stack runs on the project VM as three compose services: `db` (PostGIS, localhost-only), `api` (FastAPI/uvicorn, localhost-only), `caddy` (ports 80/443, automatic Let's Encrypt HTTPS). DNS: wildcard `A` record `*.confinia.io` → VM.

```bash
git pull            # or rsync from the workstation
make stack-up       # db + api + caddy
curl -s https://api.confinia.io/healthz
```

Reload data without downtime: run `make load-fr` (the table is rebuilt in one transaction — queries see the old data until commit).

## Data sources & attribution

- **INSEE** — Code Officiel Géographique (communes, movements)
- **IGN — Admin Express** (Licence Ouverte 2.0, attribution « IGN — Admin Express ») — commune geometries
- **Eurostat GISCO** — NUTS regions *(planned)*


## Acknowledgements

The temporal-conversion problem for French communes was first made practical
by [COGugaison](https://github.com/antuki/COGugaison) (Kim Antunez): the
package's treatment of COG vintages, and its population-weighted handling of
communal splits, informed the design of this API's temporal model and of the
upcoming weighted passage tables. Merci.

## License

Code: [Apache-2.0](LICENSE). Data: per-source licenses above.
