"""The answer on page one, a glossary, and page n/N (issue #205).

A professional decides on page one whether a document is worth the next ten
minutes. Ours opened on a contents list and a method note -- both necessary,
neither an answer to "what happened to this commune?".
"""
import os

SRC = open(os.path.join(os.path.dirname(__file__), "..", "api", "main.py"),
           encoding="utf-8").read()


def _fn(name):
    body = SRC.split(f"def {name}(")[1].split("\ndef ")[0]
    if '"""' in body:
        head, _, rest = body.partition('"""')
        body = head + rest.partition('"""')[2]
    return body


def test_the_summary_restates_facts_rather_than_recomputing_them():
    """A summary that calculates independently will one day disagree with the
    tables beneath it, and the reader will believe the summary."""
    fn = _fn("summary_of_findings")
    assert 'd.get("facts")' in fn
    for derived in ("formed_from", "absorbed", "stability", "area"):
        assert derived in fn, f"{derived} comes from the computed facts"
    assert "ST_" not in fn and "cur.execute" not in fn, "no second calculation"


def test_it_answers_in_the_order_a_person_would_ask():
    fn = _fn("summary_of_findings")
    order = [fn.index(k) for k in ('s_current"', 's_formed"', 's_area"', 's_versions"')]
    assert order == sorted(order), "still here? how did it start? how big? how far back?"


def test_a_dead_commune_is_not_described_as_existing():
    fn = _fn("summary_of_findings")
    assert 's_gone"' in fn and "FAR_FUTURE" in fn


def test_the_glossary_defines_only_what_this_report_uses():
    """Defining `commune nouvelle` for a German commune teaches the reader that
    the section is padding."""
    fn = _fn("glossary_lines")
    assert "fact_lines(d, lab)" in fn and "summary_of_findings(d, lab)" in fn
    assert "keep.append" in fn, "filtered, not dumped"


def test_the_glossary_always_defines_the_word_the_document_is_built_from():
    fn = _fn("glossary_lines")
    assert "keep.insert(0" in fn, "Version is structural even when unsaid"


def test_pages_are_numbered_out_of_a_known_total():
    """`page 3` alone tells a reader nothing about whether they hold all of it."""
    assert "class _Numbered" in SRC
    fn = SRC.split("class _Numbered")[1].split("\n    c = _Numbered")[0]
    assert "self._pages.append" in fn, "each page held until the total is known"
    assert "total = len(self._pages)" in fn
    assert 'lab["page_n"]' in fn


def test_both_renderers_read_the_same_builders():
    for fn in ("summary_of_findings(d, lab)", "glossary_lines(d, lab)"):
        assert SRC.count(fn) >= 2
