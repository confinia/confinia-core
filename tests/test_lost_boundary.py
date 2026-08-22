"""What a boundary LOST, drawn from the lineage (issue #127, second half).

The gained half shipped first and set the method: never difference two
vintages of the same commune. Measured on Haut Valromey, ST_Difference returned
97 slivers each way, of which exactly one exceeded 0.1 km² — the rest came from
mismatched IGN vintages along a border that never moved. Painted, they ring the
whole commune and tell the reader it shifted everywhere.

So the lost half mirrors it: `children` names what left, and each departed
unit's own polygon is what gets coloured.
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


def test_the_lost_area_comes_from_the_lineage_not_from_a_difference():
    fn = _fn("_lost_rings")
    assert "children" in fn, "what left is a fact of the register"
    assert "ST_Difference" not in fn, "differencing returns slivers, not territory"


def test_it_takes_the_perimeter_the_unit_had_when_it_left():
    """A unit that left in 1973 and merged again in 2019 must not be drawn with
    a shape it never had while it belonged here."""
    fn = _fn("_lost_rings")
    assert "valid_from >= %s" in fn, "at or after separation"
    assert "valid_from ASC" in fn, "its FIRST version once detached"


def test_it_reports_what_it_could_not_draw():
    """Drawing a subset of what left, silently, is the failure this function
    exists to avoid — on a document sold for per-fact provenance."""
    fn = _fn("_lost_rings")
    assert "undrawable" in fn
    assert SRC.count("lost_undrawable") >= 3, "computed, and surfaced in both renderers"


def test_a_commune_is_never_its_own_successor():
    fn = _fn("_lost_rings")
    assert "if c != code" in fn


def test_both_renderers_draw_it_under_the_gained_area():
    """Where a unit left and another arrived in the same period, the kept
    outline must stay readable rather than two colours fighting."""
    svg = SRC[SRC.index("def _report_svg"):SRC.index("def _report_pdf")]
    pdf = SRC[SRC.index("def _report_pdf"):]
    for part in (svg, pdf):
        assert part.index('v.get("lost")') < part.index('v.get("gained")'), \
            "lost is painted first, so gained sits on top"


def test_the_founder_s_colours_are_used():
    """Orange for what was removed, light blue for what was added — decided by
    the founder, not by the renderer."""
    assert "#f2c49b" in SRC and "#d4813f" in SRC, "orange, SVG"
    assert "setFillColorRGB(.95, .77, .61)" in SRC, "orange, PDF"
    assert "#a8d5f2" in SRC, "the gained blue is unchanged"


def test_both_languages_carry_the_new_labels():
    for key in ("lost", "lost_partial"):
        assert SRC.count(f'"{key}":') >= 2, f"{key} missing from a language table"
