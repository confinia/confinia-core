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
    # 11320 since the legacy drop; it was 8403. The assertion is that staging
    # reaches its OWN stack FIRST -- the fallback to the passive colour answers
    # /healthz identically, so a wrong first upstream looks perfectly healthy.
    first = m.group(1).split()[0]
    assert first.endswith(":11320"), \
        f"staging must reach the dedicated stack first, not {first}"


def test_staging_does_not_bind_a_burned_or_colour_port():
    sh = _read("deploy", "staging-up.sh")
    # Burned ports, the colours' own ports, and 85xx which belongs to panoramax.
    for taken in ("8091", "8402", "8092", "8093", "8096", "8098", "8501", "8502"):
        assert f":{taken}:" not in sh, f"staging must not bind {taken}"


def test_the_operational_database_is_not_published_on_the_host():
    """It holds customer accounts, API keys and billing state.

    It was bound on 0.0.0.0:5440 until 2026-08-12, private only because ufw
    denies incoming — one firewall rule away from the internet, on a VM shared
    with five other products. Rebinding to 127.0.0.1 would have broken every
    environment: containers reach it through host.containers.internal, which is
    not the host's loopback. So it is reached by container name instead, with
    nothing published.
    """
    compose = _read("docker-compose.yml")
    directives = [l for l in compose.splitlines() if not l.strip().startswith("#")]
    for line in directives:
        assert "5440" not in line, \
            f"the operational database must not be published on the host: {line.strip()}"


def test_the_ops_database_joins_the_colour_networks_declaratively():
    """`podman network connect` does not survive a container recreate.

    On 2026-08-12 I connected the running ops database by hand, then recreated
    it to drop its published port. The new container had only the default
    network, and every quota check, API-key lookup and billing read failed on
    production, staging and sandbox at once — while /healthz stayed green,
    because it reads only the geo database. A health check that cannot see the
    outage is the recurring shape of every incident in this repo.
    """
    compose = _read("docker-compose.yml")
    ops = compose.split("ops-db:")[1].split("\n  keycloak:")[0]
    assert "networks:" in ops, \
        "the ops database must declare its networks, not rely on a manual connect"
    for colour in ("blue", "green"):
        assert f"confinia-{colour}_default" in compose, \
            f"the {colour} colour network must be declared so the ops db joins it"


def test_staging_runs_under_systemd_not_as_a_job_child():
    """A container created by a CI job dies with the job (issue #123).

    Its rootless port forwarder goes first, and the symptom is precise and
    misleading: `podman ps` keeps showing `127.0.0.1:11320->8000/tcp` while `ss`
    shows nothing listening. The environment looks deployed and answers nothing
    -- which is how the founder got a 17-hour-old staging build three times.

    The unit is GENERATED, not committed: staging follows the passive colour,
    and that changes at every promotion. A static unit would pin yesterday's
    network and read the wrong colour's database.
    """
    sh = _read("deploy", "staging-up.sh")
    assert "systemctl --user restart confinia-staging-api" in sh, \
        "staging must be owned by systemd, not by whatever ran the script"
    assert "containers/systemd" in sh and "[Container]" in sh, \
        "the unit must be generated next to the other Quadlet units"
    assert "Network=$NET" in sh, \
        "the generated unit must follow the passive colour, not pin one"
    body = "\n".join(l for l in sh.splitlines() if not l.lstrip().startswith("#"))
    assert "podman run -d" not in body, \
        "a bare podman run reintroduces the job-child failure"


def test_no_secret_is_written_as_a_unit_Environment_line():
    """Environment= becomes `podman run --env ...` on the service command line.

    `systemctl status` prints that line and the journal keeps it, so a DSN
    written this way puts the database password in front of anything that can
    read the journal. It leaked the freshly rotated password on 2026-08-17,
    into a terminal, seconds after the rotation.
    """
    sh = _read("deploy", "staging-up.sh")
    unit = sh.split("[Container]")[1].split("UNIT")[0] if "[Container]" in sh else sh
    for line in unit.splitlines():
        if line.startswith("Environment="):
            assert not any(k in line for k in ("DSN", "PASSWORD", "SECRET", "TOKEN")), \
                f"secret on the service command line: {line[:60]}"
    assert "chmod 600" in sh, "the generated env file must not be world-readable"
    assert "EnvironmentFile=%h/.config/containers/systemd/confinia-staging.env" in sh
