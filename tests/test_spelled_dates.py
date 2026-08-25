"""Dates written out in sentences, ISO in fields.

Founder's decision, 2026-08-25, after EcoBuilding read a real record: "Elle est
née le 2019-01-01" is not French, and #205's ambition is a document an expert
office would sign — a notaire's file is prose.

The split is the point, not a compromise. A reader who copies a date copies it
from a FIELD, so the unambiguous form stays exactly where copying happens, and
the machine contract EcoBuilding consumes does not move at all.
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


def test_the_first_of_the_month_takes_its_ordinal_in_french():
    """"le 1 janvier" is as wrong as "le 2019-01-01"; the ordinal is not decoration."""
    assert m._d_fr("2019-01-01") == "1ᵉʳ janvier 2019"
    assert m._d_fr("2019-01-02") == "2 janvier 2019"


def test_english_takes_no_ordinal():
    assert m._d_en("2019-01-01") == "1 January 2019"
    assert m._d_en("2016-03-15") == "15 March 2016"


def test_a_date_object_is_accepted_as_well_as_a_string():
    assert m._d_fr(datetime.date(2016, 8, 25)) == "25 août 2016"


def test_anything_that_is_not_a_date_is_returned_untouched():
    """The events already carry "aujourd'hui" and "today" where a period is
    open. This formats dates; it does not police them."""
    for value in ("aujourd'hui", "today", "", "not-a-date", "9999-99-99"):
        assert m._d_fr(value) == value
    assert m._d_fr(None) == ""


def test_the_sentences_are_spelled_in_both_languages():
    fr, en = m.REPORT_LABELS["fr"], m.REPORT_LABELS["en"]
    assert "1ᵉʳ janvier 2019" in fr["s_formed"](2, "2019-01-01")
    assert "1ᵉʳ janvier 2019" in fr["s_stable"]("2019-01-01")
    assert "1ᵉʳ janvier 2026" in fr["cutoff"]("2026-01-01")
    assert "1 January 2019" in en["s_gone"]("Lez", "2019-01-01")
    assert "1 January 2026" in en["l_cutoff"]("2026-01-01")


def test_no_iso_date_survives_in_a_sentence():
    fr = m.REPORT_LABELS["fr"]
    for text in (fr["s_formed"](2, "2019-01-01"), fr["s_stable"]("2019-01-01"),
                 fr["cutoff"]("2026-01-01"), fr["l_cutoff"]("2026-01-01"),
                 fr["l_harmonised"]("2025-01-01")):
        assert "2019-01-01" not in text and "2026-01-01" not in text \
               and "2025-01-01" not in text


def test_the_machine_contract_is_untouched():
    """EcoBuilding parses these; spelling them would break every consumer."""
    fn = SRC.split("def commune_facts(")[1].split("\ndef ")[0]
    assert "_d_fr(" not in fn and "_d_en(" not in fn


def test_the_document_reference_keeps_its_compact_edition():
    """The reference is looked up and compared character by character, never
    read aloud in a sentence."""
    fn = SRC.split("def document_ref(")[1].split("\ndef ")[0]
    assert "_d_fr(" not in fn and "_d_en(" not in fn
    assert 'edition = (d.get("cutoff") or "").replace("-", "")' in fn


def test_the_methodology_declares_both_conventions():
    """A document that uses two forms and explains neither is worse than one
    that uses the wrong form consistently."""
    for lang, needle in (("fr", "toutes lettres"), ("en", "written out")):
        note = m.REPORT_LABELS[lang]["m_dates"]
        assert needle in note
        assert "ISO 8601" in note
        assert ("champ" in note) or ("field" in note)
