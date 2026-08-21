"""A schema change must reach the databases that already exist (issue #115).

`ingestion/ingest_cog.py` owns the geo schema and runs at INGESTION, so anything
added there reaches a live colour never. That gap bit twice in one week:

  - 2026-08-18: a Creem webhook hit `ON CONFLICT (email, tier)` against a unique
    constraint present in no environment, because `CREATE TABLE IF NOT EXISTS`
    never adds a constraint to a pre-existing table;
  - 2026-08-21: `idx_cv_country_type_code_vf` was merged and applied to nothing.
    The active colour kept a plan reading 53 076 pages to return 2 rows, fast
    only while its cache stayed hot -- one eviction from a 71 s endpoint.

So the deploy applies them, additively and idempotently, to the colour it stages.
"""
import glob
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
SH = open(os.path.join(ROOT, "deploy", "deploy-api.sh"), encoding="utf-8").read()
CODE = "\n".join(l for l in SH.splitlines() if not l.lstrip().startswith("#"))
FILES = sorted(glob.glob(os.path.join(ROOT, "deploy", "migrations", "*.sql")))


def test_the_deploy_applies_them():
    assert "migrate() {" in CODE
    assert 'migrate "$P"' in CODE, "and actually calls it"


def test_they_run_before_the_colour_is_warmed_or_smoked():
    """Warming a colour whose index is missing measures the wrong thing, and
    the smoke would then fail on a problem the deploy had already fixed."""
    assert CODE.index('migrate "$P"') < CODE.index('warm "$(port_of "$P")"')


def test_every_migration_is_idempotent():
    assert FILES, "at least one migration ships"
    for f in FILES:
        sql = open(f, encoding="utf-8").read().upper()
        assert "IF NOT EXISTS" in sql, f"{os.path.basename(f)} must survive re-running"


def test_no_migration_destroys_anything():
    """A deploy applies these to a colour that may be promoted minutes later,
    and to one that may be rolled back to."""
    for f in FILES:
        sql = open(f, encoding="utf-8").read().upper()
        for verb in ("DROP TABLE", "DROP COLUMN", "TRUNCATE", "DELETE FROM"):
            assert verb not in sql, f"{os.path.basename(f)} is not additive: {verb}"


def test_indexes_are_built_without_blocking_readers():
    for f in FILES:
        sql = open(f, encoding="utf-8").read().upper()
        if "CREATE INDEX" in sql:
            assert "CONCURRENTLY" in sql, \
                f"{os.path.basename(f)} would lock a live colour"


def test_the_api_never_runs_them():
    """PG_DSN is read-only at runtime, and the staging isolation guard rests on
    exactly that."""
    api = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()
    assert "deploy/migrations" not in api
