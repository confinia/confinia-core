"""Historical population (issue #88): the INSEE census series is HARMONISED on a
single reference geography, so we always say what the figures mean, and a code
that has disappeared — absent from the source — is routed to its living
successor. The fixture mirrors that shape: 99901 (alive) carries the series,
99902 (merged into 99901 in 2019) carries nothing, like the real file."""
import requests


def _pop(base, code, **params):
    r = requests.get(f"{base}/v1/communes/{code}/history",
                     params={"population": "true", **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_population_absent_unless_asked(base):
    d = requests.get(f"{base}/v1/communes/99901/history").json()
    assert "population" not in d          # opt-in only, no cost by default


def test_population_series_and_provenance(base):
    p = _pop(base, "99901")["population"]
    assert p["code"] == "99901"
    assert p["source"] == "insee-pop"
    assert p["harmonised_on"] == "2025-01-01"          # never hidden
    years = [x["year"] for x in p["series"]]
    assert years == sorted(years) and 1876 in years and 2023 in years
    assert p["series"][0]["population"] == 1520
    assert "harmonis" in p["note"].lower()             # the note explains the caveat
    assert "via_successor" not in p                    # the code is alive


def test_dead_code_routes_to_living_successor(base):
    # 99902 merged into 99901 in 2019: INSEE has no row for it (like the real
    # file), so we serve the successor's series AND flag the substitution.
    p = _pop(base, "99902")["population"]
    assert p["via_successor"] is True
    assert p["requested_code"] == "99902"
    assert p["code"] == "99901"
    assert len(p["series"]) == 4


def test_population_note_is_localized(base):
    fr = _pop(base, "99901", lang="fr")["population"]["note"]
    en = _pop(base, "99901", lang="en")["population"]["note"]
    assert "harmonisés" in fr and "géographie" in fr
    assert "harmonised" in en and "geography" in en


def test_ingester_documents_the_harmonisation():
    # The doctrine must stay written where the next maintainer will read it.
    import os
    src = open(os.path.join(os.path.dirname(__file__), "..", "ingestion",
                            "ingest_pop.py"), encoding="utf-8").read()
    assert "HARMONISED" in src and "harmonised_on" in src
    assert "U+FFFD" in src                              # repo doctrine on sources
