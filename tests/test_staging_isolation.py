"""Staging must not write into production's operational data (issue #113).

Until 2026-08-12 "staging" WAS the passive colour, so it was two things at once:
the environment you exercise, and the one production rolls back to. Consequences,
all real rather than theoretical:

  - validating the quota counter consumed a paying customer's allowance;
  - every staging request landed in api_usage, the table customers are billed on;
  - the API creates its tables on boot, so a schema change tried on staging was
    applied to the PRODUCTION ops database the moment the container started.

The separation rests on one verified fact: the API writes only through OPS_DSN.
Every INSERT/UPDATE/DELETE in api/main.py targets an ops table; PG_DSN is
read-only at runtime. If that ever stops being true, sharing the colour's geo
database stops being safe, so it is asserted here.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


OPS_TABLES = {"api_key", "api_usage", "visitor_daily", "premium_seen",
              "premium_usage", "premium_usage_daily", "polar_subscription",
              "upgrade_intent"}


def test_the_api_only_writes_to_operational_tables():
    """The premise of sharing a colour's geo database read-only."""
    src = _read("api", "main.py")
    # `DO UPDATE SET` inside an upsert is not a write to a table called SET.
    writes = re.findall(
        r"(?:INSERT INTO|DELETE FROM)\s+(?:public\.)?(\w+)"
        r"|(?<!DO )UPDATE\s+(?:public\.)?(\w+)\s+SET", src)
    writes = [a or b for a, b in writes]
    unexpected = {t for t in writes if t not in OPS_TABLES}
    assert not unexpected, (
        "these tables are written at runtime but are not operational tables, so "
        f"staging can no longer share a colour's geo database read-only: {unexpected}")


def test_staging_has_its_own_operational_database():
    sh = _read("deploy", "staging-up.sh")
    assert "OPS_DB=confinia_staging" in sh, "staging must not share the production ops db"
    assert "${OPS_DB}" in sh, "the ops DSN must point at that database"


def test_staging_uses_the_sandbox_realm_and_test_billing():
    compose = _read("deploy", "staging-up.sh")
    assert "confinia-sbx" in compose, "a staging signup must not touch a real account"
    assert "POLAR_MODE=sandbox" in compose, "a staging click must not produce a real charge"


def test_staging_is_routed_to_its_own_stack():
    stacks = _read("deploy", "stacks.sh")
    m = re.search(r"\(staging_upstreams\) \{\n\treverse_proxy ([^\n{]+)", stacks)
    assert m, "the staging upstream block changed shape"
    assert "8501" in m.group(1), "staging must reach the dedicated stack first"


def test_staging_does_not_bind_a_burned_or_colour_port():
    sh = _read("deploy", "staging-up.sh")
    for taken in ("8091", "8402", "8092", "8093", "8096", "8098"):
        assert f":{taken}:" not in sh, f"staging must not bind {taken}"
