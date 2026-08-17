"""The traceability annex (issue #90).

Provenance per fact is what this product sells. A flat footer names WHO the
sources are and nothing about which edition was read or where to check it, so a
reader has to take our word for everything.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load():
    src = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()
    ns = {}
    m = re.search(r"^def build_source_annex\(.*?(?=^def |\Z)", src, re.S | re.M)
    assert m, "build_source_annex not found"
    exec(m.group(0), ns)
    return ns["build_source_annex"], src


class _D:
    def __init__(self, iso): self._iso = iso
    def isoformat(self): return self._iso


def test_every_row_names_the_edition_that_was_read():
    """Never "latest": these files get republished.

    A reader verifying next year must land on what we read, not on what
    replaced it.
    """
    build, _ = _load()
    versions = [{"source": "insee-cog", "vintage": _D("2024-01-01")},
                {"source": "insee-cog", "vintage": _D("2026-01-01")}]
    registry = {"insee-cog": {"attribution": "INSEE, COG", "license": "Licence Ouverte 2.0",
                              "url": "https://insee.fr/cog"}}
    rows = build(versions, None, registry)
    assert len(rows) == 1
    assert rows[0]["vintages"] == ["2024-01-01", "2026-01-01"]
    assert rows[0]["gap"] is None


def test_a_missing_reference_is_stated_not_blanked():
    """A blank reads as an oversight; a named gap is information.

    Same doctrine as #167: state what the data cannot support rather than let
    the layout imply completeness.
    """
    build, _ = _load()
    rows = build([{"source": "mystery", "vintage": _D("2020-01-01")}], None, {})
    assert rows[0]["gap"], "an unregistered source must declare the gap"
    assert "registry" in rows[0]["gap"]

    rows = build([{"source": "x", "vintage": None}], None,
                 {"x": {"attribution": "X", "license": "CC BY", "url": "https://x"}})
    assert "edition not recorded" in rows[0]["gap"]

    rows = build([{"source": "y", "vintage": _D("2020-01-01")}], None,
                 {"y": {"attribution": "Y", "license": "CC BY", "url": None}})
    assert "no published reference" in rows[0]["gap"]


def test_only_the_sources_actually_used_are_listed():
    """An annex listing the whole catalogue proves nothing about THIS report."""
    build, _ = _load()
    registry = {k: {"attribution": k, "license": "L", "url": "u"} for k in ("a", "b", "c")}
    rows = build([{"source": "a", "vintage": _D("2020-01-01")}], None, registry)
    assert [r["source"] for r in rows] == ["a"]


def test_the_population_source_is_included():
    """The census series is a fact in the report and needs its provenance too."""
    build, _ = _load()
    registry = {"insee-pop": {"attribution": "INSEE census", "license": "LO", "url": "u"}}
    rows = build([], {"source": "insee-pop"}, registry)
    assert [r["source"] for r in rows] == ["insee-pop"]


def test_both_renderers_print_the_annex():
    """SVG and PDF must not disagree about what the report proves."""
    _, src = _load()
    assert src.count('lab["annex"]') == 2, "both renderers must print the annex heading"
    assert src.count('row["vintages"]') >= 2, "both must print the edition read"
    assert src.count('row.get("gap")') >= 2, "both must print an explicit gap"
    assert '"source_annex": build_source_annex(' in src, "the report must carry it"


def test_the_annex_is_localised():
    _, src = _load()
    for key in ('"annex"', '"annex_lead"', '"annex_cols"', '"annex_gap"', '"annex_nov"'):
        assert src.count(f"{key}:") == 2, f"{key} must exist in both language tables"
