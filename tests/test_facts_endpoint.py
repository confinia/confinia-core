"""The facts as JSON, for a consumer that composes its own document.

EcoBuilding renders its own building report and must keep our provenance intact
while doing it. The report and this endpoint therefore read ONE bundle: two
paths that computed facts separately would eventually disagree about what is
true, and the disagreement would surface as a building report contradicting the
commune record it quotes.
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


def test_it_recomputes_nothing():
    """The report renders these facts; this returns them. One source."""
    fn = _fn("commune_facts")
    assert "_report_data(code, country, lang)" in fn
    assert "cur.execute" not in fn and "ST_" not in fn


def test_a_declined_fact_is_a_list_entry_not_an_absence():
    """A consumer receiving no `rank` cannot tell "we never compute rank" from
    "this rank could not be established". That difference is the product."""
    fn = _fn("commune_facts")
    assert '"declined": [' in fn
    assert '"reason": r' in fn and '"text": phrases.get(r)' in fn, \
        "a stable machine key AND the sentence in the reader's language"


def test_the_limitations_travel_with_the_facts():
    """A building report that repeats our numbers without our caveats states
    more than we do."""
    assert '"limitations": limitation_lines(d, lab)' in _fn("commune_facts")


def test_every_source_carries_the_vintage_that_was_read():
    """A caller verifying next year must land on what we read, not on what
    replaced it."""
    fn = _fn("commune_facts")
    assert '"sources": d.get("source_annex")' in fn
    annex = _fn("build_source_annex")
    assert '"vintages"' in annex


def test_the_attribution_travels_too():
    """Whoever displays the data owes the credit; a consumer cannot honour a
    licence it was never told about."""
    assert '"attribution"' in _fn("commune_facts")


def test_geometry_is_not_in_the_payload():
    """Heavy, already served by /v1/communes, and a consumer wanting an outline
    wants it in a map."""
    fn = _fn("commune_facts")
    assert '"rings": ' not in fn, "an outline must never be emitted as a value"
    assert '"geometry"' not in fn
    assert '"has_geometry": bool(v["rings"])' in fn, "say whether one exists, not what it is"


def test_it_costs_the_same_quota_unit_as_the_report():
    """Same value, two encodings. A consumer must not pay twice for a town, and
    a partner key must cover both."""
    fn = _fn("commune_facts")
    assert 'premium_gate(request, f"{country}/{code}")' in fn
    assert fn.index("premium_gate") < fn.index("_report_data"), \
        "gate before the expensive query"


def test_the_record_reference_is_exposed_so_a_consumer_can_cite_us():
    fn = _fn("commune_facts")
    assert '"uid": d.get("uid")' in fn
    assert 'cfn:v1:' in fn


def test_the_cut_off_travels_so_absence_can_be_read_correctly():
    """Without it a consumer cannot tell a missing event from a not-yet-
    published one -- and would present our silence as completeness."""
    assert '"as_known_on": d.get("cutoff")' in _fn("commune_facts")


def test_the_language_is_resolved_the_same_way_as_everywhere_else():
    assert "resolve_lang(lang, country)" in _fn("commune_facts")
