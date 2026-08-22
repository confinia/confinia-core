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
    assert "\n\tmigrate\n" in SH, "and actually calls it"


def test_they_reach_both_colours_not_only_the_staged_one():
    """caddy keeps the other colour as a health-checked fallback. Migrating only
    the staged one leaves that fallback unable to serve the code about to go
    live: a failover would answer 500 on every report, from a fallback everyone
    believes is there. A broken fallback is worse than none, because it is
    trusted.

    Found 2026-08-21 with geography_basis: the pipeline gave it to the passive
    colour and the active one needed it applied by hand.
    """
    fn = SH.split("migrate() {")[1].split("\nwarm()")[0]
    assert "for c in blue green" in fn, "both colours"
    assert '"$1"' not in fn, "it no longer takes a colour argument"
    assert 'podman container exists' in fn, "a colour that is not there is skipped"


def test_they_run_before_the_colour_is_warmed_or_smoked():
    """Warming a colour whose index is missing measures the wrong thing, and
    the smoke would then fail on a problem the deploy had already fixed."""
    assert SH.index("\n\tmigrate\n") < SH.index('warm "$(port_of "$P")"')


def test_every_migration_is_idempotent():
    assert FILES, "at least one migration ships"
    for f in FILES:
        sql = open(f, encoding="utf-8").read().upper()
        assert "IF NOT EXISTS" in sql, f"{os.path.basename(f)} must survive re-running"


def test_a_backfill_only_fills_what_is_empty():
    """An UPDATE is allowed here -- filling a new column from data already
    present is additive in effect -- but only where the column is unset, so
    re-running cannot overwrite a value someone later corrected."""
    import re as _re
    for f in FILES:
        sql = open(f, encoding="utf-8").read()
        for m in _re.finditer(r"UPDATE\s+[\w.]+(.*?);", sql, _re.S | _re.I):
            body = m.group(1)
            assert "IS NULL" in body.upper(), \
                f"{os.path.basename(f)}: an UPDATE must only fill empty cells"


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
