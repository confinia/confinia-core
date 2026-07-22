"""Parcours INSCRIPTION (TEST_SUBSCRIPTION.md) : un nouvel utilisateur crée sa
clé, elle est active en palier free, le metering la compte, et le quota premium
gratuit s'épuise à la 10e requête (modèle fondateur : 9 offerts, 402 ensuite)."""
import uuid

import requests

EMAIL = f"ci-signup-{uuid.uuid4().hex[:8]}@test.confinia.io"


def test_healthz(base):
    d = requests.get(f"{base}/healthz").json()
    assert d["status"] == "ok"
    assert d["versions"] >= 3          # le jeu de test est chargé


def test_signup_creates_active_free_key(base):
    r = requests.post(f"{base}/v1/keys", json={"email": EMAIL, "note": "ci"})
    assert r.status_code == 201, r.text
    d = r.json()
    uuid.UUID(d["key"])                 # clé bien formée
    assert d["tier"] == "free"
    global KEY
    KEY = d["key"]


def test_invalid_email_rejected(base):
    r = requests.post(f"{base}/v1/keys", json={"email": "not-an-email"})
    assert r.status_code == 422


def test_key_is_metered(base):
    r = requests.get(f"{base}/v1/units",
                     params={"lat": 46.005, "lon": 5.005, "at": "2020-06-01",
                             "api_key": KEY})
    assert r.status_code == 200, r.text
    assert r.json()["properties"]["nom"] == "Testville"
    usage = requests.get(f"{base}/v1/keys/{KEY}/usage").json()
    assert usage["total_30d"] >= 1      # la requête ci-dessus est comptée


def test_history_exposes_merge_event(base):
    d = requests.get(f"{base}/v1/communes/99902/history").json()
    assert any(ev["type"] == "merged_into" and ev["date"] == "2019-01-01"
               for ev in d["events"])


def test_premium_quota_nine_free_then_402(base):
    quotas = []
    for _ in range(9):
        r = requests.get(f"{base}/v1/changes",
                         params={"bbox": "4.99,45.99,5.03,46.02",
                                 "api_key": KEY})
        assert r.status_code == 200, r.text
        quotas.append(r.json()["quota"]["remaining"])
        assert r.json()["events"], "le rapport doit contenir la fusion de test"
    assert quotas == list(range(8, -1, -1))     # 8, 7, … 0
    r = requests.get(f"{base}/v1/changes",
                     params={"bbox": "4.99,45.99,5.03,46.02", "api_key": KEY})
    assert r.status_code == 402                 # la 10e est payante
    assert "pricing" in r.json()["detail"]


def test_report_endpoints_serve_documents(base):
    email = f"ci-report-{uuid.uuid4().hex[:8]}@test.confinia.io"
    key = requests.post(f"{base}/v1/keys", json={"email": email}).json()["key"]
    r = requests.get(f"{base}/v1/communes/99901/report.svg",
                     params={"api_key": key})
    assert r.status_code == 200 and r.content.startswith(b"<svg")
    r = requests.get(f"{base}/v1/communes/99901/report.pdf",
                     params={"api_key": key})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
