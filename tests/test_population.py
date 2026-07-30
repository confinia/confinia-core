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


# --- The curve in the premium report (issue #88, phase 3) --------------------
# Re-downloading the same town is free (issue #83), so these calls cost one unit.

def test_report_svg_draws_the_curve_with_events(base):
    svg = requests.get(f"{base}/v1/communes/99901/report.svg").text
    assert "Population dans le temps" in svg            # FR by default for FR
    assert "stroke-dasharray" in svg                    # the dated-event markers
    assert ">2019<" in svg                              # the merger year, on the axis
    # provenance is never hidden
    assert "harmonisés sur la géographie du 2025-01-01" in svg
    # the census source joins the report's attribution block
    assert "Recensement de la population" in svg


def test_report_svg_flags_the_successor_substitution(base):
    svg = requests.get(f"{base}/v1/communes/99902/report.svg").text
    assert "successeur de ce code" in svg


def test_report_curve_is_localized(base):
    svg = requests.get(f"{base}/v1/communes/99901/report.svg", params={"lang": "en"}).text
    assert "Population through time" in svg
    assert "harmonised on the geography of 2025-01-01" in svg
    assert "Population dans le temps" not in svg


def test_report_pdf_still_valid_with_the_curve(base):
    r = requests.get(f"{base}/v1/communes/99901/report.pdf")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF") and len(r.content) > 2000


def test_commune_page_wires_the_population_chart():
    import os
    html = open(os.path.join(os.path.dirname(__file__), "..", "deploy", "site",
                             "commune.html"), encoding="utf-8").read()
    assert "population=true" in html                    # the series is requested
    assert 'id="pop-h"' in html and 'id="pop-note"' in html
    assert "stroke-dasharray" in html                   # dated-event markers
    assert "Population dans le temps" in html and "Population through time" in html
    assert "popHarmonised" in html                      # provenance shown, not hidden
