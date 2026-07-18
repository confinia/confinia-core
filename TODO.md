# Confinia — Build-track TODO (dev)

*(interne — relire avant tout passage du repo en public, comme `DEV.md`)*

**Purpose:** bootstrap file for fresh Claude Code sessions on the **build track** (code). Work top-down; check items off; log outcomes in `business/STORY.md` after each session. Business/validation tasks live in **`business/TODO.md`** — keep the two tracks separate.

**Layout (since 2026-07-18):** everything lives in **`~/project/confinia/`** — sessions open there. Repo `github.com/confinia/confinia-core` at the root (`ingestion/`, `api/`, `demo/`, `DEV.md`, `TODO.md`, `docker-compose.yml`, `Makefile`); **`business/` and `data/` are gitignored — never commit them** (repo goes public at beta).

**Session preamble (do this first in any new session):** read `business/STORY.md` (latest entries), **`DEV.md` (environment rules — mandatory: everything runs in containers, never host python; since 2026-07-18 the dev environment is the OVH VM via podman — `ssh <vm-ssh>`, project mirror at `~/projects/confinia/`; local macOS edits + rsyncs only)**, and `ingestion/README.md`. Current state (2026-07-18 evening): Steps 0–2 done, Step 3 endpoints written and deployed — full France in PostGIS on the VM (42,372 versions, 3 geometry vintages), API live behind caddy at `api.confinia.io`. Next: Step 4 (MapLibre demo), 2015 count residual (−41), more COG/geometry vintages.

**Fixed decisions (don't re-litigate):** boundaries first (OSM-diff parked); FR first, then DE/NL; temporal model = one row per (code, name) over [valid_from, valid_to), DATE_EFF is the source of truth; API contract fields = code, nom, valid_from, valid_to, parents, children (+ geometry); playground/demo on MapLibre GL JS; stack = PostGIS + FastAPI; **demo deploys to GitHub Pages, only the API runs on the OVH VM** (specs/IP/ssh in `DEV.md`).

---

## Step 0 — Repo under the `confinia` GitHub org ✅ 2026-07-18
- [x] Repo created as **`confinia/confinia-core`** (private; the name `confinia/confinia` is retired on GitHub — freeing it is a business task, see `business/TODO.md`). Apache-2.0 LICENSE from day one
- [x] Moved `src/ingest_cog.py` + `ingest_README.md` → `ingestion/`; layout `ingestion/`, `api/`, `demo/`, `README.md`; local clone at `~/project/confinia`
- [x] Commits authored as `Confinia <contact@confinia.io>` (repo-local git config)

**Done when:** repo exists, script runs from a fresh clone. ✅ verified: fresh clone → demo run, 7 versions, 0 invalid periods.

## Step 1 — IGN Admin Express geometry join ✅ 2026-07-18
- [x] Downloaded 3 vintages from `data.geopf.fr/telechargement/resource/ADMIN-EXPRESS-COG` (catalogue has 2017→2026): **2018** (SHP Lambert-93, 50 MB), **2019** (SHP WGS84, 121 MB), **2026** (GeoParquet WGS84, 445 MB commune layer) → `data/raw/ae{2018,2019,2026}/`; INSEE COG 2025 CSVs → `data/raw/insee/`
- [x] `ingestion/join_geometry.py`: loads SHP (pyshp, auto-reproject Lambert-93→WGS84 via .prj, .cpg encoding) + GeoParquet (pyarrow/WKB); matches by INSEE code **within the version's validity period** (that's what makes code reuse safe); shapely simplify ~50 m, raw + simplified outputs
- [x] Nearest-vintage inheritance flagged `geometry_approx: true` (e.g. "Bellegarde" 1943–1956 inherits 2018 polygon, approx)
- [x] Code-reuse trap verified with real data — plus **two new ingest bugs found & fixed**: (1) movements must be filtered on `TYPECOM == COM` (fusion also emits COMD/COMA rows with the same code+nom that killed the pre-merger version); (2) fusion "identity rows" (same code+nom on AV and AP side, the absorbing commune) must set neither start nor end
- [x] Test `ingestion/verify_ain.py` (repeatable): at 2018-06-01 → Bellegarde/Châtillon/Lancrans; at 2019-06-01 → Valserhône, parents = {01033,01091,01205}; **Valserhône polygon vs union of 3 parents: 0.77% symmetric difference** — all checks pass

**Done when:** ✅ `data/out/communes_01{,_raw}.geojson` (Ain) real polygons, correct at 2018-06-01 vs 2019-06-01.
**Known limit (for Step 2):** one row per (code, nom) means a renamed-then-merged commune has a hole (Bellegarde-sur-Valserine shows 1971→2019; the 1956–1971 span after the rename is folded in only partially); with a single COG millesime loaded, unchanged communes only get `valid_from` = that millesime's Jan 1 — full multi-vintage load in Step 2 fixes counts.

## Step 2 — PostGIS (on the VM) ✅ 2026-07-18
- [x] `docker-compose.yml` with postgis (+ `ingestion/Dockerfile`, `Makefile` targets `db-up`/`ingest`/`load-fr`/`join-01`/`verify-01`/`api-up`/`stack-up`)
- [x] **Model v2** (needed for correct counts): multiple periods per (code, nom) — rétablissements no longer collide (Celles 15148); unknown starts floored to 1943-01-01 (movements are complete since 1943); **MOD-aware event semantics** — création (20) doesn't end the source (Marseille≠†1946), fusion (31/33) doesn't restart the absorber (Manosque≠*1975); date-ranged parents/children per period
- [x] Load full France, all available vintages (2018 SHP, 2019 SHP, 2026 GeoParquet); indexes: GIST on geom + geom_simple, btree (code, valid_from, valid_to) + (valid_from, valid_to); raw + simplified geometry columns
- [x] Sanity counts vs INSEE published: **2015: 36,617/36,658 (−41) · 2020: 34,965/34,968 (−3) · 2025: 34,877/34,875 (+2)**
- [x] ~~Chase the 2015 residual (−41)~~ ✅ 2026-07-18 evening — two more movement-semantics bugs found via diff against COG 2019 snapshot: (1) **identity rows must cancel same-day cross-row starts/ends** (communes nouvelles keeping chef-lieu code+nom — Osmery, Neufchâteau — had their past erased); (2) **same-day start+end with no prior period = zero-length existence, discard** (dept-change + fusion same date: Freigné 44225, Pont-Farcy 50649). Result: **exact match on all three published counts** (36,658 / 34,968 / 34,875) and 0/0 diff vs the full COG 2019 snapshot (34,970)
- [ ] More vintages later (INSEE COG 2019–2024 CSVs, IGN editions 2017/2020–2025) for cross-validation — note: post-2019 INSEE CSVs have lowercase headers + BOM (`commune_2019.csv` staged on the VM already; reader currently expects uppercase)

**Done when:** ✅ `verify_ain.py` passes end-to-end on the VM (Bellegarde→Valserhône at 3 dates, 0.77% geometry gap).

## Step 3 — FastAPI skeleton (the two contract endpoints) *(current step — endpoints written 2026-07-18)*
- [x] `GET /v1/communes?at=YYYY-MM-DD&code=XXXXX` (also `?lat=&lon=` point-in-polygon variant) → commune valid at that date, GeoJSON Feature (`api/main.py`; serves geom_simple, point-in-polygon on raw geom)
- [x] `GET /v1/communes/{code}/history` → all versions + parents/children (`?geometry=true` to include polygons)
- [x] OpenAPI docs auto-exposed (`/docs`); timing middleware (`X-Response-Time-Ms` header) — p95 < 200ms to be measured under load
- [x] **Public deployment (pulled forward from Step 6):** compose services `api` (localhost:8000) + `caddy` (80/443, auto-HTTPS) on the VM; `deploy/Caddyfile`; DNS wildcard `*.confinia.io` → VM
- [ ] No auth yet — API keys/metering is a later step (plan 1.3 of Phase 1 list; before beta)
- [ ] Measure p95 properly under load (spot checks 2026-07-18: ~180 ms end-to-end from a home connection incl. TLS — server time well under 200 ms)

**Done when:** ✅ verified from the public internet 2026-07-18: `01033&at=2018-06-01` → Bellegarde-sur-Valserine; `at=2020-06-01` → Valserhône (parents 01033/01091/01205); `/history` shows Bellegarde 1943→1956→2019→Valserhône; point-in-polygon OK. Apex `confinia.io` cert pending DNS propagation of the new `@` record (caddy retries automatically; LE rate-limit clears 11:11 UTC).

## Step 4 — MapLibre time-slider demo wired to the API *(built 2026-07-18 evening)*
- [x] `demo/index.html`: MapLibre GL JS + monthly date slider 2017→2026; fetches `?dept=XX&at=` FeatureCollection from the API (new endpoint, CORS open, gzip ~170 KB, `Cache-Control 1h`); stable color per INSEE code so mergers are visible; hover card (validity, vintage, approx); autoplay ▶ for GIF capture; dept switcher (whole France loaded)
- [x] The money shot verified in data: dept 01 = 407 communes at 2018-06 → 393 at 2019-06, 01033 Bellegarde→Valserhône
- [x] `make demo` serves it (compose service `demo`, port 8080 — **temporary VM preview http://<vm-ip>:8080**; production stays GitHub Pages per fixed decision)
- [ ] Human: record the GIF/screenshot (press ▶, slide across 2019-01-01) → outreach kit
- [x] Published to GitHub Pages ✅ 2026-07-18: public repo `confinia/confinia.github.io` → **https://confinia.github.io** (deploy via `make demo-publish`; core `demo/` stays the source of truth). Custom domain ✅ 2026-07-18: **https://time-slider.confinia.io live as a caddy-managed 302** → confinia.github.io (wildcard DNS lands on the VM, caddy holds the cert — no DNS change needed; 302 keeps it reversible). Native GitHub-Pages custom domain (DNS CNAME + Pages cname) remains an option later
- [x] Front-end v2 ✅ 2026-07-18: zoom controls moved bottom-right (were hidden under the header), scroll zoom + maxZoom 15; **click anywhere switches to the clicked département** (point-in-polygon via the API; out-of-France click → polite "France only" flash); **explicit date picker** (`type=month` input synced with the slider, year tick marks, French long-date label, note that dates are civil validity dates — no timezone ambiguity)
- [x] Front-end v4 ✅ 2026-07-18: **commune name labels** from zoom 8.5 (big communes win label collisions via symbol-sort-key by area); active département name in the header; zoom control raised above the footer; **Europe backdrop from our own NUTS level-0 data** (60 KB gzipped) with country names; `departement_geom` rebuilt from **raw** geometry union then simplified once — the union of independently simplified polygons had sliver artifacts making a 13 MB payload (now 460 KB gzipped)
- [x] Front-end v3 ✅ 2026-07-18: **all-France département layer** — silhouettes + boundaries + named labels ("01 Ain"…) under the commune layer, so neighbouring départements are visible click targets. Data: new `departement_geom` materialized view (union of current communes per dept, built at `load-fr` time) served by `GET /v1/departements` (24h cache); labels via demotiles glyphs; names hardcoded client-side (they're presentation, not data)

**Done when:** the slider demo runs end-to-end against the API. ✅ (visual check + GIF = human task)

## Step 5 — Second country + NUTS (starts the "EU" in the pitch) *(NUTS done 2026-07-18)*
- [x] Eurostat GISCO NUTS ingestion ✅ — `ingestion/ingest_nuts.py`, 7 versions (2003→2024, official in-force dates as transitions), consecutive unchanged versions merged into periods (3,771 rows), hierarchical parents; `make load-nuts` (auto-download from GISCO on the VM). API: `GET /v1/nuts?level=&country=&at=` + `?code=` + `/v1/nuts/{code}/history`. Sanity at 2022: FR = 14/27/101 (nuts1/2/3) ✓. **Attribution © EuroGeographics required (Step 6 page).** v1 limits: children empty, cross-version correspondences (splits/renames) later via Eurostat correspondence tables
- [ ] Country #2 implementation once the business track picks DE or NL (decision lives in `business/TODO.md`); source: national portal (DE: BKG VG250 — license **DL-DE/BY-2.0**, commercial OK, exact attribution string + "modified" note required; NL: CBS/Kadaster — generally **CC BY 4.0**, verify per product). "Verify licenses" = final read of the exact product terms for the attribution page, not a data-access problem
- [x] Generalize schema ✅ — `unit_type` (commune | nuts0..nuts3 | gemeinde…), `country` columns + (unit_type, country) index; commune endpoints filter `unit_type='commune'` (NUTS polygons must never answer commune point-in-polygon); table name `commune_version` kept for now, rename to `admin_unit_version` at pre-beta hardening

## Step 5b — Observability (Grafana + OpenTelemetry) ✅ 2026-07-18
- [x] OTel metrics in the API (FastAPI + psycopg2 instrumentation; counter `confinia.requests` by route/method/status/country; `http.server.duration` histogram) — observability never breaks the API (fail-open)
- [x] OTel Collector → Prometheus → **Grafana** compose services; provisioned datasource + "Confinia API" dashboard (req/s by route, p50/p95, statuses, countries, top routes). **https://grafana.confinia.io** (admin password in `deploy/secrets.env`, gitignored; sign-up disabled). Legacy monitoring containers + images purged (volumes kept, prune later)
- [x] Callers by country: DB-IP Country Lite (CC BY 4.0 — **add attribution at Step 6**) in `data/geoip/`; only the country code is recorded, never the IP. **Gotcha fixed: rootlessport rewrites source IPs → caddy moved to host network** (real client IPs; backends joined via localhost ports)
- [x] p95 measured (Step 3 leftover): server-side p50 <10 ms, worst of 20 = 38 ms — well under the 200 ms contract
- [ ] Traces exporter (Tempo) later if needed; caddy JSON access logs as second source; per-API-key counters join at Step 6 (metering, plan 2.3); refresh the GeoIP mmdb monthly (cron or ansible-style task)

## Step 6 — Pre-beta hardening (before inviting anyone)
- [ ] API keys + per-key request counting (plan 1.3: metering from day one)
- [ ] Deploy on EU host (Scaleway/Hetzner/OVH — personal account); domain `api.confinia.io`; HTTPS
- [ ] Minimal docs page at confinia.io: pitch line (from `business/PITCH.md`), quickstart curl, playground link
- [ ] Attribution/licences page: IGN Licence Ouverte, INSEE, Eurostat, (OSM ODbL when/if used) — **non-negotiable before anything is public** (see OSM etiquette in `business/INTERVIEWS.md`)
- [ ] Sanitize `DEV.md` + `TODO.md` (internal notes, VM IP/ssh) before the repo goes public

## Later / parked
- OSM change-tracking product (osm2pgsql #2144 evidence) — post-GO
- Historical geocoding ("address → commune as of date X")
- SDK wrappers (Python/JS), Show HN — plan Month 4
