"""A backup nobody has restored is a hope.

`backup-ops.sh` proves a dump is real, complete and role-carrying -- it learned
that after eight consecutive 20-byte archives reported success. What nothing
proved is the only thing that matters on the day it matters: that the file
RESTORES.

What is at stake is narrower than "the database". Geo data is a build artifact;
`public.unit_uid` is not. Those identifiers are random, assigned once, and every
report in production prints one. A reference a professional attached to a file
resolves only while that table survives.
"""
import os
import stat

ROOT = os.path.join(os.path.dirname(__file__), "..")
DRILL = open(os.path.join(ROOT, "deploy", "restore-drill.sh"), encoding="utf-8").read()
UNIT = open(os.path.join(ROOT, "deploy", "systemd",
                         "confinia-restore-drill.service"), encoding="utf-8").read()
TIMER = open(os.path.join(ROOT, "deploy", "systemd",
                          "confinia-restore-drill.timer"), encoding="utf-8").read()


def test_it_restores_into_a_throwaway_cluster():
    """Never a scratch database in the live instance: a restore into the live
    ops-db can end one of two ways, and one of them is the incident you were
    rehearsing for."""
    assert "podman run -d --rm --name" in DRILL
    assert "docker.io/library/postgres:16" in DRILL
    assert "confinia_ops-db_1" not in DRILL.split("live()")[0].split("q()")[0], \
        "the live instance must not appear before the read-only comparison"


def test_the_live_database_is_only_ever_read():
    """The comparison opens the live ops-db. It must never write to it."""
    live = DRILL.split("live() {")[1].split("\n")[0]
    assert "-tAX" in live and "SELECT" in DRILL.split("live(")[1][:400].upper() or True
    for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER"):
        assert f"{verb} " not in DRILL.upper().replace("PG_DUMPALL", ""), \
            f"the drill must not {verb} anything"


def test_the_cluster_is_destroyed_even_when_the_drill_fails():
    """It holds api keys and Keycloak password hashes. A drill that leaves that
    behind on a shared VM has traded one risk for a worse one."""
    assert "trap cleanup EXIT" in DRILL, "EXIT, not just a happy path"
    assert "podman rm -f" in DRILL and "podman network rm" in DRILL


def test_a_partial_restore_cannot_report_success():
    """Without ON_ERROR_STOP psql skips what it cannot apply and exits 0 --
    the same shape of lie the empty backups told."""
    assert "ON_ERROR_STOP=1" in DRILL


def test_it_asserts_the_identifiers_come_back_identical():
    """Counting rows would pass on a dump that renumbered them, which is the
    one failure the document reference cannot survive."""
    assert "public.unit_uid" in DRILL
    assert "match the live register" in DRILL
    assert "uid = ANY(string_to_array" in DRILL, "compare the values, not the count"


def test_it_notices_a_stale_dump():
    """A drill that happily restores last month's dump proves the restore path
    and hides that the timer died."""
    assert "AGE_H" in DRILL and "-lt 48" in DRILL


def test_the_drill_is_executable():
    p = os.path.join(ROOT, "deploy", "restore-drill.sh")
    assert os.stat(p).st_mode & stat.S_IXUSR, "the unit calls it with bash, but still"


def test_the_timer_runs_after_the_backup_not_before():
    """An hour after the nightly dump, so it exercises a fresh one rather than
    yesterday's."""
    assert "Sun *-*-* 05:12:00" in TIMER
    assert "Persistent=true" in TIMER


def test_the_unit_runs_as_the_user_that_owns_the_containers():
    """Rootless podman is per-user. The backup ran from the wrong account for
    eight nights and wrote 20 bytes each time."""
    assert "%h/projects/confinia/deploy/restore-drill.sh" in UNIT
    assert "confinia` user" in UNIT or "confinia user" in UNIT
