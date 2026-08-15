"""The watchdog repairs #123, and stays honest about it (issue #123).

A self-healing loop that nobody counts is how a fault stops being fixed: the
symptom disappears, the cause survives, and six months later nobody remembers
there was one. So every repair is logged, and repeated repairs inside the
cooldown say so explicitly rather than silently looping.
"""
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_it_repairs_only_the_exact_fault():
    sh = _read("deploy", "portwatch.sh")
    # container running + mapping claimed + nothing listening. All three, or the
    # watchdog would fight a deliberate stop.
    assert "podman ps --format" in sh and "grep -qx" in sh, "must require the container to be running"
    assert "podman port" in sh, "must require podman to claim the mapping"
    assert 'ss -ltn' in sh, "must require that nothing is listening"


def test_repairs_are_rate_limited_and_visible():
    sh = _read("deploy", "portwatch.sh")
    assert "COOLDOWN=" in sh, "a repair loop with no cooldown hides a worsening fault"
    assert "NOT repairing" in sh, "hitting the cooldown must be stated, not swallowed"
    assert "REPAIRING" in sh, "every repair must be logged so the frequency stays countable"


def test_it_says_the_cause_is_unknown():
    # Six hypotheses were tested and disproved. The script must not imply a fix.
    sh = _read("deploy", "portwatch.sh")
    assert "cause is still unknown" in sh.lower(), \
        "the script must not read as if #123 were solved"
