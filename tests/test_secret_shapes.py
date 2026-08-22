"""Secrets are ignored by SHAPE, not by name.

.gitignore named each secret file individually — deploy/secrets.env,
deploy/sandbox.env, deploy/mail.env, deploy/creem.env, tests/e2e/.env. That is
an allowlist by enumeration, and it fails the way allowlists do.

Found 2026-08-22 while preparing to work directly on the VM: its working copy
held `mail.env` at the repo root and two `secrets.env.bak.*` from the DSN
rotations — nine credential lines each, none of them ignored. Any `git add -A`,
which is how most of this repo's commits are staged, would have put live
credentials into a public repository.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
IGNORE = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()


def test_any_env_file_is_ignored_whatever_it_is_called():
    assert re.search(r"^\*\.env$", IGNORE, re.M), \
        "a new secret file must not need a new rule"


def test_backup_copies_of_secrets_are_ignored():
    """A rotation leaves secrets.env.bak.<timestamp> beside the original."""
    assert re.search(r"^\*\.bak$", IGNORE, re.M)
    assert re.search(r"^\*\.bak\.\*$", IGNORE, re.M), "…and the timestamped shape"


def test_the_committed_templates_stay_committable():
    """Ignoring every .env would also ignore the examples people copy from."""
    assert re.search(r"^!\*\.env\.example$", IGNORE, re.M)
    for tmpl in ("deploy/mail.env.example", "tests/e2e/.env.example"):
        assert os.path.exists(os.path.join(ROOT, tmpl)), f"{tmpl} must exist"


def test_the_exception_comes_after_the_rule_it_excepts():
    """gitignore is order-sensitive: a negation before its rule does nothing."""
    assert IGNORE.index("*.env\n") < IGNORE.index("!*.env.example")
