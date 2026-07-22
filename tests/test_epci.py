"""EPCI as a unit level (issue #5): the fixture EPCI is served through the OHM
export with its banatic provenance."""
import requests


def test_epci_in_ohm_export(base):
    r = requests.get(f"{base}/v1/export/ohm",
                     params={"country": "FR", "unit_type": "epci", "limit": 5})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["count"] >= 1
    p = d["features"][0]["properties"]
    assert p["name"] == "CC de Testville"
    assert p["unit_type"] == "epci"


def test_banatic_in_attributions(base):
    a = requests.get(f"{base}/v1/attributions").json()
    assert any(s["source"] == "banatic" for s in a["sources"])
