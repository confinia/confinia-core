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
