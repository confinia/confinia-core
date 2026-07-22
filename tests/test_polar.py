"""Parcours COMPTE PRO (TEST_POLAR.md) : un utilisateur s'abonne chez Polar
(simulé par des webhooks SIGNÉS, mêmes maths que la production), son palier est
appliqué à ses clés existantes ET futures, le premium devient illimité, et la
résiliation le ramène en free. Les webhooks non signés sont refusés."""
import base64
import hashlib
import hmac
import json
import time
import uuid

import requests

from conftest import PRODUCT_ENTERPRISE, PRODUCT_PRO, WEBHOOK_SECRET

EMAIL = f"ci-polar-{uuid.uuid4().hex[:8]}@test.confinia.io"


def _signed_headers(body: bytes) -> dict:
    secret = WEBHOOK_SECRET.removeprefix("whsec_")
    key = base64.b64decode(secret + "=" * (-len(secret) % 4))
    mid, ts = f"msg_{uuid.uuid4().hex[:10]}", str(int(time.time()))
    sig = base64.b64encode(
        hmac.new(key, f"{mid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return {"content-type": "application/json", "webhook-id": mid,
            "webhook-timestamp": ts, "webhook-signature": f"v1,{sig}"}


def _subscription_event(etype, sub_id, email, product_id, status):
    return json.dumps({"type": etype, "data": {
        "id": sub_id, "status": status, "product_id": product_id,
        "customer": {"email": email}}}).encode()


def _post_webhook(base, body, headers=None):
    return requests.post(f"{base}/polar/webhook", data=body,
                         headers=headers or _signed_headers(body))


def test_unsigned_webhook_rejected(base):
    r = requests.post(f"{base}/polar/webhook", json={"type": "subscription.created"})
    assert r.status_code == 401


def test_bad_signature_rejected(base):
    body = _subscription_event("subscription.active", "sub_ci_bad", EMAIL,
                               PRODUCT_PRO, "active")
    h = _signed_headers(body)
    h["webhook-signature"] = "v1,AAAA" + h["webhook-signature"][7:]
    assert _post_webhook(base, body, h).status_code == 401


def test_unknown_product_ignored(base):
    body = _subscription_event("subscription.active", "sub_ci_unknown", EMAIL,
                               "prod-not-mapped", "active")
    r = _post_webhook(base, body)
    assert r.status_code == 200 and r.json()["status"] == "ignored"


def test_purchase_upgrades_existing_key(base):
    global KEY_BEFORE
    KEY_BEFORE = requests.post(f"{base}/v1/keys",
                               json={"email": EMAIL}).json()["key"]
    body = _subscription_event("subscription.active", "sub_ci_pro", EMAIL,
                               PRODUCT_PRO, "active")
    r = _post_webhook(base, body)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok", "email_tier": "pro"}


def test_key_created_after_purchase_inherits_pro(base):
    d = requests.post(f"{base}/v1/keys", json={"email": EMAIL}).json()
    assert d["tier"] == "pro"
    global KEY_AFTER
    KEY_AFTER = d["key"]


def test_pro_key_has_daily_allowance(base):
    for i, key in enumerate((KEY_BEFORE, KEY_AFTER)):
        r = requests.get(f"{base}/v1/changes",
                         params={"bbox": "4.99,45.99,5.03,46.02", "api_key": key})
        assert r.status_code == 200, r.text
        q = r.json()["quota"]
        assert q["tier"] == "pro" and q["daily_limit"] == 50
        assert q["remaining"] == q["daily_limit"] - q["used_today"]


def test_enterprise_outranks_pro(base):
    body = _subscription_event("subscription.active", "sub_ci_ent", EMAIL,
                               PRODUCT_ENTERPRISE, "active")
    assert _post_webhook(base, body).json()["email_tier"] == "enterprise"
    # Résilier l'enterprise seul redescend au pro encore actif.
    body = _subscription_event("subscription.revoked", "sub_ci_ent", EMAIL,
                               PRODUCT_ENTERPRISE, "canceled")
    assert _post_webhook(base, body).json()["email_tier"] == "pro"


def test_cancellation_demotes_to_free(base):
    body = _subscription_event("subscription.revoked", "sub_ci_pro", EMAIL,
                               PRODUCT_PRO, "canceled")
    r = _post_webhook(base, body)
    assert r.json() == {"status": "ok", "email_tier": "free"}
    # La clé redevient un appelant sous quota (free) : la réponse porte un compteur.
    r = requests.get(f"{base}/v1/changes",
                     params={"bbox": "4.99,45.99,5.03,46.02", "api_key": KEY_AFTER})
    assert r.status_code in (200, 402)
    if r.status_code == 200:
        assert "daily_limit" not in r.json()["quota"]   # back on the free lifetime quota
