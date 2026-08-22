"""The three codebook sections a research-grade record has and ours lacked.

Taken from a real NHGIS codebook, which opens:

    Contents
        - Data Description
        - Data Summary
        - Data Dictionary
        - Median Computation & Special Codes
        - Suppression
        - Citation and Use

That document has no design at all -- plain monospaced text -- and is cited by
thousands of publications. Its authority comes from what it DECLARES, not from
how it looks: it states in the delivered document that a source table is
incorrect and what was substituted, it gives suppression its own section, and
it prints the full citation so a reader can copy it.

We already had the best of those: "Not stated, and why" is our Suppression, and
computed per fact rather than written once. These are the three we did not have.
"""
import os

SRC = open(os.path.join(os.path.dirname(__file__), "..", "api", "main.py"),
           encoding="utf-8").read()


def _fn(name):
    body = SRC.split(f"def {name}(")[1].split("\ndef ")[0]
    if '"""' in body:                       # a test that reads prose proves nothing
        head, _, rest = body.partition('"""')
        body = head + rest.partition('"""')[2]
    return body


def test_the_contents_list_describes_this_report_and_not_a_template():
    """A contents entry pointing at an absent section is worse than none."""
    # The contents list stopped deciding for itself when the sections were
    # numbered (#205): it and the headings now read one order, which is the
    # only way the two cannot disagree. So the conditions live there.
    assert "report_sections(d, lab)" in _fn("report_contents")
    fn = _fn("report_sections")
    assert "fact_lines(d, lab)" in fn and "declined_lines(d)" in fn
    assert 'd.get("events")' in fn and 'd.get("source_annex")' in fn
    assert "if " in fn, "entries are conditional on the section existing"


def test_the_method_note_states_what_was_done_to_the_data():
    fn = _fn("data_description")
    assert "BOUNDARY_NOISE_PCT" in fn, "what counts as a change rather than noise"
    assert "harmonised_on" in fn, "what a population figure actually means"
    assert 'm_area' in fn, "which geometry the areas were measured on"


def test_the_method_note_only_says_what_applies_here():
    """Boilerplate that mentions harmonisation for a commune with no population
    series teaches the reader to skip the section."""
    fn = _fn("data_description")
    # Not anchored on the exact condition: a population series now also
    # declares the geography it was counted on (#252), so the test states what
    # must be true -- the line is conditional on this report having one.
    assert 'pop.get("harmonised_on")' in fn and "if " in fn
    assert 'f.get("formed_from") or f.get("absorbed")' in fn


def test_the_citation_carries_what_makes_it_citable():
    fn = _fn("citation_block")
    assert "APP_VERSION" in fn, "which version produced this"
    assert "www.confinia.io/commune" in fn, "and where it can be fetched again"
    # quote style differs inside f-strings; assert the fields, not the quoting
    assert "code" in fn and "country" in fn


def test_all_three_are_built_once_and_read_by_both_renderers():
    """When the boundary panels were annotated per renderer, the two formats
    disagreed and the PDF kept drawing a panel per version."""
    for fn in ("report_contents(d, lab)", "data_description(d, lab)",
               "citation_block(d, lab)"):
        assert SRC.count(fn) == 2, f"{fn} must be called by exactly both renderers"


def test_both_languages_carry_every_new_label():
    for key in ("contents", "method", "cite", "cite_as", "m_area", "m_noise",
                "m_pop", "m_lineage", "m_approx"):
        assert SRC.count(f'"{key}":') >= 2, f"{key} missing from a language table"


def test_the_french_decimal_hack_does_not_eat_the_full_stop():
    """`.replace(".", ",", 1)` on the whole sentence replaced the FIRST period,
    which is the one that ends it: the rendered line read "...n'est trace,".
    The decimal separator is the caller's job, and it already passes "0,5"."""
    fr = SRC.split('"m_noise": lambda pct:')[1].split("),")[0]
    assert '.replace(".", ","' not in fr, "no blanket period replacement in the sentence"
    assert "n'est tracé." in SRC, "the sentence ends with a full stop"
