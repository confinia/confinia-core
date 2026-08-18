"""The situation inset: where the commune sits in its country (report locator).

A professional document situates its subject on a national map; the commune
report never did. The inset is built from the country outline (union of its
current units, cached) plus the commune's position.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
SRC = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()


def test_the_country_outline_is_cached():
    """The union is the one genuinely expensive query in a report, and a country
    border does not move between two reports."""
    fn = SRC.split("def country_outline(")[1].split("\ndef ")[0]
    assert "_COUNTRY_OUTLINE" in fn and "in _COUNTRY_OUTLINE" in fn, \
        "the outline must be memoised across reports"
    # nuts0 first: unioning a country's communes at request time timed the PDF
    # out (France is 35 000 geometries). The single national polygon is ~16 ms.
    assert "unit_type = 'nuts0'" in fn, "the cheap national polygon must be tried first"
    assert fn.index("nuts0") < fn.index("ST_Union"), \
        "the union is only the fallback for a country without nuts0"


def test_the_locator_declines_when_it_cannot_place_the_unit():
    """A country drawn with no marker is worse than no inset -- the #167 rule."""
    fn = SRC.split("def _locator(")[1].split("\ndef ")[0]
    assert "return None" in fn
    assert "if not rings" in fn, "no national outline -> no inset, not a blank country"
    assert "if not bbox" in fn, "no commune position -> no inset"


def test_both_renderers_draw_the_inset_and_the_marker():
    """SVG and PDF must not disagree about where the commune is."""
    assert SRC.count('d.get("locator")') >= 2, "both renderers must read the locator"
    assert SRC.count('loc.get("country_rings")') >= 2
    # the marker is what makes it a locator, not just a country shape
    assert 'loc["marker"]' in SRC
    assert SRC.count("_ring_points([loc[\"marker\"]]") == 2, \
        "both renderers must project the marker point the same way"


def test_the_inset_is_country_agnostic():
    """The report serves FR, DE, NL, NZ, IT -- the outline is unioned per
    country, never hardcoded to France."""
    fn = SRC.split("def country_outline(")[1].split("\ndef ")[0]
    assert "country = %s" in fn, "the outline query must be parameterised by country"
    assert '"FR"' not in fn and "'FR'" not in fn, "no country may be hardcoded"


def test_the_locator_is_attached_to_the_report_data():
    assert '"locator": locator,' in SRC
    assert "_locator(cur, country, bbox)" in SRC
