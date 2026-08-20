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


def test_the_dumps_defend_themselves_and_not_only_the_home_directory():
    """pg_dumpall of the ops instance carries public.api_key (keys that grant
    paid access), Keycloak's public.credential (password hashes) and 27 real
    e-mail addresses. On a VM shared with other products and other people, they
    were surviving on the mode of ~ alone -- one bit between them and every
    account on the machine. Measured 2026-08-19: the files were 0664 and the
    directories 0775.
    """
    assert "umask 077" in CODE, "nothing this script creates may be group/world readable"
    assert 'chmod 600 "$OUT"' in CODE, "the published dump, explicitly"
    assert 'chmod 700 "$DEST"' in CODE, "and the directory holding it"


def test_the_dump_carries_its_globals_and_the_tool_choice_is_pinned():
    """pg_dumpALL, never pg_dump.

    A per-database dump carries no roles, and the restore then dies on
    `role "..." does not exist`. Overwatch found every dump they held was
    unrestorable for exactly that reason -- five per-tenant RLS roles that had
    never been backed up. Ours are safe by construction rather than foresight,
    so the choice is pinned: changing this line to `pg_dump -d ops` must fail
    loudly rather than quietly produce archives that cannot be restored.

    Verified non-vacuously on a real dump: 102 CREATE TABLE, 104 COPY blocks,
    one role (`confinia`) both referenced and created.
    """
    assert "pg_dumpall" in CODE and "pg_dump -d" not in CODE
    assert 'grep -c "^CREATE ROLE "' in CODE, "the globals must be proven present"


def test_the_globals_check_cannot_pass_vacuously():
    """An EMPTY dump satisfies 'every referenced role is created' -- zero
    references, zero missing. The platform session's first run called a
    20-byte file OK for precisely that reason. So the emptiness checks must run
    BEFORE this one, and this one must require a positive count."""
    size_i = CODE.index("MIN_BYTES")
    hdr_i = CODE.index("cluster dump")
    role_i = CODE.index("CREATE ROLE")
    assert size_i < role_i and hdr_i < role_i, \
        "non-triviality is established before any role reasoning"
    assert '-ge 1' in CODE, "at least one role must actually be created"
