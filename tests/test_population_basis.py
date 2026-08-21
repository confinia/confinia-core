"""A population figure must say which geography it counts (issue #91 spike).

The spike's first question, and it changed the design exactly as #88's had:

    INSEE  harmonises onto one reference geography (2025). A figure is how many
           people lived inside TODAY's territory at that census.
    ISTAT  publishes "ai confini dell'epoca" -- the commune as it then stood.
           A step at a merger is territory changing hands, not people arriving.

They are opposite claims. Until this, the report asserted the French reading
over any series we might hold, naming INSEE while doing it -- so an Italian
curve would have arrived carrying a sentence that was simply false.
"""
import os

SRC = open(os.path.join(os.path.dirname(__file__), "..", "api", "main.py"),
           encoding="utf-8").read()


def test_the_note_has_a_wording_per_basis():
    assert '"harmonised": {' in SRC and '"as_at_the_time": {' in SRC
    assert '"unknown": {' in SRC, "not knowing is a third state, not a default"
    for basis in ("harmonised", "as_at_the_time", "unknown"):
        block = SRC.split(f'"{basis}": {{')[1][:900]
        assert '"en"' in block and '"fr"' in block, f"{basis} needs both languages"


def test_it_no_longer_names_one_institute_for_every_series():
    note = SRC.split("POP_NOTE = {")[1].split("\n}")[0]
    assert "INSEE" not in note, "the note applies to whatever series we hold"


def test_null_keeps_meaning_unknown():
    """It must not quietly come to mean 'at the boundaries of the time', which
    is a claim rather than an absence."""
    fn = SRC.split("stored_basis = rows[0][4]")[1][:300]
    assert '"harmonised" if harmonised_on else "unknown"' in fn


def test_a_stored_basis_wins_over_the_inference():
    fn = SRC.split("stored_basis = rows[0][4]")[1][:300]
    assert "stored_basis if stored_basis in POP_NOTE" in fn


def test_density_declines_for_the_reason_that_is_true():
    """'Harmonised elsewhere' is wrong for a series that is not harmonised at
    all, and a wrong reason on the page is its own small untruth."""
    fn = SRC.split("def _facts(")[1].split("\ndef ")[0]
    assert 'basis == "as_at_the_time"' in fn
    assert "density:population-at-historical-boundaries" in fn
    assert "density:population-basis-unknown" in fn
    for lang_marker in ("les diviser par la superficie actuelle",
                        "would mix two territories"):
        assert lang_marker in SRC, "both languages explain it"


def test_the_method_note_follows_the_basis():
    fn = SRC.split("def data_description(")[1].split("\ndef ")[0]
    assert 'geography_basis") == "harmonised"' in fn
    assert 'geography_basis") == "as_at_the_time"' in fn
    assert "m_pop_unknown" in fn


def test_the_column_is_added_by_a_migration_not_by_the_api():
    """commune_population lives in the GEO database; the API's schema block runs
    against OPS, where an ALTER on it would fail at startup."""
    assert "ALTER TABLE public.commune_population" not in SRC
    mig = open(os.path.join(os.path.dirname(__file__), "..", "deploy", "migrations",
                            "002-population-geography-basis.sql"), encoding="utf-8").read()
    assert "ADD COLUMN IF NOT EXISTS geography_basis" in mig
