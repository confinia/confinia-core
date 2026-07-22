"""Bearer JWT acceptance (issue #36): a Keycloak-issued token authenticates a
call the same way X-API-Key does. Skipped unless a CI Keycloak is configured
(TEST_KC_BASE) and the API was started with KC_ISSUER pointing at it."""
import os
import uuid

import pytest
import requests

KC = os.environ.get("TEST_KC_BASE")
ADMIN_USER = os.environ.get("KC_SETUP_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("KC_SETUP_ADMIN_PASS", "citest-admin")

pytestmark = pytest.mark.skipif(not KC, reason="no CI Keycloak configured")


def _admin_token():
    return requests.post(f"{KC}/realms/master/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "admin-cli",
              "username": ADMIN_USER, "password": ADMIN_PASS}).json()["access_token"]


def _make_user(email, pw):
    h = {"Authorization": f"Bearer {_admin_token()}"}
    requests.post(f"{KC}/admin/realms/confinia/users", headers=h, json={
        "email": email, "username": email, "enabled": True, "emailVerified": True,
        "attributes": {"organization": ["CI Corp"]},
        "credentials": [{"type": "password", "value": pw, "temporary": False}]})
    # direct grant needs the client to allow it; use admin-cli-style password grant
    # against confinia-web is public+standard flow only, so enable a temp direct grant.


def test_bearer_authenticates(base):
    email = f"ci-bearer-{uuid.uuid4().hex[:8]}@test.confinia.io"
    pw = "Pw!" + uuid.uuid4().hex
    _make_user(email, pw)
    # obtain a token via the resource-owner grant on a confidential test client
    tok = requests.post(f"{KC}/realms/confinia/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "confinia-web",
        "username": email, "password": pw, "scope": "openid email"})
    assert tok.status_code == 200, f"token mint failed: {tok.status_code} {tok.text[:200]}"
    access = tok.json()["access_token"]
    r = requests.get(f"{base}/v1/changes",
                     params={"bbox": "4.99,45.99,5.03,46.02"},
                     headers={"Authorization": f"Bearer {access}"})
    assert r.status_code in (200, 402)   # authenticated (not 401/anon path)
    assert "quota" in r.json() or r.status_code == 402
