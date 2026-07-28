"""Language coherency (issue #79): French is the default for France, English is
the fallback, and an explicit `lang` always wins. The fixture has Testville-A
(99901) renamed to Testville in 2019 while absorbing Testville-B (99902), so the
chronology of 99901 carries a localizable "absorbed / a absorbé" event."""
import requests


def test_fr_history_defaults_to_french(base):
    r = requests.get(f"{base}/v1/communes/99901/history")
    assert r.status_code == 200, r.text
    details = " ".join(ev["detail"] for ev in r.json()["events"])
    assert "a absorbé" in details          # French by default for FR units
    assert "absorbed" not in details


def test_history_lang_override_to_english(base):
    r = requests.get(f"{base}/v1/communes/99901/history", params={"lang": "en"})
    assert r.status_code == 200, r.text
    details = " ".join(ev["detail"] for ev in r.json()["events"])
    assert "absorbed" in details           # explicit choice wins over the FR default
    assert "a absorbé" not in details


def test_report_svg_defaults_to_french_for_fr(base):
    r = requests.get(f"{base}/v1/communes/99901/report.svg")
    assert r.status_code == 200, r.text
    svg = r.text
    assert "Chronologie" in svg
    assert "fiche communale" in svg
    assert "Chronology / chronologie" not in svg   # dual label is gone


def test_report_svg_lang_override_to_english(base):
    r = requests.get(f"{base}/v1/communes/99901/report.svg", params={"lang": "en"})
    assert r.status_code == 200, r.text
    svg = r.text
    assert "Chronology" in svg
    assert "commune record" in svg
    assert "Chronologie" not in svg


def test_unsupported_lang_falls_back_to_default(base):
    # An unsupported language collapses to the country default (French for FR).
    r = requests.get(f"{base}/v1/communes/99901/report.svg", params={"lang": "de"})
    assert r.status_code == 200, r.text
    assert "Chronologie" in r.text
