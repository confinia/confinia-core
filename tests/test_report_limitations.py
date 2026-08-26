"""What the report cannot tell you, stated in the report (issue #205).

The last substantive item of #205's structure list: "Limitations section,
explicit and unflattering [...] A report that states its limits is trusted
more, not less."

Distinct from "Not stated, and why", which lists facts we withheld. This lists
facts we DID state and the boundary of what they support -- and every line is
counted from the report in hand, never inferred.
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

from test_report_identity import bundle, labels          # one fixture, not two


def test_a_report_with_nothing_to_admit_still_states_its_cut_off():
    """Every fact in every report is bounded by the ingestion behind it, and
    silence about that reads as completeness."""
    d, lab = bundle(), labels()
    lines = m.limitation_lines(d, lab)
    assert lines, "the cut-off applies to every report"
    # Sentences spell their dates since #272; the cut-off is still the one
    # stated, just not in the form a machine would copy.
    assert m._d_en(d["cutoff"]) in lines[-1]
    assert "not evidence" in lines[-1], "absence must not read as proof"


def test_an_approximated_outline_says_which_edition_it_came_from():
    """Differencing an approximated geometry measures our approximation, not
    history -- the finding that blocked #127. A reader must be told which
    outline is which."""
    d, lab = bundle(), labels()
    d["versions"][0]["approx"] = True
    line = [l for l in m.limitation_lines(d, lab) if "approximated" in l]
    assert line, "an approximated outline must be admitted"
    assert m._d_en("2026-01-01") in line[0], "the edition it was taken from"
    assert "indicative" in line[0]


def test_periods_without_a_boundary_are_counted_not_implied():
    d, lab = bundle(), labels()
    d["versions"][0]["rings"] = []
    line = [l for l in m.limitation_lines(d, lab) if "no boundary" in l]
    assert line and "1 of 2" in line[0]
    assert "absent, not empty" in line[0]


def test_predecessors_we_could_not_draw_are_named_as_a_gap():
    """Colouring one absorbed commune and silently omitting three would be a
    confident, wrong picture -- the reason #127 declines to draw."""
    d, lab = bundle(), labels()
    d["versions"][1]["gained_undrawable"] = ["01176", "01292", "01409"]
    line = [l for l in m.limitation_lines(d, lab) if "predecessor" in l]
    assert line and "3" in line[0]
    assert "not drawn" in line[0]


def test_a_harmonised_population_says_it_is_not_what_was_counted():
    d, lab = bundle(), labels()
    d["population"] = {"geography_basis": "harmonised",
                       "harmonised_on": "2025-01-01", "series": [[1968, 900]]}
    line = [l for l in m.limitation_lines(d, lab) if "recomputed" in l]
    assert line and m._d_en("2025-01-01") in line[0]
    assert "not what was counted at the time" in line[0]


def test_a_population_at_the_boundaries_of_its_time_is_not_called_harmonised():
    """ISTAT publishes 'ai confini dell'epoca' -- the opposite of INSEE's
    harmonisation (#252). Saying the wrong one is worse than saying nothing."""
    d, lab = bundle(), labels()
    d["population"] = {"geography_basis": "as_at_the_time", "series": [[1961, 800]]}
    assert not [l for l in m.limitation_lines(d, lab) if "recomputed" in l]


def test_nothing_here_is_inferred():
    """#205 also asks for January-1st fallback dates. The schema carries no
    date precision, so claiming to know which dates are conventions would be
    the exact failure this section exists to prevent."""
    body = SRC.split("def limitation_lines(")[1].split("\ndef ")[0]
    head, _, rest = body.partition('"""')          # the docstring SAYS January;
    body = head + rest.partition('"""')[2]         # the code must not USE it
    assert "month" not in body and "January" not in body
    assert "01-01" not in body, "no date is guessed to be a convention"


def test_the_section_appears_only_when_it_has_something_to_say():
    d, lab = bundle(), labels()
    d["cutoff"] = None
    d["versions"] = []
    assert m.limitation_lines(d, lab) == []
    assert lab["limits"] not in m.report_sections(d, lab)


def test_it_is_a_numbered_section_in_both_renderers():
    d, lab = bundle(), labels()
    assert lab["limits"] in m.report_sections(d, lab)
    svg = m._report_svg(d)
    assert m.numbered(d, lab, lab["limits"]) in svg
    for part in (SRC[SRC.index("def _report_svg"):SRC.index("def _report_pdf")],
                 SRC[SRC.index("def _report_pdf"):]):
        assert 'limitation_lines(d, lab)' in part
        assert 'lab["limits"]' in part


def test_what_it_states_travels_into_the_document_reference():
    """A copy with the limits quietly removed must not keep the reference of
    the one that carried them."""
    d, lab = bundle(), labels()
    before = m.report_digest(d, lab)
    d["versions"][0]["approx"] = True
    assert m.report_digest(d, lab) != before


def test_both_languages_carry_every_limitation_phrase():
    for key in ("limits", "l_approx", "l_nogeom", "l_undrawable",
                "l_harmonised", "l_cutoff"):
        for lang in ("en", "fr"):
            assert labels(lang).get(key), f"{key} missing in {lang}"
