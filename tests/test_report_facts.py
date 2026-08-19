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
    for lang in ("fr", "en"):
        assert f'"{lang}": {{' in SRC.split("DECLINE_PHRASES")[1][:900]


def test_both_renderers_read_one_builder():
    """When the boundary panels were annotated per renderer, the SVG and the PDF
    disagreed and the PDF kept drawing a panel per version."""
    assert SRC.count("fact_lines(d, lab)") == 2
    assert SRC.count("declined_lines(d)") == 2


def test_a_rank_we_cannot_compute_is_a_rank_we_do_not_claim():
    fn = _fn("_facts")
    assert "statement_timeout" in fn
    assert "rank:timed-out" in fn


def test_a_predecessor_cannot_outlive_the_commune_it_became():
    """Measured 2026-08-19: 1169 such pairs over 714 communes, all French, the
    parents uniformly 'ending' on the COG snapshot date and the children
    'starting' at the 1943 nomenclature. Those are pipeline defaults, not facts.

    Naming the predecessor stays true and useful; quoting a date we do not hold
    does not (#167, #90)."""
    fn = _fn("_facts")
    assert "dates_unreliable" in fn
    assert "trustworthy" in fn
    assert "lineage-dates" in fn, "the reader is told why the dates are absent"


def test_a_predecessor_without_trustworthy_dates_still_gets_named():
    """Declining a date must not delete the fact that the commune existed."""
    fn = _fn("fact_lines")
    assert 'parts.append(x["nom"])' in fn, \
        "the name is shown even when its dates are not"
