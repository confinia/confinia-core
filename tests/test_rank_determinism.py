"""The stated facts must not depend on server load (issue #205).

The report's document reference is computed from the facts it states. So any
fact that can appear or vanish for a reason other than the data makes two
honest copies of one commune disagree -- the exact accusation the reference
exists to prevent. Observed in production on 2026-08-24: a cold page cache lost
a 3 s race that the same query wins warm in ~295 ms, and the rank turned into
"not stated, and why".
"""
import os
import re

SRC = open(os.path.join(os.path.dirname(__file__), "..", "api", "main.py"),
           encoding="utf-8").read()


def _fn(name):
    body = SRC.split(f"def {name}(")[1].split("\ndef ")[0]
    if '"""' in body:
        head, _, rest = body.partition('"""')
        body = head + rest.partition('"""')[2]
    return body


def test_the_rank_budget_sits_between_the_query_and_the_caller():
    """Two constraints, and each one was learned the hard way.

    ABOVE the query's cost: at 3 s the clock decided whether the rank was
    stated, so identical requests disagreed and the document reference moved
    with them.

    BELOW the caller's patience: at 30 s this one query could consume the whole
    30 s a client waits for the report, and on 2026-08-25 a promotion smoke
    timed out and rolled back a healthy deployment. A wrong fact became an
    unavailable page, which is worse.
    """
    budgets = [int(b) for b in
               re.findall(r"SET LOCAL statement_timeout = '(\d+)s'", SRC)]
    assert budgets, "the guard must still exist"
    for b in budgets:
        assert b >= 5, f"{b}s races the cold query, as 3s did"
        assert b <= 15, f"{b}s can eat a 30s client budget on its own, as 30s did"


def test_a_rank_we_cannot_compute_is_still_declined_rather_than_invented():
    """Widening the budget must not turn a missing fact into a claimed one."""
    fn = _fn("_facts")
    assert 'out["declined"].append("rank:timed-out")' in fn
    assert "except Exception:" in fn


def test_the_decline_is_still_explained_in_both_languages():
    for lang in ('"fr"', '"en"'):
        block = SRC.split("DECLINE_PHRASES", 1)[1].split("\n}", 1)[0]
        assert f"{lang}: {{" in block
    assert SRC.count('"rank:timed-out"') >= 3, "both languages plus the producer"
