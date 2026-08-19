"""The backup that reported success every night while writing 20 bytes.

Found by the platform session 2026-08-19: `confinia-ops-backup` had produced
EIGHT consecutive empty archives (2026-08-12..19) and recorded
`Result=success, ExecMainStatus=0` each time. Two faults combined:

  1. The unit ran as the debian/platform user while the stack had moved to the
     confinia user. Rootless podman is per-user, so `podman exec
     confinia_ops-db_1` could not see the container at all.
  2. `podman exec ... | gzip > file` -- podman failed, gzip wrote a valid
     20-byte archive, and a pipeline's status is its LAST command, so `set -eu`
     never tripped.

And the prune ran unconditionally, so the failing nights were quietly eating
the last good dumps: the final real one (2026-08-11) was due for deletion
around 2026-08-26, by a job reporting success.
"""
import os
import re

SH = open(os.path.join(os.path.dirname(__file__), "..", "deploy", "backup-ops.sh"),
          encoding="utf-8").read()
CODE = "\n".join(l for l in SH.splitlines() if not l.lstrip().startswith("#"))


def test_a_failing_pipeline_fails_the_script():
    """`set -eu` alone does not see a failure upstream of a pipe."""
    assert "set -euo pipefail" in CODE


def test_the_dump_is_proven_before_it_is_published():
    """Three questions, each already answered wrongly by a file that existed:
    is it intact, is it big enough to be real, is it OUR dump?"""
    assert "gzip -t" in CODE, "intact"
    assert "MIN_BYTES" in CODE, "big enough"
    assert "PostgreSQL database cluster dump" in CODE, "actually a cluster dump"


def test_it_is_written_aside_and_only_then_published():
    assert ".partial" in CODE, "the in-progress dump must not wear the real name"
    assert re.search(r'mv "\$TMP" "\$OUT"', CODE), "publish is a rename, after the checks"
    assert 'trap ' in CODE and 'rm -f "$TMP"' in CODE, "a failed run leaves no debris"


def test_the_prune_happens_only_after_a_verified_dump_exists():
    """This is what made it time-critical: eight failing nights kept deleting."""
    pub = CODE.index('mv "$TMP" "$OUT"')
    prune = CODE.index("-mtime +14 -delete")
    assert prune > pub, "pruning before publishing eats the last good backups"


def test_the_header_check_cannot_be_defeated_by_sigpipe():
    """Under pipefail, `head` exiting early SIGPIPEs zcat and the pipeline
    reports failure even when the pattern was found -- it rejected a perfectly
    good 6.6 MB dump on the first real run."""
    assert "| grep -q" not in CODE.split("cluster dump")[0][-200:], \
        "no pipeline that head/grep can cut short"
    assert "|| true" in CODE, "the SIGPIPE status must be neutralised"


def test_the_unit_says_where_it_must_run():
    """Rootless podman is per-user; installing this as the wrong user is
    precisely what produced eight empty nights."""
    unit = open(os.path.join(os.path.dirname(__file__), "..", "deploy", "systemd",
                             "confinia-ops-backup.service"), encoding="utf-8").read()
    assert "confinia" in unit and "per-user" in unit
