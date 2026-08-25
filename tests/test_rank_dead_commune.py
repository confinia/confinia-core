"""A commune that no longer exists is not the largest of its district.

Found in production on 2026-08-25, on the report for Lez (31298): 2.58 km2,
dissolved into Saint-Béat-Lez in 2019, and the document stated *"la plus étendue
des 595 — Haute-Garonne"*.

The peer set holds the CURRENT communes of the district, so a version that no
longer exists is absent from it. Its own area came back NULL, `p.km2 > NULL`
matched no rows, the count was 0 and the rank 0 + 1 = 1. Every dissolved commune
therefore claimed first place -- confidently, in a document sold on provenance,
and for every merged commune in the database.
"""
import os

SRC = open(os.path.join(os.path.dirname(__file__), "..", "api", "main.py"),
           encoding="utf-8").read()


def _facts():
    body = SRC.split("def _facts(")[1].split("\ndef ")[0]
    head, _, rest = body.partition('"""')
    return head + rest.partition('"""')[2]


def test_the_rank_is_claimed_only_when_the_unit_is_in_the_peer_set():
    """The bug in one line: rank 1 was computed from an absence."""
    fn = _facts()
    assert "row[2] is not None" in fn, "the unit's own area decides whether a rank exists"


def test_the_query_reads_back_the_units_own_area():
    """Two columns could not tell 'largest' from 'not present'."""
    fn = _facts()
    assert fn.count("SELECT km2 FROM peers WHERE code = %s") >= 2, \
        "once to compare against, once to know whether it is there at all"


def test_a_unit_outside_the_peer_set_declines_rather_than_ranks():
    fn = _facts()
    assert 'out["declined"].append("rank:not-comparable")' in fn


def test_the_decline_is_explained_in_both_languages():
    block = SRC.split("DECLINE_PHRASES", 1)[1].split("\n}", 1)[0]
    assert block.count('"rank:not-comparable"') == 2, "fr and en, exactly once each"
    assert "n'existe plus" in block and "no longer exists" in block


def test_the_two_rank_declines_stay_distinct():
    """'too costly to establish' and 'cannot be compared' are different facts:
    one is our machine, the other is the world. Collapsing them would hide a
    performance problem behind a modelling one."""
    block = SRC.split("DECLINE_PHRASES", 1)[1].split("\n}", 1)[0]
    assert '"rank:timed-out"' in block and '"rank:not-comparable"' in block


def test_a_living_unit_still_gets_a_rank():
    """The fix must not silence the fact for everyone -- Toulouse really is the
    largest of the Haute-Garonne's 595."""
    fn = _facts()
    assert 'out["rank"] = {' in fn
    assert '"by_area": int(row[1])' in fn
