# Geo-schema migrations, applied at deploy

`ingestion/ingest_cog.py` creates the geo schema, and it runs **at ingestion**.
So an index added there reaches a database only when that database is next
rebuilt — which for a live colour is never.

That gap bit twice in one week:

- 2026-08-18, the Creem webhook hit `ON CONFLICT (email, tier)` against a unique
  constraint that exists in no environment, because `CREATE TABLE IF NOT EXISTS`
  never adds a constraint to a pre-existing table;
- 2026-08-21, `idx_cv_country_type_code_vf` was merged and applied to nothing.
  The active colour kept a plan that read 53 076 pages to return 2 rows, fast
  only while its cache stayed hot.

Both are [#115](https://github.com/confinia/confinia-core/issues/115)'s
"migrations as a first-class step".

## The rules

- **Idempotent, always.** Every file must survive being applied twice.
- **Additive only.** A deploy applies these to a colour that may become
  production minutes later, and to one that may be rolled back to. Nothing here
  may drop or rewrite data.
- **`CONCURRENTLY` for indexes on a live colour**, so a build never blocks
  readers. Note it cannot run inside a transaction block.
- The API must **never** run these: `PG_DSN` is read-only at runtime, and the
  staging isolation guard rests on that. Migrations are the deploy's job.
