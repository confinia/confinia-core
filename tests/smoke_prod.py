"""Post-deployment smoke suite (RULES 6): one live check per shipped issue,
run against a REAL deployment after every promotion. Read-only.

    TEST_API_BASE=https://staging.api.confinia.io \
        BASIC="confinia:PASS" pytest -q tests/smoke_prod.py   # staging
    TEST_API_BASE=https://api.confinia.io pytest -q tests/smoke_prod.py  # prod

Each test names the issue it guards. A failure here means a deployment
regressed a shipped capability.
"""
import os

import requests

BASE = os.environ.get("TEST_API_BASE", "https://api.confinia.io").rstrip("/")
AUTH = tuple(os.environ["BASIC"].split(":", 1)) if os.environ.get("BASIC") else None
S = requests.Session()
if AUTH:
    S.auth = AUTH


def get(path, **params):
    return S.get(f"{BASE}{path}", params=params, timeout=30)


def test_health_and_version():
    d = get("/healthz").json()
    assert d["status"] == "ok" and d["versions"] > 100000


def test_units_point():                       # core lookup
    d = get("/v1/units", lat=48.85, lon=2.35, at="2020-06-01").json()
    assert d["properties"]["country"] == "FR"


def test_nz_countries():                       # issue #1/#2
    codes = [f["properties"]["code"] for f in get("/v1/countries").json()["features"]]
    assert "NZ" in codes


def test_ohm_export_departement():             # issue #3
    d = get("/v1/export/ohm", country="FR", unit_type="departement",
            **{"to": "1941-01-01"}, limit=2).json()
    assert d["features"] and d["features"][0]["properties"]["start_date"]


def test_trf_supra_cantons():                  # issue #4
    d = get("/v1/export/ohm", country="FR", unit_type="canton",
            **{"from": "1900-01-01", "to": "1901-01-01"}, limit=1).json()
    assert d["count"] >= 1


def test_epci_served():                        # issue #5
    d = get("/v1/export/ohm", country="FR", unit_type="epci", limit=1).json()
    assert d["count"] >= 1 and d["features"][0]["properties"]["name"]


def test_commune_report_pdf():                 # issue #14
    r = get("/v1/communes/01033/report.pdf")
    assert r.status_code in (200, 402)         # served or quota-gated (both prove it exists)


def test_passage_table():                      # issue #21
    d = get("/v1/passage", code="01091", **{"from": "2018-06-01", "to": "2020-06-01"}).json()
    assert any(t["code"] == "01033" for t in d["targets"])


def test_attributions_registry():
    a = get("/v1/attributions").json()
    assert any(s["source"] == "banatic" for s in a["sources"])   # #5 source registered


def test_demo_has_auth_buttons():              # issue #43 (site surface, not API)
    import requests as _rq
    for url in ("https://www.confinia.io/", "https://www.confinia.io/account.html"):
        html = _rq.get(url, timeout=30).text
        assert "account.html" in html or "Create account" in html, url
