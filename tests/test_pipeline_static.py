"""Deployment pipeline invariants (issue #109).

Nothing is deployed by hand any more. These assertions guard the properties that
make that safe, because each of them fails silently if someone loosens it.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
WF = os.path.join(ROOT, ".github", "workflows")


def _wf(name):
    return open(os.path.join(WF, name), encoding="utf-8").read()


def test_production_promotion_is_manual_and_gated():
    s = _wf("promote-production.yml")
    assert "workflow_dispatch:" in s, "promotion must be manual"
    assert "push:" not in s, "a merge to main must never promote by itself"
    assert "environment: production" in s, \
        "the production environment carries the required reviewer"


def test_production_rolls_back_when_the_smoke_fails():
    s = _wf("promote-production.yml")
    assert "deploy-api.sh rollback" in s, \
        "a failed smoke on production must roll back, not just report"


def test_staging_smokes_something_other_than_production():
    s = _wf("deploy-staging.yml")
    assert "smoke_prod.py" in s
    assert "TEST_API_BASE=https://api.confinia.io" not in s, \
        "a staging deployment must not smoke the production API"


def test_the_smoke_is_run_by_pytest():
    # smoke_prod.py is a pytest module: it has no __main__. Running it with
    # `python3 smoke_prod.py` executes ZERO tests and exits 0, so a deployment
    # reports green having checked nothing. That is how the first draft of
    # these workflows was written.
    for f in ("deploy-staging.yml", "promote-production.yml"):
        s = _wf(f)
        assert re.search(r"pytest\W+-q\b[^\n]*\btests/smoke_prod\.py", s), \
            f"{f} must invoke the smoke through pytest, not as a script"
        assert not re.search(r"python3?\s+\S*smoke_prod\.py", s), \
            f"{f} runs smoke_prod.py as a script: that executes zero tests"


def test_deployments_run_on_the_vm_runner():
    for f in ("deploy-staging.yml", "promote-production.yml"):
        assert "self-hosted" in _wf(f), \
            f"{f} must run on the VM runner, so no credential lives in GitHub"


def test_deployments_are_serialised():
    for f in ("deploy-staging.yml", "promote-production.yml"):
        s = _wf(f)
        assert "concurrency:" in s and "cancel-in-progress: false" in s, \
            f"{f} must never interrupt a deployment mid-flight"


def test_the_mirror_is_reset_not_rsynced():
    s = _wf("deploy-staging.yml")
    assert "git reset --hard" in s
    assert "rsync" not in s, "hand-driven rsync to the VM is what this replaces"


def test_the_smoke_runs_in_a_container():
    # DEV.md: everything runs in a container, never host python. The first
    # version built a venv on the runner and failed -- python3-venv is absent,
    # and installing it would need sudo, the privilege issue #114 is about.
    for f in ("deploy-staging.yml", "promote-production.yml"):
        s = _wf(f)
        assert "podman run" in s and "python:3.12-slim" in s, \
            f"{f} must run the smoke in a container"
        assert "python3 -m venv" not in s, \
            f"{f} must not build a venv on the runner"


def test_promotion_checks_the_promoted_colour_directly():
    # The public smoke cannot see a dead promoted colour: caddy falls back to
    # the other one and every public check passes against the OLD build. That
    # is what happened on 2026-08-03 (issue #123).
    s = _wf("promote-production.yml")
    assert "ACTIVE_COLOR" in s and "127.0.0.1:$port" in s, \
        "promotion must check the promoted colour on its own port, not only the public URL"


def test_every_job_has_a_timeout():
    # On 2026-08-11 two jobs hung on GitHub-hosted runners: keycloak for 20 min
    # (normally 1m20s) and e2e for 30+ min (normally 50s). With no timeout a hung
    # job blocks the PR indefinitely, burns runner minutes, and cannot even be
    # re-run -- GitHub refuses to retry a run it considers still in flight.
    # A timeout turns "hangs forever" into "fails in N minutes", which is the
    # difference between a blocked afternoon and a retry.
    import glob
    for path in sorted(glob.glob(os.path.join(WF, "*.yml"))):
        s = open(path, encoding="utf-8").read()
        jobs = re.findall(r"^  ([a-z][a-z0-9_-]*):\n(?:    .*\n)*?    runs-on:", s, re.M)
        for job in jobs:
            block = re.search(rf"^  {re.escape(job)}:\n((?:    .*\n|\n)*?)(?=^  \S|\Z)", s, re.M)
            assert block and "timeout-minutes:" in block.group(1), \
                f"{os.path.basename(path)}: job '{job}' has no timeout-minutes"


def test_stage_checks_the_port_before_destroying_a_container():
    # On 2026-08-11 `stage` removed a healthy green API and then could not
    # recreate it: another tenant held the port. The check must come BEFORE the
    # rm, or the guard is worthless.
    sh = open(os.path.join(ROOT, "deploy", "deploy-api.sh"), encoding="utf-8").read()
    assert "port_is_ours" in sh, "deploy-api.sh must verify the port before recreating"
    guard = sh.index("port_is_ours \"$(port_of")
    rm = sh.index('podman rm -f "confinia-${P}_api_1"')
    assert guard < rm, "the port check must run BEFORE the container is destroyed"


def test_the_burned_ports_are_not_bound_anywhere():
    # 8092/8093 are squatted by other tenants (PORTS.md, BURNED table). Binding
    # them again reproduces the outage.
    targets = ["deploy/stack/docker-compose-green.yml", "deploy/stacks.sh",
               "deploy/deploy-api.sh", "docker-compose.yml"]
    for rel in targets:
        for line in open(os.path.join(ROOT, rel), encoding="utf-8").read().splitlines():
            if line.strip().startswith("#"):
                continue
            for burned in ("8092", "8093", "8096", "8098"):
                assert burned not in line, f"{rel}: binds burned port {burned}: {line.strip()}"


def test_the_deploy_dir_is_the_confinia_home():
    # The stack moved to the confinia user on 2026-08-11 (#99). A workflow still
    # pointing at /home/debian operates on a home that no longer owns anything,
    # and its guard then refuses because the ports belong to another user --
    # which is exactly how deploy-staging broke on 6869992.
    for f in ("deploy-staging.yml", "promote-production.yml"):
        s = _wf(f)
        assert "/home/confinia/projects/confinia" in s, f"{f}: wrong DEPLOY_DIR"
        assert "/home/debian/projects" not in s, f"{f}: still points at the old home"


def test_the_runner_privilege_is_asserted_at_deploy_time():
    # A comment cannot enforce this; the job must check it every run.
    s = _wf("deploy-staging.yml")
    assert "sudo -n true" in s, \
        "deploy-staging must assert the runner cannot become root (issue #114)"


def test_no_file_names_another_products_port():
    """A wrong port in a comment is how the wrong port gets bound.

    That is not hypothetical: maplibre took 8092 from our band, and I took 8501
    from panoramax's — both after checking a port was *free* rather than *ours*.
    A comment asserting "the VM default is 8087" (mapmax's) is the same trap one
    step earlier.
    """
    import glob
    foreign = {"8087": "mapmax", "8090": "overwatch", "8096": "overwatch",
               "8092": "maplibre", "8093": "maplibre",
               "8501": "panoramax", "8502": "panoramax", "8503": "panoramax"}
    checked = ([os.path.join(WF, f) for f in ("deploy-staging.yml", "promote-production.yml",
                                              "subscription-tests.yml")]
               + glob.glob(os.path.join(ROOT, "deploy", "*.sh")))
    for path in checked:
        lines = open(path, encoding="utf-8").read().splitlines()
        for i, line in enumerate(lines, 1):
            for port, owner in foreign.items():
                if port not in line:
                    continue
                # An explanation can span several lines, so look at the window
                # around the mention rather than the single line.
                window = " ".join(lines[max(0, i - 4):i + 3])
                assert any(w in window for w in
                           (owner, "BURNED", "burned", "belongs", "never", "NOT", "self-assigned")), \
                    f"{os.path.basename(path)}:{i} names {port} ({owner}'s) with no explanation: {line.strip()[:70]}"


def test_the_deploy_prefers_a_systemd_unit():
    """A container created by a CI job is a child of that job (issue #123).

    About two minutes after the job ended, the passive colour was killed with
    `exit=-1` — neither a crash nor a clean stop — and staging stayed dead. The
    identical command over ssh survived nine hours. Quadlet makes the container
    belong to systemd, so a deployment only ever restarts it.
    """
    sh = open(os.path.join(ROOT, "deploy", "deploy-api.sh"), encoding="utf-8").read()
    assert "systemctl --user restart" in sh, \
        "the deploy must restart a systemd unit, not create a container as its own child"
    i_unit = sh.index("systemctl --user restart")
    i_compose = sh.index("podman-compose -p \"confinia-$P\"")
    assert i_unit < i_compose, "the systemd path must be tried FIRST, compose only as fallback"


def test_every_systemd_unit_publishes_its_1pesi_port():
    """A unit publishes what it declares, and it beats compose.

    A green unit carrying only 8402 was already installed on the VM when the
    11xxx band landed. Since deploy-api.sh prefers the unit, green would never
    have reached 11220 however many times it was recreated -- the declaration in
    docker-compose-green.yml was simply not the one being used.

    Blue keeps its legacy 8091 alongside 11120 until it is recreated, which
    waits on a production promotion; that is the one documented exception.
    """
    import glob
    for path in sorted(glob.glob(os.path.join(ROOT, "deploy", "quadlet", "*.container"))):
        s = open(path, encoding="utf-8").read()
        published = set(re.findall(r"^PublishPort=127\.0\.0\.1:(\d+):", s, re.M))
        assert any(p.startswith("11") for p in published), (
            f"{os.path.basename(path)} publishes {sorted(published) or 'nothing'} and no "
            "11xxx port; the unit wins over compose, so the colour would never reach the band")
        legacy = {p for p in published if not p.startswith("11")}
        if legacy:
            assert "blue" in os.path.basename(path), (
                f"{os.path.basename(path)} still publishes legacy {sorted(legacy)}; only blue "
                "keeps its legacy port, until the promotion lets it be recreated")


def test_no_host_network_container_depends_on_a_container_name():
    """`--network host` and a container-name DSN cannot both be true.

    A container name resolves only inside a podman network. On the host network
    there is no such resolver, so the reference is unreachable — and since the
    ops database stopped publishing a host port on 2026-08-12 (platform audit),
    there is no fallback either.

    The sandbox shipped exactly that pair. It was stopped when the publish was
    removed, so nothing failed until it was next started: uvicorn printed
    "Waiting for application startup" and hung there forever, with no error and
    no non-zero exit. A hang is the worst shape this can take, because every
    "is it running?" check says yes.
    """
    import glob
    for path in sorted(glob.glob(os.path.join(ROOT, "deploy", "*.sh"))):
        s = open(path, encoding="utf-8").read()
        body = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
        if "--network host" not in body:
            continue
        for name in ("confinia_ops-db_1", "confinia-blue_db_1", "confinia-green_db_1"):
            assert name not in body, (
                f"{os.path.basename(path)} runs on the host network and references "
                f"{name}, which only resolves inside a podman network")


def test_every_edge_upstream_points_at_a_port_we_actually_publish():
    """Dropping a publish without repointing the edge fails silently.

    Step 4 removed grafana's 8086 while the Caddyfile still proxied /grafana
    there: the dashboard went 502. Worse, it removed green's 8402 and staging's
    8403 while stacks.sh still generated upstreams naming them -- and a dead
    upstream does not fail, caddy simply falls back to the other one. Production
    stayed up with no safety net, and staging routed to the passive colour
    instead of the staging stack.

    So: every loopback port the edge proxies to must be published somewhere in
    this repo. A port that appears only in an upstream is one nothing serves.
    """
    import glob
    published = set()
    for rel in (glob.glob(os.path.join(ROOT, "deploy", "stack", "*.yml"))
                + glob.glob(os.path.join(ROOT, "deploy", "quadlet", "*.container"))
                + glob.glob(os.path.join(ROOT, "deploy", "*.sh"))
                + [os.path.join(ROOT, "docker-compose.yml")]):
        s = open(rel, encoding="utf-8").read()
        published |= set(re.findall(r"127\.0\.0\.1:(\d+):", s))          # compose / podman -p
        published |= set(re.findall(r"PublishPort=127\.0\.0\.1:(\d+):", s))
        published |= set(re.findall(r"^(?:NEW_)?PORT=(\d+)", s, re.M))   # scripts

    upstreams = set()
    for rel in ["deploy/caddy/Caddyfile", "deploy/stacks.sh"]:
        s = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        body = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
        upstreams |= set(re.findall(r"reverse_proxy (?:127\.0\.0\.1:(\d+))", body))
        upstreams |= set(re.findall(r"(?:ACT|PAS)=(\d+)", body))

    # ecobuilding is another product behind the same edge; it publishes elsewhere.
    orphans = {p for p in upstreams - published if p != "8020"}
    assert not orphans, (
        f"the edge proxies to {sorted(orphans)}, which nothing in this repo publishes; "
        "a dead upstream does not error, it silently falls back")


def test_the_colour_ports_the_deploy_waits_on_are_published():
    """A stale colour port makes every deployment time out, not misroute.

    Step 4 dropped green's 8402 and left `port_of` returning it. Nothing was
    misrouted -- the deployment simply polled a port nobody serves for 120
    seconds and failed, on a merge whose contents were fine.
    """
    import glob
    published = set()
    for rel in (glob.glob(os.path.join(ROOT, "deploy", "stack", "*.yml"))
                + glob.glob(os.path.join(ROOT, "deploy", "quadlet", "*.container"))):
        s = open(rel, encoding="utf-8").read()
        published |= set(re.findall(r"127\.0\.0\.1:(\d+):", s))
        published |= set(re.findall(r"PublishPort=127\.0\.0\.1:(\d+):", s))

    sh = open(os.path.join(ROOT, "deploy", "deploy-api.sh"), encoding="utf-8").read()
    m = re.search(r"port_of\(\) \{[^}]*echo (\d+)[^}]*echo (\d+)", sh)
    assert m, "port_of changed shape"
    for port in m.groups():
        assert port in published, \
            f"deploy-api.sh waits on {port}, which no colour stack or unit publishes"

    # Every workflow, not just promote-production: the same literal appeared a
    # third time in deploy-staging's smoke step, and each miss cost a full
    # red pipeline to discover.
    import glob as _glob
    for path in sorted(_glob.glob(os.path.join(WF, "*.yml"))):
        wf = open(path, encoding="utf-8").read()
        # Only the colour conditional -- other ports in these files are
        # container-internal (CI's throwaway Keycloak on 8180, for one).
        for line in re.findall(r'^.*\$active" = blue.*$', wf, re.M):
            for port in re.findall(r"port=(\d+)", line):
                assert port in published, (
                    f"{os.path.basename(path)} checks {port} for a colour, which no colour "
                    "stack or unit publishes")

    # And the watchdog, which repairs colour publishers by name and port.
    pw = open(os.path.join(ROOT, "deploy", "portwatch.sh"), encoding="utf-8").read()
    body = "\n".join(l for l in pw.splitlines() if not l.lstrip().startswith("#"))
    for port in re.findall(r"(?:blue|green):(\d+)", body):
        assert port in published, \
            f"portwatch.sh watches {port}, which no colour stack or unit publishes"


def test_the_pipeline_reloads_the_edge():
    """The edge config was deployed by hand only, and nobody knew.

    A Caddyfile change reached the VM mirror through the pipeline and stopped
    there: the running caddy kept its old config, proxying to ports that had
    just been removed. /grafana served 502 in production until deploy-edge.sh
    was run by hand.
    """
    s = _wf("deploy-staging.yml")
    assert "deploy-edge.sh" in s, \
        "deploy-staging must reload the edge, or a Caddyfile change never takes effect"


def test_the_alert_check_reads_the_source_not_a_mailbox():
    """RULES 17 is only actionable if there is something to run.

    The alert that mattered on 2026-08-16 was delivered correctly and read by
    nobody. A mailbox needs credentials this session does not have, and reports
    what fired at some point; the alertmanager API reports what is firing now.
    """
    sh = open(os.path.join(ROOT, "deploy", "alerts.sh"), encoding="utf-8").read()
    assert "api/v2/alerts" in sh, "must query the alertmanager API"
    assert "11040" in sh, "Grafana is on its 1PESI port"
    assert "exit=2" not in sh
    # An unreachable Grafana must not read as 'nothing is firing'.
    assert "CANNOT REACH GRAFANA" in sh, \
        "a Grafana that cannot be reached is a finding, not a clean result"


def test_the_sandbox_edge_is_a_separate_process():
    """Platform RULES §6: the sandbox gets its own caddy, not another listener.

    Until 2026-08-16 sandbox.confinia.io came out of confinia_caddy_1, the same
    process as www and api -- so a config error while working on the sandbox,
    which is by definition where unfinished things are tried, took production
    with it. A second port on the same process satisfies the numbering and none
    of the isolation, which is exactly the shortcut this test refuses.
    """
    compose = open(os.path.join(ROOT, "deploy", "sandbox-stack", "docker-compose.yml"),
                   encoding="utf-8").read()
    assert "container_name: confinia-sandbox_caddy_1" in compose, \
        "the sandbox edge must be its own container"

    sbx = open(os.path.join(ROOT, "deploy", "caddy-sandbox", "Caddyfile"),
               encoding="utf-8").read()
    prod = open(os.path.join(ROOT, "deploy", "caddy", "Caddyfile"), encoding="utf-8").read()

    assert "http://sandbox.confinia.io:11400" in sbx
    assert "sandbox.confinia.io:11400" not in prod, \
        "11400 must be served by the sandbox process, not added to production's"

    # A shared admin address lets `caddy reload` load a config into ANOTHER
    # caddy's process -- the VM-wide outage of 2026-07-20.
    admin_sbx = re.search(r"admin localhost:(\d+)", sbx)
    admin_prod = re.search(r"admin localhost:(\d+)", prod)
    assert admin_sbx and admin_prod, "both edges must pin an admin address"
    assert admin_sbx.group(1) != admin_prod.group(1), \
        f"both edges share admin {admin_sbx.group(1)}; a reload could cross processes"

    # And the reload script must talk to the sandbox admin, never production's.
    sh = open(os.path.join(ROOT, "deploy", "sandbox-edge-up.sh"), encoding="utf-8").read()
    assert admin_sbx.group(1) in sh and f"localhost:{admin_prod.group(1)}" not in sh, \
        "the sandbox reload script must address its own admin endpoint"


def test_staging_dual_listens_until_the_platform_flips():
    """Rollback is doing nothing, as in the band migration."""
    prod = open(os.path.join(ROOT, "deploy", "caddy", "Caddyfile"), encoding="utf-8").read()
    for host in ("staging.confinia.io", "staging.api.confinia.io"):
        line = next(l for l in prod.splitlines() if l.startswith(f"http://{host}:"))
        assert ":11000" in line and ":11300" in line, \
            f"{host} must answer on both ports until the edge is flipped: {line}"


def test_the_shared_libraries_are_installed_before_the_edge_reloads():
    """Order matters: the edge serves /lib/* from that directory.

    Reloading an edge pointed at an empty directory returns 404 for a module,
    and a missing module is a map that hangs with no error -- the same silence
    this whole family of bug hides behind.
    """
    s = _wf("deploy-staging.yml")
    assert "shared-lib-up.sh" in s, "the pipeline must install the shared libraries"
    assert s.index("shared-lib-up.sh") < s.index("deploy-edge.sh"), \
        "the libraries must be in place BEFORE the edge starts serving them"


def test_the_app_edges_bind_loopback_but_the_collector_does_not():
    """Two opposite requirements, and getting either backwards fails silently.

    The caddies must be loopback: binding every interface left ufw as the single
    rule between staging/sandbox and the internet, and the basic_auth gate lives
    in those processes, so a direct connection would carry credentials over
    plaintext HTTP around the edge's TLS.

    The otel collector must NOT be: the stack APIs push through
    host.containers.internal -- the podman gateway, not the host loopback -- so
    a loopback bind makes it unreachable from every container at once. An
    exporter with nowhere to send does not error; it stops reporting.
    """
    import glob
    for rel in ("deploy/caddy/Caddyfile", "deploy/caddy-sandbox/Caddyfile"):
        s = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        sites = re.findall(r"^http://[a-z.\-]+\.confinia\.io:\d+.*\{\s*$", s, re.M)
        binds = len(re.findall(r"^\tbind 127\.0\.0\.1\s*$", s, re.M))
        assert binds == len(sites), (
            f"{rel}: {len(sites)} site block(s) but {binds} bind directive(s); "
            "an unbound block listens on every interface")

    otel = open(os.path.join(ROOT, "deploy", "otel-collector.yaml"), encoding="utf-8").read()
    for endpoint in re.findall(r"endpoint: (\S+:\d+)", otel):
        assert not endpoint.startswith("127.0.0.1"), (
            f"otel endpoint {endpoint} is on loopback; every container pushes "
            "through host.containers.internal and would silently stop reporting")


def test_no_legacy_port_survives_in_any_declaration():
    """1PESI is finished only when nothing NAMES the old ports.

    Every step of this migration was found by a red pipeline rather than by
    reading: green's 8402 lived on in four files, and each fix widened the
    search only as far as the file that had just failed.
    """
    import glob
    legacy = {"8091": "blue api", "5441": "blue geo db", "8402": "green api",
              "5442": "green geo db", "8403": "staging api", "8085": "app caddy",
              "8086": "grafana", "8095": "keycloak", "8097": "demo",
              "8094": "otel exporter", "2085": "caddy admin"}
    checked = (glob.glob(os.path.join(ROOT, "deploy", "*.sh"))
               + glob.glob(os.path.join(ROOT, "deploy", "stack", "*.yml"))
               + glob.glob(os.path.join(ROOT, "deploy", "quadlet", "*.container"))
               + glob.glob(os.path.join(WF, "*.yml"))
               + [os.path.join(ROOT, "docker-compose.yml"),
                  os.path.join(ROOT, "deploy", "caddy", "Caddyfile"),
                  os.path.join(ROOT, "deploy", "otel-collector.yaml")])
    for path in sorted(checked):
        for i, line in enumerate(open(path, encoding="utf-8").read().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue          # history is allowed to name them
            # ...and so is a trailing comment: CI's throwaway Keycloak explains
            # itself by contrast with the VM's old port.
            code = stripped.split("#", 1)[0]
            if not code.strip():
                continue
            for port, what in legacy.items():
                assert port not in code, (
                    f"{os.path.basename(path)}:{i} still names {port} ({what}): "
                    f"{stripped[:70]}")


def test_the_reload_addresses_the_admin_endpoint_it_declares():
    """caddy cannot rebind its own admin through that admin.

    Moving 2085 -> 11090 needs a RECREATE; and once moved, a reload issued
    without --address talks to the default 2019 and either fails or, worse,
    reaches ANOTHER caddy on the host -- the VM-wide outage of 2026-07-20.
    """
    caddy = open(os.path.join(ROOT, "deploy", "caddy", "Caddyfile"), encoding="utf-8").read()
    admin = re.search(r"^\tadmin localhost:(\d+)", caddy, re.M)
    assert admin, "the production edge must pin an admin address"
    sh = open(os.path.join(ROOT, "deploy", "deploy-edge.sh"), encoding="utf-8").read()
    assert f"--address localhost:{admin.group(1)}" in sh, \
        f"deploy-edge.sh must reload through localhost:{admin.group(1)}"


def test_the_units_carry_every_variable_the_compose_file_supplies():
    """A Quadlet unit reads ONLY its EnvironmentFile.

    Moving the colours to systemd silently dropped everything the compose file
    passed through `environment:`. OTEL_EXPORTER_OTLP_ENDPOINT was one, and
    api/main.py exports only `if OTLP:` -- so the variable going missing is not
    an error the process reports, it is dashboards that quietly stop filling.
    """
    import glob
    compose_env = set()
    for f in glob.glob(os.path.join(ROOT, "deploy", "stack", "docker-compose-*.yml")):
        s = open(f, encoding="utf-8").read()
        block = s.split("environment:")[1].split("\n    ")[0] if "environment:" in s else ""
        compose_env |= set(re.findall(r"^\s+([A-Z][A-Z0-9_]+):", block, re.M))

    for path in sorted(glob.glob(os.path.join(ROOT, "deploy", "quadlet", "*.container"))):
        unit = open(path, encoding="utf-8").read()
        declared = set(re.findall(r"^Environment=([A-Z][A-Z0-9_]+)=", unit, re.M))
        has_envfile = "EnvironmentFile=" in unit
        missing = {v for v in compose_env if v not in declared}
        # Anything not in the unit must be in secrets.env, which the unit reads.
        assert not (missing and not has_envfile), \
            f"{os.path.basename(path)} supplies neither {sorted(missing)} nor an EnvironmentFile"
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" in declared, (
            f"{os.path.basename(path)} does not set the OTLP endpoint; the API "
            "exports only `if OTLP:` and would stop reporting in silence")


def test_the_pipeline_redeploys_what_staging_actually_serves():
    """staging.confinia.io/api reaches the staging STACK, not the passive colour.

    Nothing in the pipeline redeployed it. The founder opened a staging URL on
    three separate occasions and got a build up to 17 hours old each time, while
    the static page beside it was current -- which reads as "the fix does not
    work", and cost a round trip to explain every time.
    """
    s = _wf("deploy-staging.yml")
    assert "staging-up.sh" in s, "the pipeline must redeploy the staging stack"
    assert "sandbox-up.sh" in s, \
        "the sandbox API must be redeployed: it 404'd new endpoints while the page was current"
    assert "sandbox-edge-up.sh" in s, "the sandbox edge must be reloaded too"
    assert s.index("smoke_prod.py") < s.index("staging-up.sh"), \
        "staging must be redeployed only after the staged colour passes its smoke"


def test_container_replacement_is_atomic():
    """`rm -f || true` swallows its own failure.

    On 2026-08-17 the rm failed, the run that followed collided on the name, and
    the staging environment stayed on the OLD container -- a deploy that had run
    and changed nothing, while `podman ps` showed a published port that `ss`
    could not see. --replace cannot half-succeed.
    """
    import glob
    for path in sorted(glob.glob(os.path.join(ROOT, "deploy", "*-up.sh"))):
        s = open(path, encoding="utf-8").read()
        body = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
        # Only long-lived containers: `podman run --rm` is an ephemeral
        # validation step and owns no name to collide on.
        if "podman run -d" not in body:
            continue
        assert "podman run -d --replace" in body, \
            f"{os.path.basename(path)} must replace atomically, not rm-then-run"
        assert "rm -f" not in body, \
            f"{os.path.basename(path)} still removes by hand before running"


def test_a_promotion_warms_the_colour_before_the_smoke_measures_it():
    """2026-08-25: a promotion switched correctly, proved the new colour was
    answering, then failed its production smoke because the report PDF was the
    request paying the cold cost of geometry and neighbours. The colour was
    healthy; the first caller was unlucky, and the first caller was the smoke.

    The warm-up must not be a gate -- the smoke that follows is the gate -- so
    its failures are swallowed on purpose.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..", "deploy",
                            "deploy-api.sh"), encoding="utf-8").read()
    promote = src.split("promote() {")[1].split("\n}")[0]
    assert "report.pdf" in promote, "warm the expensive path, not just /healthz"
    assert "|| true" in promote, "a warm-up that can fail a deployment is a second gate"
    assert promote.index("stacks.sh promote") < promote.index("warming"), \
        "warm the colour that is now live, not the one being left"
