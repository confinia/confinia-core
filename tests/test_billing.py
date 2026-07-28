"""Self-service billing portal (issue #81): GET /v1/billing/portal mints a Polar
customer-portal session for the SIGNED-IN caller only. The endpoint is gated by
the Keycloak Bearer JWT, and degrades gracefully (503) when no portal can be
issued (no access token configured, or no Polar customer for the account)."""
import os
import uuid

import pytest
import requests

KC = os.environ.get("TEST_KC_BASE")
ADMIN_USER = os.environ.get("KC_SETUP_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("KC_SETUP_ADMIN_PASS", "citest-admin")


def test_portal_requires_bearer(base):
    r = requests.get(f"{base}/v1/billing/portal")
    assert r.status_code == 401


def test_portal_rejects_garbage_bearer(base):
    r = requests.get(f"{base}/v1/billing/portal",
                     headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


@pytest.mark.skipif(not KC, reason="no CI Keycloak configured")
def test_portal_degrades_gracefully_for_valid_caller(base):
    # A real signed-in caller with no Polar customer (and no access token in CI)
    # gets a clean 503, not a crash — the account page falls back to the receipt.
    email = f"ci-bill-{uuid.uuid4().hex[:8]}@test.confinia.io"
    pw = "Pw!" + uuid.uuid4().hex
    admin = requests.post(f"{KC}/realms/master/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "admin-cli",
              "username": ADMIN_USER, "password": ADMIN_PASS}).json()["access_token"]
    requests.post(f"{KC}/admin/realms/confinia/users",
        headers={"Authorization": f"Bearer {admin}"}, json={
            "email": email, "username": email, "enabled": True, "emailVerified": True,
            "firstName": "CI", "lastName": "User", "requiredActions": [],
            "attributes": {"organization": ["CI Corp"]},
            "credentials": [{"type": "password", "value": pw, "temporary": False}]})
    tok = requests.post(f"{KC}/realms/confinia/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "confinia-web",
        "username": email, "password": pw, "scope": "openid email"})
    assert tok.status_code == 200, f"token mint failed: {tok.status_code} {tok.text[:200]}"
    # The id_token always carries the email claim; the access token may not, so
    # the account page (and this test) sends the id_token as the Bearer.
    id_token = tok.json()["id_token"]
    r = requests.get(f"{base}/v1/billing/portal",
                     headers={"Authorization": f"Bearer {id_token}"})
    assert r.status_code == 503, r.text     # authenticated, but no portal to issue
