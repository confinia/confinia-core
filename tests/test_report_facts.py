"""The key-facts block: what the report held and never showed (issue #193).

The report said four things -- a chronology, one outline per boundary, a
population curve where we have one, and the annex. For a commune with no
history that is a page with a map on it.

Four defects showed up only on rendering the result, and each is guarded here,
because reading the markup would have missed all four:

  - Haut Valromey listed ITSELF among its own parents. The code survives a
    merger (Hotonnes kept 01187), so it sits in its own lineage array.
  - "des 388 communes de Ain" -- French articles, on a document a professional
    signs. Departement names take every article there is; a Landkreis takes
    none.
  - Bad Berneck reported "+0.1 %" beside "boundary never changed": two
    contradictory statements on one page. The 0.1 % is re-digitisation noise.
  - The lineage was cut mid-name at "Le Petit-Abergement (1943-01-01 ->".
"""
import importlib.util
import os

HERE = os.path.dirname(__file__)
SRC = open(os.path.join(HERE, "..", "api", "main.py"), encoding="utf-8").read()


def _fn(name: str) -> str:
    body = SRC.split(f"def {name}(")[1].split("\ndef ")[0]
    # Strip the docstring: a test that reads prose proves nothing about code.
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


def test_a_commune_is_never_its_own_predecessor():
    fn = _fn("_facts")
    assert "if c != code" in fn, \
        "the commune's own code must be filtered out of parents and children"


def test_an_area_change_inside_the_noise_threshold_is_not_reported_as_change():
    """Otherwise the page says '+0.1 %' and 'boundary never changed' at once."""
    fn = _fn("_facts")
    assert "BOUNDARY_NOISE_PCT" in fn, \
        "the area delta must use the same threshold the panels are grouped by"
    assert "prev, delta = None, None" in fn


def test_the_rank_sentence_needs_no_grammatical_article():
    """l'Ain, la Savoie, le Rhone, les Yvelines -- and a Landkreis takes none."""
    for frag in ('f"largest of {n} — {d}"', 'f"la plus étendue des {n} — {d}"'):
        assert frag in SRC, "the district name must sit apart, not inside a phrase"
    assert "communes de {d}" not in SRC and "communes of {d}" not in SRC


def test_a_long_fact_wraps_and_never_stops_mid_name():
    spec = importlib.util.spec_from_file_location("m", os.path.join(HERE, "..", "api", "main.py"))
    assert "def _wrap(" in SRC
    fn = _fn("_wrap")
    assert "value.split(\" \")" in fn, "breaks on separators, never mid-token"
    assert "[:96]" not in SRC.split("def fact_lines(")[0][-2000:], \
        "the renderers must wrap, not truncate"


def test_density_is_refused_unless_population_and_area_share_a_territory():
    """A census series is harmonised on ONE geography date. A density against
    another version's area, or an approximate one, is a number with no meaning."""
    fn = _fn("_facts")
    assert "same_territory" in fn
    assert "harmonised_on" in fn
    assert 'density:area-approximate' in fn
    assert 'density:population-harmonised-elsewhere' in fn


def test_a_declined_fact_is_explained_in_the_readers_language():
    assert "DECLINE_PHRASES" in SRC
    block = SRC.split("DECLINE_PHRASES", 1)[1].split("\n}", 1)[0]
    for lang in ("fr", "en"):
        assert f'"{lang}": {{' in block, f"{lang} has no declined phrases"


def test_both_renderers_read_one_builder():
    """When the boundary panels were annotated per renderer, the SVG and the PDF
    disagreed and the PDF kept drawing a panel per version."""
    for part, name in zip(_renderers(), ("SVG", "PDF")):
        assert "fact_lines(d, lab)" in part, f"{name} builds its own key facts"
        assert "declined_lines(d)" in part, f"{name} builds its own declines"


def test_a_rank_we_cannot_compute_is_a_rank_we_do_not_claim():
    fn = _fn("_facts")
    assert "statement_timeout" in fn
    assert "rank:timed-out" in fn


def test_forming_a_commune_and_being_absorbed_by_it_are_two_facts():
    """One label covered both, and it made Haut Valromey -- created in 2016 --
    claim it was 'formed from' a commune that lived until 2025.

    A predecessor whose life ends exactly when this version starts helped FORM
    it. One that ends during this version's life was ABSORBED later: the
    absorber's code and name do not change, so no new version is minted, which
    the ingest models deliberately ("Coupy -> Bellegarde in 1971, with no
    version end"). Measured: 3396 links of the first kind, 1265 of the second.
    """
    fn = _fn("_facts")
    assert "absorbed_later" in fn
    assert 'out["absorbed"]' in fn and 'out["formed_from"]' in fn
    assert "f_absorbed" in SRC, "the second fact needs its own label"


def test_the_dates_are_stated_because_they_are_real():
    """They were briefly suppressed on a false reading of the data: 762 distinct
    end dates exist, including mid-year ones like 1973-07-01, which no pipeline
    default would produce. Nothing here may hide them again."""
    assert "dates_unreliable" not in SRC
    assert "lineage-dates" not in SRC
    fn = _fn("fact_lines")
    assert "x['from']" in fn and "x['to']" in fn
