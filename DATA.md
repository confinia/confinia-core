# DATA.md — what is loaded, from where, and how to refresh it

Three files answer three different questions. Use the right one:

| Question | File |
|---|---|
| **Where does a fact come from, and may we use it?** | [SOURCES.md](SOURCES.md) — provenance, licence, attribution |
| **How is the French temporal model built?** | [ingestion/README.md](ingestion/README.md) — the INSEE model, the schema, the two target queries |
| **How do I load or refresh a dataset?** | **this file** |

The machine-readable registry is `ingestion/sources.sql`, and it is what the
`/v1/attributions` endpoint serves. **A loader that writes rows for a source
absent from that registry fails on a foreign key** — deliberately, so no fact can
ever be served without provenance.

---

## The rule that governs every load

**The geo database is a build artefact, never a copy.** Each colour (blue/green)
has its own geo database, and both are populated by replaying the *same*
versioned scripts against the *same* raw files. Nothing is ever copied between
colours, and nothing is replicated.

This is not tidiness. A logical corruption — a bad parse, a wrong date — would
replicate instantly through any copy or replication. Replaying scripts means a
corrupt database is one colour's problem, and a divergence in row counts between
colours is a script bug, visible immediately.

So **every refresh below runs twice**: once on the passive colour, then, after
validation and promotion, once on the new passive colour.

```sh
cat ~/confinia-edge-state/ACTIVE_COLOR      # green -> blue is passive, port 5441
                                            # blue  -> green is passive, port 5442
```

Ports: **blue = 5441, green = 5442**, ops (never rebuilt) = 5440.

---

## The refresh cycle

```
 1. download the raw file into data/raw/<source>/     (VM-side; the files are large)
 2. run the loader against the PASSIVE colour
 3. sanity-check row counts against the previous edition
 4. ./deploy/deploy-api.sh stage      -> staging serves the passive colour
 5. the founder validates on staging.confinia.io       (RULES 13)
 6. promote-production                                 (manual, reviewer required)
 7. run the SAME loader against the new passive colour  <- do not skip
```

**Step 7 is the one that gets forgotten**, and forgetting it is invisible until
the next promotion serves a colour that never got the data. After every data
promotion, check that both colours agree:

```sh
for p in 5441 5442; do
  psql "postgresql://confinia@127.0.0.1:$p/confinia" -tAc \
    "select country, source, count(*) from commune_version group by 1,2 order by 1,2"
done | sort | uniq -c | grep -v '^ *2 ' || echo "both colours agree"
```

Anything printed is a colour that is missing data.

---

## What is loaded today

| Dataset | Loader | Raw input | Refresh when |
|---|---|---|---|
| **FR communes, temporal** | `ingest_cog.py` | INSEE COG, `data/raw/insee` | a new COG vintage (yearly, ~April) |
| **FR geometry** | `join_geometry.py` | IGN Admin Express | a new IGN edition |
| **FR EPCI** | `ingest_epci.py` | BANATIC | yearly |
| **FR supra-communal history** | `ingest_trf*.py` | INSEE historical tables | rarely; historical |
| **FR historical population** | `ingest_pop.py` | INSEE workbook, 1876-2023 | a new census edition (yearly) |
| **IT comune lineage** | `ingest_istat.py` | ISTAT *Elenco comuni soppressi* | yearly, or after any *fusione* |
| **DE Gemeinden** | `ingest_de.py` | BKG VG250, 2016-2025 | yearly |
| **NL gemeenten** | `ingest_nl.py` | CBS/PDOK WFS per year | yearly (herindelingen take effect 1 January) |
| **UK** | `ingest_ons.py` + `reconcile_uk.sql` | ONS | rarely |
| **NZ** | `ingest_nz.py` | Stats NZ | rarely |
| **EU LAU** | `ingest_lau.py` | Eurostat LAU, 2016-2023 | yearly |
| **NUTS** | `ingest_nuts.py` | Eurostat GISCO, 7 versions | on a NUTS revision (every 3 years) |

`make` verbs exist for the common ones: `ingest`, `load-fr`, `load-nuts`,
`load-de`, `load-nl`, `load-lau`.

**Two shapes of loader, and the difference matters.** `ingest_snapshots.py`
derives events by *diffing annual snapshots* — it can only see changes that fall
on a snapshot boundary, and it infers the reason. A national temporal source
(INSEE COG, ISTAT) *states* the event, its date and its successors. Where both
exist, the national source wins, and the snapshot only supplies geometry.

---

## Italy in particular (issue #91)

Italy arrived as Eurostat LAU: annual snapshots 2016-2023, **zero events**. A
dead code returned nothing.

`ingest_istat.py` adds the lineage from ISTAT's *Elenco dei comuni soppressi* —
2 634 suppressions spanning **1865-2024**, 2 527 of them naming a successor.

```sh
ssh debian
cd ~/projects/confinia
python3 -c "import urllib.request as u; \
  r=u.Request('https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-soppressi.zip', \
  headers={'User-Agent':'confinia-research/1.0'}); \
  open('data/raw/istat/soppressi.zip','wb').write(u.urlopen(r,timeout=180).read())"

DSN="postgresql://confinia:...@127.0.0.1:5441/confinia"     # the PASSIVE colour
podman exec -i confinia-blue_db_1 psql -U confinia -d confinia -q < ingestion/sources.sql
podman run --rm --network host -v "$PWD/ingestion:/app:ro" -v "$PWD/data:/data:ro" -w /app \
  localhost/confinia-api:latest \
  python3 /app/ingest_istat.py --zip /data/raw/istat/soppressi.zip --dsn "$DSN" --dry-run
```

`--dry-run` runs the whole thing inside a transaction and rolls back. **Use it
first, every time**, and read the counts before dropping the flag.

Expected on the 2024 edition, against LAU 2016-2023:

```
2634 suppressions, 1865-08-13 -> 2024-01-22, 2527 with a successor, 333 scorporo
closed 119 · created 2128 historical-only · parents set on 46
```

- **closed 119** — comuni that died inside the snapshot window; their validity
  now ends on the real date instead of running forever.
- **created 2128** — comuni that died before 2016. They get a code, a name and a
  successor, and **no geometry**. That is the honest result: we can route the
  dead code, and we must not draw a boundary we do not have.
- **parents set on 46** — only where the successor's own record *starts* at the
  event date. Absorbing a comune is not being born, and claiming otherwise would
  invent a creation event.

---

## Rules for adding a source

1. **Register it in `ingestion/sources.sql` first.** The foreign key will stop
   you otherwise, which is the intended order.
2. **Check the licence before writing any code** — the filter is in
   [SOURCES.md](SOURCES.md). Commercial use must be allowed; share-alike is a
   decision, not a detail.
3. **Refuse bad input loudly.** `ingest_pop.py` exits if the workbook decodes
   with U+FFFD; `ingest_istat.py` exits if the CSV is not CP1252. A loader that
   half-works produces data nobody can trust and nobody can find.
4. **Make it idempotent.** Every loader must be safe to replay: that is what
   makes double ingestion possible at all.
5. **Say what the data cannot say.** INSEE population figures are *harmonised*
   onto one reference geography, so the loader stores `harmonised_on` and the
   report prints it. ISTAT rows with no day fall back to 1 January, which a
   reader can detect. Per-fact provenance is the product.
