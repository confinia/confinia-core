# Confinia

**An EU-hosted API for administrative boundaries with full historical versioning** — query any commune, municipality, or NUTS region *as it existed at any date*, as GeoJSON.

> Boundaries change: communes merge, districts are redrawn, codes get reused. Confinia models every administrative unit as versions valid over `[valid_from, valid_to)`, with parent/child links across mergers and splits — so `code=01033&at=2018-06-01` returns Bellegarde-sur-Valserine, and the same code a year later returns Valserhône.

Status: **early development** (private). France first (INSEE COG + IGN Admin Express), then DE/NL and Eurostat NUTS.

## Layout

| Directory | Contents |
|---|---|
| [`ingestion/`](ingestion/) | Source-data ingestion — INSEE COG → temporal model (works today; see its [README](ingestion/README.md)) |
| [`api/`](api/) | FastAPI service — the query endpoints (not started) |
| [`demo/`](demo/) | MapLibre GL JS time-slider playground (not started) |

## Quickstart (ingestion demo, zero dependencies)

```bash
python3 ingestion/ingest_cog.py
```

Runs the built-in demo dataset (real French merger cases) and prints the resulting temporal table. See [`ingestion/README.md`](ingestion/README.md) for real INSEE files and PostGIS loading.

## Data sources & attribution

- **INSEE** — Code Officiel Géographique (communes, movements)
- **IGN — Admin Express** (Licence Ouverte 2.0) — commune geometries *(join in progress)*
- **Eurostat GISCO** — NUTS regions *(planned)*

## License

Code: [Apache-2.0](LICENSE). Data: per-source licenses above.
