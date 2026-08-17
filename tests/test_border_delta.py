"""Colouring what a boundary gained (issue #127).

The founder's decision: orange for what was lost, light blue for what was
gained. The issue's own measurements decide HOW, and they rule out the obvious
implementation.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
SRC = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()


def test_gained_area_comes_from_the_lineage_not_a_difference():
    """A naive ST_Difference returns slivers, and colour makes them assertions.

    Measured on Haut Valromey: 97 pieces each way, exactly one above 0.1 km²,
    the other 96 totalling 0.178 km². They are mismatched IGN vintages along a
    border that never moved. Painted orange and blue they would ring the whole
    commune and tell the reader it shifted everywhere.

    `parents` says which communes were absorbed -- that is the fact -- so each
    predecessor's own polygon is what gets coloured.
    """
    fn = SRC.split("def _gained_rings(")[1].split("\ndef ")[0]
    # Strip the docstring: it NAMES the approach the code must not take.
    body = fn.split('"""')[2] if fn.count('"""') >= 2 else fn
    assert "parents" in body, "the gained area must be driven by the lineage"
    assert "ST_Difference" not in body, \
        "differencing two vintages of one commune returns slivers, not a delta"
    assert "ST_Intersection" in body, "clipped to the frame, like the neighbours"


def test_predecessors_we_cannot_draw_are_named():
    """Three of Haut Valromey's four parents have no geometry.

    Colouring only what we hold would show "gained Ruffieu" and imply "and
    nothing else" -- a false statement in a document sold on per-fact
    provenance. The report must name them instead.
    """
    fn = SRC.split("def _gained_rings(")[1].split("\ndef ")[0]
    assert "undrawable" in fn, "the query must report what it could not draw"
    assert SRC.count('v["gained_undrawable"]') >= 2, \
        "both renderers must name the predecessors they could not draw"
    assert SRC.count('"gained_partial"') >= 2, \
        "the phrase must exist in both language tables"


def test_only_the_last_version_of_each_parent_is_drawn():
    """A commune has many versions; its perimeter at absorption is the one."""
    fn = SRC.split("def _gained_rings(")[1].split("\ndef ")[0]
    assert "ORDER BY code, valid_from DESC" in fn
    assert "if c in drawn" in fn, "later versions of the same parent must be skipped"
    # NOT valid_to: Ruffieu was absorbed in 2016 and its own record runs to
    # 2025, so filtering on the end of its existence dropped the one parent we
    # could actually draw.
    assert "valid_from <= %s" in fn, \
        "a parent's perimeter must be taken AT absorption, not at the end of its record"
    assert "valid_to <= %s" not in fn, \
        "registries disagree about when an absorbed commune stops existing"


def test_both_renderers_colour_the_gained_area():
    assert SRC.count('v.get("gained")') >= 3, \
        "SVG and PDF must both draw the absorbed area"
    # Light blue, per the founder's decision.
    assert "#a8d5f2" in SRC, "the SVG fill must be the agreed light blue"
    assert "setFillColorRGB(.66, .84, .95)" in SRC, "the PDF fill must match it"


def test_the_outline_stays_readable_over_the_fill():
    """A solid outline over a solid fill hides what was just coloured."""
    assert 'fill-opacity="0.55"' in SRC, "the SVG outline must let the fill show"
    assert "setFillAlpha(0.55)" in SRC and "setFillAlpha(1)" in SRC, \
        "the PDF must set alpha and reset it, or every later fill inherits it"
