"""A merger of one commune, and an event that hid a date it already knew.

Both reported from EcoBuilding's first read of a real record (Saint-Béat-Lez,
31471) and reproduced here before being fixed:

  "Elle est née le 2019-01-01 de la fusion de 1 commune(s)."  -- beside a
      lineage carrying parents ["31298", "31471"], two of them;
  an absorption event with date null and the wording "entre 2019-01-01 et
      aujourd'hui", while /history on the same service publishes the exact day.

Plus agreement: "1 période(s) sur 2 n'ont aucune limite". A reader who meets
that stops believing the sentences, and this document's whole claim is that its
sentences were written on purpose.
"""
import datetime
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
SRC = open(os.path.join(HERE, "..", "api", "main.py"), encoding="utf-8").read()

sys.path.insert(0, os.path.join(HERE, "..", "api"))
os.environ.setdefault("PG_DSN", "postgresql://unused@127.0.0.1:1/unused")
os.environ.setdefault("OPS_DSN", "postgresql://unused@127.0.0.1:1/unused")
try:
    import main as m
except Exception as exc:                # pragma: no cover - environment
    pytest.skip(f"api/main.py not importable: {exc}", allow_module_level=True)


# ------------------------------------------------------------ the merger count

def test_a_commune_that_kept_its_code_still_counts_in_its_own_merger():
    """Saint-Béat + Lez -> Saint-Béat-Lez is a merger of TWO, one of whose codes
    survived. `formed_from` rightly excludes the survivor; the sentence must
    not."""
    fn = SRC.split("def summary_of_findings(")[1].split("\ndef ")[0]
    assert 'kept_its_code = d["code"] in (cur.get("parents") or [])' in fn
    assert '+ (1 if kept_its_code else 0)' in fn


def test_a_creation_from_others_is_not_inflated():
    """A commune created from predecessors whose codes all died counts only
    them -- adding one there would invent a predecessor."""
    fn = SRC.split("def summary_of_findings(")[1].split("\ndef ")[0]
    assert "if kept_its_code else 0" in fn, "the +1 is conditional, not constant"


def test_a_commune_is_still_not_its_own_predecessor():
    """The fix must not undo the reason formed_from excludes self."""
    fn = SRC.split("def _facts(")[1].split("\ndef ")[0]
    assert 'if c != code' in fn


# ------------------------------------------------------------ the dated event

def test_an_absorption_carries_the_day_it_happened():
    """`parents` belongs to the version starting that day, so the date is known.
    An undated event sorts and cites like a rumour."""
    fn = SRC.split("def derive_events(")[1].split("\ndef ")[0]
    assert '"date": None, "type": "absorbed"' not in fn
    assert fn.count('"type": "absorbed"') >= 2
    assert "absorbed_on" in fn


def test_the_dated_phrase_exists_in_both_languages():
    block = SRC.split("EVENT_PHRASES", 1)[1].split("\n}", 1)[0]
    assert block.count('"absorbed_on"') == 2
    assert "a absorbé" in block and "absorbed" in block


def test_the_range_wording_survives_for_the_case_that_needs_it():
    """Where a date genuinely is unknown, saying "between X and Y" is right.
    Removing the phrase would have replaced a hedge with a guess."""
    block = SRC.split("EVENT_PHRASES", 1)[1].split("\n}", 1)[0]
    assert '"absorbed"' in block, "the undated wording is kept, not deleted"


# ------------------------------------------------------------ agreement

def test_the_report_no_longer_writes_parenthesised_plurals():
    labels = SRC[SRC.index("REPORT_LABELS = {"):SRC.index("DECLINE_PHRASES")]
    assert "(s)" not in labels, "no reader should be asked to pick the ending"


@pytest.mark.parametrize("n,expected", [(0, "many"), (1, "one"), (2, "many")])
def test_english_gives_zero_the_plural(n, expected):
    assert m._n_en(n, "one", "many") == expected


@pytest.mark.parametrize("n,expected", [(0, "one"), (1, "one"), (2, "many")])
def test_french_gives_zero_the_singular(n, expected):
    """This is exactly where the two languages part company -- "0 commune" but
    "0 communes" -- which is why they do not share a helper."""
    assert m._n_fr(n, "one", "many") == expected


def test_the_french_verb_agrees_too():
    fr = m.REPORT_LABELS["fr"]
    assert "n'a aucune limite" in fr["l_nogeom"](1, 3)
    assert "n'ont aucune limite" in fr["l_nogeom"](2, 3)
    assert "1 période sur 3" in fr["l_nogeom"](1, 3)
    assert "2 périodes sur 3" in fr["l_nogeom"](2, 3)


def test_the_english_verb_agrees_too():
    en = m.REPORT_LABELS["en"]
    assert "1 of 3 periods has" in en["l_nogeom"](1, 3)
    assert "2 of 3 periods have" in en["l_nogeom"](2, 3)


def test_the_counted_nouns_agree_in_both_languages():
    for lang, one, many in (("fr", "1 commune.", "2 communes."),
                            ("en", "1 commune.", "2 communes.")):
        lab = m.REPORT_LABELS[lang]
        assert lab["s_formed"](1, "2019-01-01").endswith(one)
        assert lab["s_formed"](2, "2019-01-01").endswith(many)
