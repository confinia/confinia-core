"""Sign-in must be reachable, not merely configured (follow-up to #132).

Wiring Keycloak to the API turned up a failure that had been in place for weeks
without a symptom anyone could see: staging carried KC_ISSUER while its
container could not reach Keycloak at all. `_jwks()` caught the error, returned
{}, and `bearer_identity` then returned None for every caller — a signed-in
user is indistinguishable from an anonymous one, so nothing looked wrong.

Keycloak sits on `confinia_default`. The sandbox worked only because it happens
to be attached to that network as well as its own.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
SRC = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()


def _q(colour):
    return open(os.path.join(ROOT, "deploy", "quadlet",
                             f"confinia-{colour}-api.container"), encoding="utf-8").read()


def test_each_colour_can_actually_reach_keycloak():
    for colour in ("blue", "green"):
        q = _q(colour)
        assert f"Network=confinia-{colour}_default" in q, "its own colour network"
        assert "Network=confinia_default" in q, \
            "and the shared one, where Keycloak is -- otherwise no keys, no identity"


def test_staging_reaches_it_too():
    sh = open(os.path.join(ROOT, "deploy", "staging-up.sh"), encoding="utf-8").read()
    assert "Network=confinia_default" in sh
    assert "KC_DISCOVERY=http://confinia_keycloak_1:8180" in sh, \
        "the keys are fetched internally; the public URL goes out through the edge"


def test_discovery_is_never_left_to_default_to_the_public_issuer():
    """That default is what made staging fail: KC_DISCOVERY fell back to
    KC_ISSUER, a public URL the container cannot fetch."""
    for text in (_q("blue"), _q("green")):
        assert "KC_DISCOVERY=" in text, "set it explicitly wherever KC_ISSUER is set"


def test_the_issuer_matches_the_realms_pinned_frontend_url():
    """A realm has one frontendUrl and therefore one issuer; a mismatch rejects
    every token (learned the hard way when CI went red)."""
    setup = open(os.path.join(ROOT, "deploy", "keycloak", "setup-realm.sh"),
                 encoding="utf-8").read()
    assert "https://www.confinia.io/auth" in setup
    for colour in ("blue", "green"):
        m = re.search(r"KC_ISSUER=(\S+)", _q(colour))
        assert m and m.group(1) == "https://www.confinia.io/auth/realms/confinia"


def test_a_broken_identity_is_reported_rather_than_silent():
    assert "def identity_health(" in SRC
    for state in ('"off"', '"unreachable"', '"ok"'):
        assert state in SRC, f"health must distinguish {state}"
    assert '"identity": identity_health()' in SRC, "and /healthz must say so"
    assert "_JWKS_ERROR" in SRC, "the reason is kept, not discarded"


def test_the_keys_are_fetched_where_discovery_was_reached():
    """Keycloak builds `jwks_uri` from the realm's frontendUrl.

    Once that is pinned to the public host, the discovery document -- correctly
    fetched over the internal network -- advertises a key URL the container
    cannot reach, because it goes out through the edge and back. Discovery
    succeeded and the SECOND hop failed, which presented as "Keycloak is
    unreachable" and sent me looking at networks that were fine.

    Measured: `jwks_uri` = https://sandbox.confinia.io/... , fetching it from
    inside the container = Connection refused, while the discovery URL beside
    it returned 200.
    """
    fn = SRC.split("def _jwks(")[1].split("\ndef ")[0]
    assert "jwks_uri.startswith(KC_DISCOVERY" in fn, \
        "an advertised URL off our discovery host must not be trusted"
    assert "/protocol/openid-connect/certs" in fn, "we build the internal one instead"


def test_the_committed_unit_is_actually_installed_by_the_deploy():
    """deploy/quadlet/*.container was committed, reviewed and merged -- and
    reached nothing. The units had been installed by hand once, so the repo
    copies were documentation that drifted.

    Proven by this very change: KC_ISSUER landed in the file, the promotion
    succeeded, and production still reported `identity: off` because the image
    was new and the environment was not. Same shape as the Caddyfile that
    reached the mirror and stopped there.
    """
    sh = open(os.path.join(ROOT, "deploy", "deploy-api.sh"), encoding="utf-8").read()
    assert "deploy/quadlet/confinia-${P}-api.container" in sh, "read the committed unit"
    assert "containers/systemd" in sh, "and write it where systemd reads"
    assert "daemon-reload" in sh, "so the change is picked up"
    i_install = sh.index("UNIT_DST")
    i_restart = sh.index('systemctl --user restart "confinia-${P}-api"')
    assert i_install < i_restart, "install before restart, or the restart uses the old file"
