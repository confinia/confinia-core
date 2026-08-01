"""Passage tables (issue #21): a value expressed in a source vintage maps to
the unit(s) covering the same territory at a target date, with weights. The
fixture has Testville-A (99901) + Testville-B (99902) merging into Testville
(99901) in 2019, so both pre-merger codes map to 99901 at a 2020 target."""
import requests


def test_split_source_maps_to_successor(base):
    r = requests.get(f"{base}/v1/passage",
                     params={"code": "99902", "from": "2015-06-01", "to": "2020-06-01"})
    assert r.status_code == 200, r.text
    d = r.json()
    codes = {t["code"]: t["weight"] for t in d["targets"]}
    assert "99901" in codes
    assert abs(sum(codes.values()) - 1.0) < 1e-6         # weights normalized
    assert codes["99901"] > 0.9                          # B is fully inside Testville


def test_unknown_source_404(base):
    r = requests.get(f"{base}/v1/passage",
                     params={"code": "00000", "from": "2015-06-01", "to": "2020-06-01"})
    assert r.status_code == 404


# --- Population weighting (issue #94) ---------------------------------------
# Method confirmed by Kim Antunez (COGugaison) 2026-08-01: the weights are the
# municipal populations of the communes RESULTING from the split, at the FIRST
# census following it. The fixture split has equal halves but 300/700 people,
# so area and population weightings must give visibly different answers.

def _passage(base, **params):
    r = requests.get(f"{base}/v1/passage", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_area_weighting_is_still_the_default(base):
    d = _passage(base, code="99910", **{"from": "1990-01-01", "to": "2020-01-01"})
    assert d["weighting"] == "area"
    w = {t["code"]: t["weight"] for t in d["targets"]}
    assert abs(w["99911"] - 0.5) < 0.02 and abs(w["99912"] - 0.5) < 0.02
    assert "COGugaison" in d["note"]


def test_population_weighting_uses_the_first_census_after_the_split(base):
    d = _passage(base, code="99910", weighting="population",
                 **{"from": "1990-01-01", "to": "2020-01-01"})
    assert d["weighting"] == "population"
    assert d["census_year"] == 2006          # the split is 2000, not 2023
    w = {t["code"]: t["weight"] for t in d["targets"]}
    assert abs(w["99911"] - 0.3) < 0.001     # 300 / 1000
    assert abs(w["99912"] - 0.7) < 0.001     # 700 / 1000
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert "first census following the split" in d["note"]


def test_merger_needs_no_apportionment(base):
    # 99902 merged into 99901: the successor takes everything, weight 1.
    d = _passage(base, code="99902", weighting="population",
                 **{"from": "2015-06-01", "to": "2020-06-01"})
    assert len(d["targets"]) == 1 and d["targets"][0]["code"] == "99901"
    assert abs(d["targets"][0]["weight"] - 1.0) < 1e-6


def test_fallback_to_area_is_stated_never_silent(base):
    # 99901/99902 have no census row on or after their 2019 merger, so a
    # population request must fall back AND say why.
    d = _passage(base, code="99901", weighting="population",
                 **{"from": "1950-01-01", "to": "2020-06-01"})
    assert d["weighting_requested"] == "population"
    if d["weighting"] == "area":
        assert "not applied" in d["note"]    # the substitution is visible


def test_note_never_promises_a_closed_issue(base):
    # The old note advertised "planned (issue #21)", an issue already closed.
    d = _passage(base, code="99910", **{"from": "1990-01-01", "to": "2020-01-01"})
    assert "planned" not in d["note"] and "issue #21" not in d["note"]
