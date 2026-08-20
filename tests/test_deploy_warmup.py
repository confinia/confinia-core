"""A passive colour is cold, and promotion hands it to real users (2026-08-20).

Measured on identical data -- both colours held 205 370 rows, 1 255 of them
EPCI -- with the same indexes:

    /v1/export/ohm?country=FR&unit_type=epci&limit=1
        passive colour, first call   37.2 s
        passive colour, third call    1.9 s
        active colour                 0.015 s

A factor of ~2400, and not a planning problem: ANALYZE on the passive colour
changed nothing. One colour's pages are in PostgreSQL's buffer cache because it
serves production continuously; the other's are on disk because it serves
nothing.

Two consequences. The smoke test times out at 30 s, which is how this surfaced
-- it failed a deploy. And every promotion so far has handed real users a cold
colour, with the first requests paying that price unnoticed, because nobody was
watching the moment of the switch.
"""
import os

SH = open(os.path.join(os.path.dirname(__file__), "..", "deploy", "deploy-api.sh"),
          encoding="utf-8").read()
CODE = "\n".join(l for l in SH.splitlines() if not l.lstrip().startswith("#"))


def test_the_staged_colour_is_warmed_before_anyone_uses_it():
    assert "warm() {" in CODE
    assert 'warm "$(port_of "$P")"' in CODE, "and it is actually called"


def test_warming_happens_after_the_colour_is_up_and_before_the_smoke():
    """Warming a container that is not serving yet does nothing; warming after
    the smoke does not help the smoke, which is what failed."""
    i_wait = CODE.index('wait_ok "$(port_of "$P")"')
    i_warm = CODE.index('warm "$(port_of "$P")"')
    assert i_wait < i_warm, "the colour must be answering before we warm it"


def test_it_touches_the_paths_that_were_actually_slow():
    """Not a token /healthz: the 37 s was an export, and reports are the
    premium path a customer pays for."""
    warm = SH.split("warm() {")[1].split("\nwait_ok()")[0]
    assert "/v1/export/ohm" in warm
    assert "report.svg" in warm
    assert "neighbours=true" in warm


def test_warming_can_never_fail_a_deploy():
    """It is a favour to the cache, not a gate. A slow warm-up must not block a
    release that is otherwise good."""
    warm = SH.split("warm() {")[1].split("\nwait_ok()")[0]
    assert "|| echo" in warm or "2>/dev/null" in warm, "failures are swallowed"
    assert "exit 1" not in warm
