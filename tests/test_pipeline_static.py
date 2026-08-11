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
