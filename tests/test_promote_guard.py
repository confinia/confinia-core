"""A promotion must not ship code nobody reviewed (issue #115).

This directory is BOTH the deployment checkout and the tree people edit in.
`deploy-staging` resets it to the commit it deploys; `promote-production` does
not -- it runs whatever is sitting here. On 2026-08-24 a production promotion
executed scripts from an unmerged branch. Nothing broke, and nothing would have
said so, which is the part that matters: the failure is invisible by
construction.

The real fix is a deployment checkout that nobody edits, and it needs the
workflow files. This is the half that can ship without them: refuse loudly.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
STACKS = open(os.path.join(ROOT, "deploy", "stacks.sh"), encoding="utf-8").read()


def _branch():
    return STACKS.split("write-upstreams|promote)")[1].split("\nstatus)")[0]


def test_a_promotion_refuses_a_dirty_tree():
    """Uncommitted work in this directory would go live with the promotion."""
    b = _branch()
    assert "git diff --quiet HEAD --" in b
    assert "REFUSING" in b and "exit 3" in b


def test_a_promotion_refuses_code_that_is_not_on_origin_main():
    """The failure actually observed: HEAD on a branch nobody had merged."""
    b = _branch()
    assert "git merge-base --is-ancestor HEAD origin/main" in b


def test_it_checks_only_tracked_files():
    """`git status --porcelain` would list .claude/, agent/ and other untracked
    scratch that lives here permanently, and a guard that always fires is a
    guard people learn to override."""
    b = _branch()
    assert "status --porcelain" not in b


def test_write_upstreams_is_exempt():
    """deploy-edge.sh regenerates the state file on every deploy, and that
    changes no colour. Guarding it would break the deploy instead."""
    b = _branch()
    assert '[ "$1" = promote ]' in b, "the guard is scoped to the promote verb"


def test_a_rollback_can_always_be_forced():
    """A rollback runs through this same path. A guard that blocks a rollback
    during an incident is worse than the hazard it guards against."""
    b = _branch()
    assert "PROMOTE_UNSHIPPED" in b
    assert b.count("PROMOTE_UNSHIPPED") >= 3, "documented in the refusal messages too"


def test_an_unreachable_origin_does_not_block_a_promotion():
    """Fetch failure must degrade to the last known origin/main, not to a
    refusal: the network is not part of the safety property."""
    b = _branch()
    fetch = [l for l in b.split("\n") if "git fetch" in l][0]
    assert "||" in fetch or "2>/dev/null" in fetch


def test_the_refusal_says_what_to_do():
    """A guard that only says no teaches people to disable it."""
    b = _branch()
    assert "Commit, or PROMOTE_UNSHIPPED=1." in b
    assert "git --no-pager diff --stat HEAD" in b, "show WHAT is uncommitted"
