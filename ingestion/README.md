# INSEE COG ingestion → PostGIS (temporal model)

Ingestion script for INSEE's Code Officiel Géographique into a temporal
`valid_from` / `valid_to` model queryable by date, with parent/child links
rebuilt from the commune movements.

## What the script does

1. Reads, for several vintages, two INSEE files:
   - the **COMMUNE** file (state of the communes on January 1st of the vintage)
   - the **MVTCOMMUNE** file (movements: mergers, creations, renamings…)
2. Rebuilds a temporal table: one row = one `(code, name)` pair valid over a
   `[valid_from, valid_to)` period.
   The **effective date** (`DATE_EFF`) of the movements is the source of truth
   for transitions — not the vintage date.
3. Derives the `parents` / `children` links (where a commune comes from, what replaces it).
4. Loads the result into **PostGIS**, or exports it to **GeoJSON** if no
   database is provided.

## Running it

Demonstration mode (no dependencies, built-in set of real cases):

```bash
python3 ingest_cog.py
```

On real INSEE files downloaded locally:

```bash
python3 ingest_cog.py \
  --millesimes 2015 2020 2025 \
  --data-dir ./insee_files \
  --geojson communes_temporel.geojson
```

Local files must be named `commune_YYYY.csv` and `mvtcommune_YYYY.csv`.

Into PostGIS:

```bash
export PG_DSN="postgresql://user:pwd@localhost/chronocarte"
python3 ingest_cog.py --millesimes 2015 2020 2025 --data-dir ./insee_files
```

## Produced schema

```sql
CREATE TABLE commune_version (
    id               bigserial PRIMARY KEY,
    code             text NOT NULL,          -- INSEE code
    nom              text NOT NULL,          -- name
    valid_from       date NOT NULL,          -- start of validity
    valid_to         date NOT NULL,          -- end (9999-01-01 = still valid)
    parents          text[] DEFAULT '{}',    -- codes this version originates from
    children         text[] DEFAULT '{}',    -- codes that replace it
    geometry_vintage date,                   -- IGN edition used
    geometry_approx  boolean DEFAULT false,  -- inherited from a neighboring edition
    geom             geometry(Geometry, 4326),   -- raw (spatial queries)
    geom_simple      geometry(Geometry, 4326)    -- simplified ~50 m (web)
);
```

Indexes created: `(valid_from, valid_to)`, `(code, valid_from, valid_to)`, and
GiST on `geom` and `geom_simple`. Loading recreates the table (DROP+CREATE)
inside a transaction: queries see the old state until the commit.

## The two target queries (once in the database)

Which commune contains this point at this date:

```sql
SELECT code, nom FROM commune_version
WHERE valid_from <= '2015-06-01' AND valid_to > '2015-06-01'
  AND ST_Contains(geom, ST_SetSRID(ST_Point(5.83, 46.11), 4326));
```

Full history of a code:

```sql
SELECT nom, valid_from, valid_to, parents, children
FROM commune_version WHERE code = '01033' ORDER BY valid_from;
```

## IGN geometry join (`join_geometry.py`)

Joins the **Admin Express COG édition** polygons (IGN, Licence Ouverte 2.0 —
attribution « IGN — Admin Express ») to the temporal model: matching by INSEE
code **within the validity period of each version** (makes code reuse
harmless), inheritance from the closest vintage flagged
`geometry_approx: true`, raw + simplified (~50 m) outputs. SHP sources
(≤ 2024, automatic Lambert-93 reprojection) and GeoParquet (≥ 2025). Catalog
of the 2017→2026 editions: `data.geopf.fr/telechargement/resource/ADMIN-EXPRESS-COG`.

Outputs: `--geojson` / `--geojson-raw` (extracts, fixtures) and/or **`--dsn`**
(streaming PostGIS loading, raw + simplified per version — this is the
production path, `load-fr` target of the `Makefile`). `--dsn` without a value
reads `$PG_DSN`; it is never implicit, so that a departmental extract
(`join-01`) does not overwrite the full-France table.

Non-regression test on the Valserhône merger (dept 01): `verify_ain.py` —
see the `join-01` / `verify-01` targets of the root `Makefile`. Everything runs
in a container (rules in `DEV.md`).

## Movement semantics (learned on real data)

- Filter on `TYPECOM == COM` (mergers also emit COMD/COMA rows).
- Identity row (same code+name AV/AP): the commune passes through the event — neither start nor end.
- Same code, different name: renaming — end + start, whatever the MOD.
- Different code: **the MOD decides** — end of the AV for {30, 31, 32, 33, 41, 50}
  (abolition, mergers on the absorbed side, code changes); start of the AP for
  {20, 21, 32, 41, 50} (creation, re-establishment, commune nouvelle). A
  creation (20) does not kill the source commune; a merger (31/33) does not
  (re)start the absorber.
- A (code, name) can have **several periods** (re-establishments — Celles 15148).
- **The identity row cancels the cross starts/ends of the same day**: a
  commune nouvelle keeping the chef-lieu's code and name (Osmery 18173 in 2024,
  Neufchâteau 88321 in 2025) passes through the event — the cross row coming
  from the absorbed commune must not reset its history.
- **Start + end on the same day with no past = zero-duration existence, ignored**
  (simultaneous department change + merger: Freigné 44225,
  Pont-Farcy 50649 in 2018). End + re-start on the same day with a past =
  continuity (name round-trip).
- Unknown start (no incoming event): bounded at `1943-01-01` — the movements
  file is complete since 1943, which makes the counts correct at any date with
  a single COG vintage loaded. **Verified vs published INSEE figures:
  2015: 36,658 / 2020: 34,968 / 2025: 34,875 — all three exact**, and a null
  diff (0 missing, 0 extra) against the complete COG 2019 snapshot.

## Known limitations (to address next)

- **Geometry**: the COG does not contain the polygons. The IGN Admin Express
  outline of the matching vintage must be joined (Shapefile/GeoPackage).
  The script already accepts a GeoJSON geometry per version; the IGN join
  remains to be wired up.
- **Communes that disappeared before the oldest loaded vintage**: their real
  start date is unknown; the script bounds it at `1943-01-01` (lower bound of
  the COG) and reports it. Loading an older vintage removes the ambiguity.
- **INSEE URLs**: unstable from one vintage to the next, to be filled in the
  `INSEE_SOURCES` dictionary at the top of the file.
