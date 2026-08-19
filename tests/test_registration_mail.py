"""The mail a stranger gets when they register (issue #132).

What had been proven before was the ADMIN-initiated template
(execute-actions-email), which is a different message from the one
self-registration sends. Driving the real form and reading the resulting mail
found four defects, none of which was visible from the configuration:

  1. frontendUrl was unset, so the action-token link is built from whatever
     host the request arrived on -- it produced http://127.0.0.1:11070/... in a
     message addressed to a customer.
  2. The link was valid for 300 s. Five minutes to click a link in an e-mail:
     anyone reading their mail a quarter of an hour later cannot activate their
     account, and we would never hear of it.
  3. The body was Keycloak's default and named the realm -- 'un compte
     "Confinia-sbx"' -- leaking an internal name to a stranger.
  4. Then, in the replacement text: {1} arrived as the bare number 720 because
     realm overrides skip Keycloak's duration formatter, and every apostrophe
     vanished ("n'êtes" -> "nêtes") because an apostrophe QUOTES in Java
     MessageFormat.
"""
import os
import re

SH = open(os.path.join(os.path.dirname(__file__), "..", "deploy", "keycloak",
                       "setup-realm.sh"), encoding="utf-8").read()
CODE = "\n".join(l for l in SH.splitlines() if not l.lstrip().startswith("#"))


def test_the_verification_link_does_not_depend_on_how_the_request_arrived():
    assert "frontendUrl" in CODE
    assert "https://www.confinia.io/auth" in CODE, "production's public auth URL"
    assert "https://sandbox.confinia.io/auth" in CODE, "the sandbox's own"


def test_the_link_lasts_long_enough_for_a_human_to_use_it():
    """300 s was the default. E-mail is not a synchronous medium."""
    assert '"accessCodeLifespanUserAction": 43200' in CODE, "12 hours"
    assert "actionTokenGeneratedByUserLifespan.verify-email" in CODE


def test_the_stated_duration_matches_the_configured_one():
    """{1} is not formatted in a realm override -- it came out as '720' -- so
    the duration is written into the sentence and must not drift from it."""
    assert "43200" in CODE, "the setting"
    assert "12 heures" in SH and "12 hours" in SH, "the sentence, both languages"
    assert "valable {1}" not in SH and "valid for {1}" not in SH


def test_every_literal_apostrophe_is_doubled():
    """An apostrophe quotes in Java MessageFormat: "n'êtes" arrives as "nêtes"."""
    body = SH.split('kcmsg fr emailVerificationBody')[1].split('kcmsg en')[0]
    singles = re.findall(r"(?<!')'(?!')", body.replace('"', ''))
    assert not singles, f"undoubled apostrophe(s) in the French body: {singles}"


def test_the_message_never_names_the_realm_to_a_stranger():
    """Keycloak's default said 'un compte "Confinia-sbx"'."""
    assert '"displayName": "Confinia"' in CODE
    body = SH.split('kcmsg fr emailVerificationBody')[1].split('kcmsg en')[0]
    assert "sbx" not in body and "{2}" not in body


def test_both_languages_are_written():
    for loc in ("fr", "en"):
        assert f"kcmsg {loc} emailVerificationSubject" in CODE
        assert f"kcmsg {loc} emailVerificationBody" in CODE


def test_verify_email_is_still_never_flipped_automatically():
    """With verifyEmail on and SMTP broken, nobody can register at all."""
    assert 'if [ "${VERIFY_EMAIL:-0}" = 1 ]' in CODE


def test_the_admin_url_default_followed_the_1pesi_move():
    """Keycloak left 8095 for 11070; the script's default had not."""
    assert "KC=${KC_SETUP_URL:-http://127.0.0.1:11070/auth}" in SH
    assert ":-http://127.0.0.1:8095" not in SH, "the stale default must be gone"
