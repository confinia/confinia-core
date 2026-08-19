"""The bounce detector must not drown in noise it makes itself.

alert@confinia.io is send-only, so anything in its inbox is a finding -- that is
the one place where "the alerting itself is broken" becomes visible. By
2026-08-19 the inbox held 64 messages and the check failed on every run, which
is the same as not checking: 53 were bounces the e2e journey caused itself
(Keycloak verification mail to synthetic e2e-<timestamp>@confinia.io addresses
that cannot exist), 10 were another system's CI mail addressed here, and
exactly 1 was a real bounce -- the 2026-08-11 one this file was written for.

So the check now sorts what it finds, and the three piles mean different things.
"""
import email
import importlib.util
import os

HERE = os.path.dirname(__file__)
SRC = open(os.path.join(HERE, "..", "deploy", "mailcheck.py"), encoding="utf-8").read()
CODE = "\n".join(l for l in SRC.splitlines() if not l.strip().startswith("#"))

_spec = importlib.util.spec_from_file_location(
    "mailcheck", os.path.join(HERE, "..", "deploy", "mailcheck.py"))
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)


def _msg(raw: str):
    return email.message_from_string(raw)


BOUNCE_OF_OUR_TEST_USER = """From: MAILER-DAEMON@ssl0.ovh.net
Subject: Undelivered Mail Returned to Sender
Content-Type: text/plain

<e2e-20260817-134554@confinia.io>: Recipient address rejected: User unknown
"""

REAL_BOUNCE = """From: MAILER-DAEMON@ssl0.ovh.net
Subject: Undelivered Mail Returned to Sender
Content-Type: text/plain

<contact@confinia.io>: Recipient address rejected: User unknown
"""

FOREIGN_MAIL = """From: ci@example.invalid
Subject: [Another Product CI] FAILED: sandbox (240/merge)
Content-Type: text/plain

A build failed. This is not a bounce -- it is mail someone addressed to us.
"""


def test_a_delivery_failure_is_told_apart_from_mail_merely_sent_here():
    """Calling foreign mail 'never delivered' would be a plain untruth."""
    assert "def is_bounce(" in CODE
    assert "message/delivery-status" in CODE, "the standard bounce part"
    assert "mailer-daemon" in CODE.lower() and "postmaster" in CODE.lower()


def test_our_own_synthetic_test_recipients_are_recognised():
    assert "TEST_RECIPIENT" in CODE
    assert "e2e" in CODE and "reset" in CODE, "both synthetic families"


def test_an_unattributable_bounce_stays_in_the_real_pile():
    """Failing to attribute a bounce must never demote it to noise."""
    fn = SRC.split("def bounced_recipient(")[1].split("\ndef ")[0]
    assert "return m.group(0) if m else None" in fn, \
        "no match means None, and None routes to the real pile"


def test_purging_is_deliberate_and_never_a_side_effect_of_looking():
    assert '"--purge" in sys.argv' in CODE, "purging must be asked for explicitly"
    assert "readonly=not purge" in CODE, "the mailbox opens read-only unless purging"


def test_a_bounce_for_our_test_address_is_recognised_as_our_own_noise():
    m = _msg(BOUNCE_OF_OUR_TEST_USER)
    assert mc.is_bounce(m), "a MAILER-DAEMON report is a bounce"
    assert mc.bounced_recipient(m) == "e2e-20260817-134554@confinia.io"


def test_a_bounce_for_a_real_address_is_not_dismissed_as_noise():
    """The 2026-08-11 bounce this check exists for must survive the filter."""
    m = _msg(REAL_BOUNCE)
    assert mc.is_bounce(m)
    assert mc.bounced_recipient(m) is None, "a real recipient is never test noise"


def test_foreign_mail_is_not_called_a_delivery_failure():
    m = _msg(FOREIGN_MAIL)
    assert not mc.is_bounce(m), "mail sent to us is not mail that bounced"


def test_foreign_mail_still_fails_the_check():
    """Another system pointing at our alert mailbox is a nuisance worth seeing,
    even though it is not a delivery failure."""
    assert "return 0 if not foreign else 1" in CODE
