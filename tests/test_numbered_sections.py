"""Numbered sections, a stated date format, and figures that carry their basis.

The three mechanical items left on issue #205 after the founder deferred the
cover page. Each one exists so a reader can USE the document rather than only
read it: cite "section 6" in writing, know that 2016-01-01 is a civil effect
date and not a publication date, and copy a density without leaving behind the
geography that makes it mean anything.
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


def test_the_order_is_built_once_and_read_by_both_the_contents_and_the_headings():
    """Computing the numbers twice means one day disagreeing, which is worse
    than having no numbers at all."""
    assert "def report_sections(" in SRC
    assert "report_sections(d, lab)" in _fn("report_contents")
    assert "report_sections(d, lab)" in _fn("numbered")


def test_every_section_heading_carries_its_number():
    """A contents list that numbers what the headings do not is a worse
    document than one that numbers neither."""
    assert SRC.count("numbered(d, lab") >= 14, "both renderers, every section"
    svg = SRC[SRC.index("def _report_svg"):SRC.index("def _report_pdf")]
    pdf = SRC[SRC.index("def _report_pdf"):]
    for part, name in ((svg, "SVG"), (pdf, "PDF")):
        assert part.count("numbered(d, lab") >= 6, f"{name} misses numbered headings"


def test_an_absent_section_gets_no_number_rather_than_a_wrong_one():
    fn = _fn("numbered")
    assert "if title in order else title" in fn


def test_the_date_format_is_stated():
    """Dates were ISO and consistent, and the document never said so."""
    assert "ISO 8601" in SRC
    assert SRC.count('"m_dates"') >= 2, "both languages"
    assert "m_dates" in _fn("data_description")


def test_the_stated_dates_are_civil_effect_not_publication():
    """The distinction a professional checks: 2016-01-01 is when the merger took
    effect, not when INSEE printed it."""
    for phrase in ("took civil effect", "pris effet civil"):
        assert phrase in SRC


def test_a_density_carries_its_reference_geography_on_the_same_line():
    """A reader who copies the figure into their own file copies the line, not
    the document — so the basis must travel with the number."""
    fn = _fn("fact_lines")
    assert "f_density_on" in fn
    assert SRC.count('"f_density_on"') >= 2, "both languages"
