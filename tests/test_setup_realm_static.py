"""The realm bootstrap must survive `set -u` (issue #132).

`REALM="$REALM"` — a self-referential assignment with no default — is an unbound
variable under `set -u`, and the script died on line 14 before doing anything.
It got there because a blanket `${REALM:-confinia}` -> `$REALM` substitution,
meant to normalise the *usages*, also rewrote the *declaration*.

The CI keycloak job failed for two days on both branches that touched this file,
and the visible symptom was three unrelated billing tests failing to reach an
API that had never started.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
SH = os.path.join(ROOT, "deploy", "keycloak", "setup-realm.sh")


def _read():
    return open(SH, encoding="utf-8").read()


def test_no_self_referential_assignment():
    for i, line in enumerate(_read().splitlines(), 1):
        if line.strip().startswith("#"):
            continue
        m = re.match(r'\s*([A-Z_]+)="?\$\{?\1\}?"?\s*$', line)
        assert not m, (
            f"line {i}: {m.group(1)}=\"${m.group(1)}\" is unbound under `set -u`; "
            f"give it a default: {line.strip()}")


def test_the_realm_has_a_default():
    assert re.search(r'REALM="?\$\{REALM:-\w+\}"?', _read()), \
        "REALM must default, so the script runs unchanged in CI and in production"


def test_the_script_still_runs_under_set_u():
    # `bash -n` only parses; `set -u` failures are runtime. This asserts the
    # guard rather than the behaviour, which is what a static test can do.
    s = _read()
    assert "set -eu" in s, "the script relies on set -eu; keep it and keep it honest"
