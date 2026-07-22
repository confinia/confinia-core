"""Identity journey (issue #19 phase 1): a throwaway Keycloak bootstrapped by
the SAME script as production must yield a realm where self-registration is
open, the organization attribute is REQUIRED on the registration form, and
the confinia-web client is public with PKCE enforced."""
import html
import os
import re
import urllib.parse

import requests

KC = os.environ.get("TEST_KC_BASE", "http://127.0.0.1:8180/auth")
ADMIN_USER = os.environ.get("KC_SETUP_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("KC_SETUP_ADMIN_PASS", "citest-admin")


def _admin_headers():
    r = requests.post(f"{KC}/realms/master/protocol/openid-connect/token",
                      data={"grant_type": "password", "client_id": "admin-cli",
                            "username": ADMIN_USER, "password": ADMIN_PASS})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_realm_is_up_with_open_registration():
    r = requests.get(f"{KC}/realms/confinia/.well-known/openid-configuration")
    assert r.status_code == 200
    realm = requests.get(f"{KC}/admin/realms/confinia", headers=_admin_headers()).json()
    assert realm["registrationAllowed"] is True
    assert realm["registrationEmailAsUsername"] is True


def test_profile_requires_organization():
    prof = requests.get(f"{KC}/admin/realms/confinia/users/profile",
                        headers=_admin_headers()).json()
    org = next(a for a in prof["attributes"] if a["name"] == "organization")
    assert "user" in org["required"]["roles"]
    assert "user" in org["permissions"]["edit"]


def test_client_is_public_pkce():
    cl = requests.get(f"{KC}/admin/realms/confinia/clients",
                      params={"clientId": "confinia-web"},
                      headers=_admin_headers()).json()[0]
    assert cl["publicClient"] is True
    assert cl["attributes"]["pkce.code.challenge.method"] == "S256"
    assert "https://www.confinia.io/*" in cl["redirectUris"]
    mappers = {m["name"] for m in cl.get("protocolMappers", [])}
    assert "organization" in mappers   # the /account page reads it from the token


def test_registration_form_shows_organization():
    s = requests.Session()
    r = s.get(f"{KC}/realms/confinia/protocol/openid-connect/registrations", params={
        "client_id": "confinia-web", "response_type": "code", "scope": "openid",
        "redirect_uri": "https://www.confinia.io/account.html",
        "code_challenge": "A" * 43, "code_challenge_method": "S256"})
    assert r.status_code == 200, r.text[:300]
    # Theme versions name the input differently (organization vs
    # user.attributes.organization): assert on the attribute name itself.
    assert "organization" in r.text.lower(), \
        "organization field missing from the registration form"
