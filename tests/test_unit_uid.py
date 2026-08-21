"""A citable identifier is assigned and remembered, never derived.

The founder chose an opaque identifier over a composed one (`FR-01187-2016-01-01`)
for the right reason: a citable reference must never change, and a composed one
moves the day a start date is corrected.

Opacity alone does not deliver that, and both obvious implementations fail:

  - Derived from the row id. Ingestion runs `DELETE ... WHERE source = %s` and
    re-inserts, and `id` comes from a sequence -- every rebuild would mint
    different identifiers for the same history. Stable in appearance, unstable
    in fact, which is worse than an honestly unstable one.
  - Hashed from the natural key. Reproducible across rebuilds, but it moves
    exactly when a date is corrected: the composed form's weakness, hidden.

So it is assigned on first sight and stored in the ops database -- the one that
is backed up and never rebuilt from source.
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


def test_the_identifier_lives_where_the_data_is_not_rebuilt():
    """The geo database is a build artefact; the ops database is backed up."""
    fn = _fn("unit_uid")
    assert "ops_cursor()" in fn, "the ops database, not the rebuilt geo one"
    assert "CREATE TABLE IF NOT EXISTS public.unit_uid" in SRC


def test_it_is_never_derived_from_anything_that_can_move():
    fn = _fn("unit_uid")
    assert "id" not in fn.split("SELECT uid")[0].replace("valid_from", ""), \
        "no row id anywhere near the minting"
    assert "hashlib" not in fn and "md5" not in fn, "not hashed from the natural key"
    assert "secrets" in fn, "assigned at random, then remembered"


def test_two_concurrent_reports_cannot_mint_two_identifiers():
    """A commune must not end up with two citable references because two
    readers opened its report at the same moment."""
    fn = _fn("unit_uid")
    assert "ON CONFLICT DO NOTHING" in fn
    assert fn.count("SELECT uid") >= 2, "re-read after a losing insert"
    assert "UNIQUE (country, code, unit_type, valid_from)" in SRC


def test_the_natural_key_includes_unit_type():
    """A `commune` and a `lau` can share a code; two different things must never
    share one citable identifier."""
    assert "unit_uid(country, code, versions[-1][\"unit_type\"]" in SRC
    assert '"unit_type": utype,' in SRC, "carried from the query, not guessed"


def test_a_missing_identifier_never_breaks_the_report():
    """A gap is a gap; a 500 is an outage."""
    fn = _fn("unit_uid")
    assert "except Exception:" in fn and "return None" in fn


def test_the_alphabet_survives_being_read_aloud():
    assert "UID_ALPHABET" in SRC
    alpha = SRC.split('UID_ALPHABET = "')[1].split('"')[0]
    for ambiguous in "lio01":
        assert ambiguous not in alpha, f"{ambiguous!r} is misread when dictated"


def test_the_citation_prints_it():
    fn = _fn("citation_block")
    assert 'cfn:v1:' in fn, "namespaced, so it is recognisable out of context"
