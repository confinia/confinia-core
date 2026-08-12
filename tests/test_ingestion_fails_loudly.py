"""An ingestion that cannot write must fail, not fall back quietly (issue #99).

On 2026-08-12 the green rebuild printed "[!] PostGIS connection failed", wrote a
geojson instead, printed "Done." and exited 0. The pipeline treated that as
success and ran for an hour populating nothing.

This is the same family as the smoke suite that executed zero tests and exited 0,
and the promotion that reported success while serving the previous build: a step
that says "green" without doing the work.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_a_dsn_load_that_fails_exits_nonzero():
    src = _read("ingestion", "ingest_cog.py")
    m = re.search(r"if args\.dsn:(.*?)print\(.\\nDone", src, re.S)
    assert m, "the exit path of ingest_cog.py changed shape; re-read it"
    block = m.group(1)
    assert "sys.exit" in block, \
        "a failed --dsn load must exit non-zero, not fall back to a geojson"


def test_the_geojson_fallback_only_applies_without_a_dsn():
    src = _read("ingestion", "ingest_cog.py")
    m = re.search(r"if args\.dsn:(.*?)print\(.\\nDone", src, re.S)
    assert "else:" in m.group(1), \
        "the geojson output must be the no-dsn branch, not a consolation prize"


def test_the_builder_waits_for_healthy_not_pg_isready():
    # pg_isready answers from the container's initdb server, which then shuts
    # down. An ingestion started in that gap hits a database that looks ready.
    sh = _read("deploy", "build-geo.sh")
    wait = sh[sh.index("waiting for the database"):sh.index("RUN /app/ingest_cog.py")]
    assert "healthy" in wait, "build-geo.sh must wait for the healthcheck"
    assert "select 1" in wait, "and prove it with a real query"
    directives = [l for l in wait.splitlines() if not l.strip().startswith("#")]
    assert not any("pg_isready" in l for l in directives), \
        "pg_isready is exactly the check that races with initdb"
