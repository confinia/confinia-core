"""A dump beside the database it protects survives a bad migration, and nothing
else.

The drill (#115) proves the ops dump restores and that the identifiers come
back identical. It cannot prove the file still exists after the disk does not.
Measured on 2026-08-26: the dump held 8 identifiers, the live register held 10
— a nightly cadence leaves up to a day of new identifiers on one disk, and
every one of them is printed on a report someone may already have filed.

The Mac pulls; the pull leaves a receipt; the weekly drill reads it. Without
the receipt, a laptop closed for a long weekend and a chain that stopped three
weeks ago look identical from the VM — and the second is invisible precisely
when it matters.
"""
import os
import stat

ROOT = os.path.join(os.path.dirname(__file__), "..")
PULL = open(os.path.join(ROOT, "deploy", "macos", "pull-ops-backups.sh"),
            encoding="utf-8").read()
DRILL = open(os.path.join(ROOT, "deploy", "restore-drill.sh"), encoding="utf-8").read()
PLIST = open(os.path.join(ROOT, "deploy", "macos",
                          "io.confinia.pull-ops-backups.plist"), encoding="utf-8").read()


def test_the_host_is_not_committed():
    """Infrastructure addresses stay out of a public repository."""
    assert "CONFINIA_REMOTE" in PULL
    assert "REPLACE-WITH-HOST" in PLIST
    for leak in ("5.135.", "confinia-ovh"):
        assert leak not in PULL and leak not in PLIST


def test_a_pulled_dump_is_never_overwritten_or_deleted_by_the_vm():
    """A dump never changes once published. A plain sync would make this an
    off-site mirror of the VM's retention rather than a backup — the VM prunes
    at 14 days and the copy would follow it into the bin."""
    assert "--ignore-existing" in PULL
    assert "--delete" not in PULL


def test_what_arrived_is_verified_not_what_was_sent():
    """Eight consecutive 20-byte 'backups' already reported success here. A
    truncated transfer is the same failure wearing a different coat."""
    assert "gzip -t" in PULL
    # Anchored on CODE, not on prose: "prune" also appears in the comments
    # explaining why the order matters, which made an earlier version of this
    # test pass or fail on where a sentence sat.
    assert PULL.index("gzip -t") < PULL.index('rm -f "$old"')


def test_pruning_happens_after_a_verified_pull():
    """The order backup-ops.sh learned the hard way: publish, verify, then
    prune. Reversed, a bad night deletes the last good copy."""
    assert PULL.index("gzip -t") < PULL.index('-gt "$KEEP"')


def test_the_pull_leaves_a_receipt():
    assert ".last-pull" in PULL
    assert "date -u" in PULL


def test_the_drill_reads_the_receipt_and_says_when_none_exists():
    assert ".last-pull" in DRILL
    assert "no off-VM copy has ever been recorded" in DRILL


def test_a_long_weekend_warns_and_a_dead_chain_fails():
    """Two thresholds because they mean different things. Only the second exits
    non-zero, which is what the platform's unit-failed alert can see."""
    assert "-gt 72" in DRILL, "warn after three days"
    assert "-gt 336" in DRILL, "fail after a fortnight"
    fail_branch = DRILL.split("-gt 336")[1].split("elif")[0]
    assert "exit 1" in fail_branch
    warn_branch = DRILL.split("-gt 72")[1].split("else")[0]
    assert "exit 1" not in warn_branch, "a closed laptop is not an incident"


def test_the_receipt_failing_does_not_fail_the_pull():
    """The dumps did leave the VM; only the bookkeeping did not."""
    assert "could not write the receipt" in PULL


def test_the_pull_script_is_executable():
    p = os.path.join(ROOT, "deploy", "macos", "pull-ops-backups.sh")
    assert os.stat(p).st_mode & stat.S_IXUSR


def test_the_schedule_survives_a_sleeping_laptop():
    """StartCalendarInterval alone silently skips a run it slept through."""
    assert "RunAtLoad" in PLIST and "StartCalendarInterval" in PLIST
