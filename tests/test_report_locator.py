"""The situation inset: where the commune sits in its country (report locator).

A professional document situates its subject on a national map; the commune
report never did. Two things had to be right before it read as a map at all,
and both were found by rendering it rather than reading the markup:

  - it must show the LANDMASS the commune is on, not the whole territory.
    France's nuts0 spans the globe (Guadeloupe 63W to Reunion 56E), so drawing
    all of it shrank metropolitan France to an invisible speck.
  - the silhouette must be dark enough to see. The first fill (#eef2f7) was
    near-white on white -- a red dot floating in nothing.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
SRC = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()


def test_the_inset_shows_the_landmass_the_commune_is_on():
    """Not the whole national territory -- France's overseas parts span the
    globe and would shrink the mainland to a speck."""
    fn = SRC.split("def _locator(")[1].split("\ndef ")[0]
    assert "ST_Dump" in fn, "the national polygon must be split into its parts"
    assert "ST_Contains" in fn, "the part CONTAINING the commune wins"
    assert "ST_Area(g) DESC" in fn, "largest part is the fallback when none contains it"
    assert "unit_type = 'nuts0'" in fn, "the national outline is the pre-computed nuts0"


def test_the_locator_declines_when_it_cannot_place_the_unit():
    """A country drawn with no marker is worse than no inset -- the #167 rule."""
    fn = SRC.split("def _locator(")[1].split("\ndef ")[0]
    assert fn.count("return None") >= 2, "no bbox and no polygon must both decline"
    assert "if not bbox" in fn
    assert "if not rings" in fn


def test_the_silhouette_is_dark_enough_to_see():
    """#eef2f7 was near-white on white; the marker floated in nothing."""
    assert "#eef2f7" not in SRC, "the near-invisible fill must be gone"
    assert "#ccd6e6" in SRC, "the SVG silhouette must use a visible fill"
    # the PDF fill, well below white
    assert "setFillColorRGB(.80, .84, .90)" in SRC, "the PDF fill must be visibly grey"


def test_both_renderers_draw_the_inset_and_the_marker():
    assert SRC.count('d.get("locator")') >= 2, "both renderers must read the locator"
    # The marker is projected inside the shared draw helper (one per format), so
    # the country and district insets place their markers identically.
    assert SRC.count("_ring_points([marker]") == 2, \
        "each format's draw helper must project the marker the same way"


def test_the_inset_is_country_agnostic():
    fn = SRC.split("def _locator(")[1].split("\ndef ")[0]
    assert "country = %s" in fn, "the outline query must be parameterised by country"
    assert '"FR"' not in fn and "'FR'" not in fn, "no country may be hardcoded"


def test_the_locator_is_attached_to_the_report_data():
    assert '"locator": locator,' in SRC
    assert "_locator(cur, country, bbox)" in SRC


def test_the_district_inset_is_the_containing_nuts3():
    """The intermediate zoom -- departement in France, Kreis in Germany.

    Found the same way as the landmass, by the nuts3 polygon that CONTAINS the
    commune (a 0.35 ms indexed point lookup), labelled with its own name. Ain
    for Haut Valromey, not a country code.
    """
    fn = SRC.split("def _district(")[1].split("\ndef ")[0]
    assert "unit_type = 'nuts3'" in fn, "the district is the nuts3 level"
    assert "ST_Contains" in fn, "the nuts3 CONTAINING the commune"
    assert "nom" in fn, "the district's own name labels the inset"
    assert "'FR'" not in fn and '"FR"' not in fn, "country-agnostic"
    assert fn.count("return None") >= 2, "declines when it cannot place the unit"


def test_both_renderers_draw_both_insets_via_one_helper():
    """Country and district must not disagree about projection or style."""
    assert 'd.get("district")' in SRC and SRC.count('d.get("district")') >= 2
    assert "def draw_inset(" in SRC, "the SVG uses one helper for both insets"
    assert "def draw_inset_pdf(" in SRC, "the PDF uses one helper for both insets"
    assert '"district": district,' in SRC
