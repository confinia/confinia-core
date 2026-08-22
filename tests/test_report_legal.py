"""What Confinia warrants, and the date its picture stops (issue #205).

The founder's position, and it is narrower and more defensible than the usual
disclaimer: **Confinia guarantees provenance, not truth.** Every fact traces to
an open-data source named in the annex, fact by fact; whether that source is
right is the source's business, not ours.

That is why the annex is not decoration. It IS the commitment, and the legal
notice says so rather than disclaiming everything and therefore nothing.

The cut-off date answers the other question a professional asks before trusting
a document: how current is this? Without it a reader cannot tell a missing event
from a not-yet-published one.
"""
import os

import re

SRC = open(os.path.join(os.path.dirname(__file__), "..", "api", "main.py"),
           encoding="utf-8").read()

# The notice is written as adjacent string literals across several lines, so a
# sentence a reader will see is not contiguous in the source. Rejoin them before
# asserting on wording -- otherwise the test fails on perfectly correct text,
# which has happened three times in this suite already.
PROSE = re.sub(r'"\s*\n\s*"', "", SRC)


def _fn(name):
    body = SRC.split(f"def {name}(")[1].split("\ndef ")[0]
    if '"""' in body:
        head, _, rest = body.partition('"""')
        body = head + rest.partition('"""')[2]
    return body


def _renderers():
    """The two renderer bodies, separately.

    Counting an identifier across the whole file was the old way, and it broke
    the moment a third caller appeared -- `report_sections` naming a heading,
    `report_digest` reading a fact. What the test always meant is narrower and
    survives that: EACH renderer reads the shared builder.
    """
    svg = SRC[SRC.index("def _report_svg"):SRC.index("def _report_pdf")]
    return svg, SRC[SRC.index("def _report_pdf"):]


def test_the_notice_states_a_commitment_not_only_a_disclaimer():
    """Boilerplate that disclaims everything says nothing and is trusted less."""
    for lang, phrase in (("fr", "s'engage sur un seul point"),
                         ("en", "commits to one thing")):
        assert phrase in PROSE, f"the {lang} notice must state what we DO warrant"


def test_it_names_the_annex_as_that_commitment():
    assert "L'annexe est cet engagement." in PROSE
    assert "The annex is that commitment." in PROSE


def test_it_places_accuracy_with_the_publishers():
    """The boundary of the commitment, and the reason it is credible."""
    assert "et non de Confinia" in PROSE, "French: accuracy is theirs, not ours"
    assert "with Confinia" in PROSE, "English: same boundary"
    for lang_phrase in ("Nous ne les corrigeons pas et nous ne les garantissons pas",
                        "We do not correct it and we do not warrant it"):
        assert lang_phrase in PROSE, f"stated plainly: {lang_phrase[:40]}"


def test_it_tells_the_reader_where_to_go_when_they_disagree():
    """A notice that only limits liability leaves the reader stuck."""
    assert "se vérifie à la source" in PROSE
    assert "is checked at the source named" in PROSE


def test_the_cutoff_is_derived_from_the_data_not_from_today():
    """A report generated this morning from last year's ingestion is current as
    of last year; saying otherwise is the most flattering possible lie."""
    fn = _fn("data_cutoff")
    assert "geometry_vintage" in fn and "valid_from" in fn
    for forbidden in ("date.today", "datetime.now", "utcnow"):
        assert forbidden not in fn, f"{forbidden} would report freshness we do not have"


def test_an_unknown_cutoff_is_said_rather_than_hidden():
    assert "cutoff_none" in SRC
    assert SRC.count('lab["cutoff_none"]') == 2, "both renderers"


def test_the_cutoff_is_cached_per_process():
    """207 ms cold; it does not change between two reports."""
    fn = _fn("data_cutoff")
    assert "_CUTOFF" in fn and "if country in _CUTOFF" in fn


def test_both_renderers_read_one_builder():
    for part, name in zip(_renderers(), ("SVG", "PDF")):
        assert "legal_lines(d, lab)" in part, f"{name} prints what we warrant"
        assert 'd.get("cutoff")' in part, f"{name} prints how current the data is"
