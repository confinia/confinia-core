"""Three places define commune_population, and they must not drift apart.

  ingestion/ingest_pop.py   builds it on a fresh ingestion
  tests/fixture.sql         builds it for CI
  deploy/migrations/*.sql   alters the ones that already exist

Adding `geography_basis` to only the migration produced a 500 on every report
in CI: the API selected a column the fixture had never created. The same shape
as the Creem `ON CONFLICT` that hit a constraint present in no environment, and
as the index that was merged and applied to nothing -- three faces of #115's
"migrations as a first-class step".

So the guard is not "remember to update three files". It is: every column the
API reads must exist everywhere the table is built.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*p):
    return open(os.path.join(ROOT, *p), encoding="utf-8").read()


API = _read("api", "main.py")
FIXTURE = _read("tests", "fixture.sql")
INGEST = _read("ingestion", "ingest_pop.py")


def _columns_selected_from(table: str) -> set:
    """Columns the API names in a SELECT ... FROM <table>."""
    cols = set()
    for m in re.finditer(r'"SELECT ([^"]+)"\s*\n?\s*"?\s*FROM ' + table, API):
        for part in m.group(1).split(","):
            part = part.strip().strip('"').split()[0]
            if re.fullmatch(r"[a-z_]+", part):
                cols.add(part)
    return cols


def test_every_population_column_the_api_reads_exists_where_the_table_is_built():
    cols = _columns_selected_from("commune_population")
    assert cols, "the probe found no SELECT; fix the probe, not the assertion"
    for col in cols:
        assert col in FIXTURE, f"{col} missing from tests/fixture.sql — CI will 500"
        assert col in INGEST, f"{col} missing from ingest_pop.py — a fresh DB will 500"


def test_the_migration_covers_what_the_others_declare():
    """An existing database gets the column from a migration, or not at all."""
    migs = ""
    d = os.path.join(ROOT, "deploy", "migrations")
    for f in sorted(os.listdir(d)):
        if f.endswith(".sql"):
            migs += open(os.path.join(d, f), encoding="utf-8").read()
    assert "geography_basis" in migs, \
        "a column added to the schema must also reach databases that already exist"
