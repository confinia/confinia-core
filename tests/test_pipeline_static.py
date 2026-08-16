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


def test_the_systemd_units_carry_both_port_bands():
    """Moving a colour to systemd must not drop it off the 11xxx band.

    The Quadlet units were written before the 1PESI migration, when a colour had
    exactly one port. Replaying them onto a tree that dual-publishes would have
    given the compose path two ports and the systemd path one — and since the
    systemd path is the one that runs, the new band would have quietly stopped
    answering for whichever colour was migrated first.
    """
    import glob
    for path in sorted(glob.glob(os.path.join(ROOT, "deploy", "quadlet", "*.container"))):
        s = open(path, encoding="utf-8").read()
        published = set(re.findall(r"^PublishPort=127\.0\.0\.1:(\d+):", s, re.M))
        legacy = {p for p in published if not p.startswith("11")}
        new = {p for p in published if p.startswith("11")}
        assert legacy and new, (
            f"{os.path.basename(path)} publishes {sorted(published) or 'nothing'}; "
            "a unit must carry the legacy port AND its 11xxx twin while both bands live")
