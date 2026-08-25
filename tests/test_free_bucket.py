"""A second free key must not buy a second free allowance.

Measured on production, 2026-08-25: `POST /v1/keys` mints a key from an
unverified address, and a brand-new key reported `used 0 / remaining 10` while
the caller behind it had already spent 6. Minting was cheaper than paying, so
the premium gate stopped the honest and inconvenienced nobody else.

This is a speed bump by design. The data is INSEE and IGN under Licence
Ouverte: what is sold is the assembly and the provenance, not an exclusivity we
do not have. The goal is only to make paying cheaper than looping.
"""
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
SRC = open(os.path.join(HERE, "..", "api", "main.py"), encoding="utf-8").read()

sys.path.insert(0, os.path.join(HERE, "..", "api"))
os.environ.setdefault("PG_DSN", "postgresql://unused@127.0.0.1:1/unused")
os.environ.setdefault("OPS_DSN", "postgresql://unused@127.0.0.1:1/unused")
try:
    import main as m
except Exception as exc:                # pragma: no cover - environment
    pytest.skip(f"api/main.py not importable: {exc}", allow_module_level=True)


def test_two_keys_for_one_mailbox_share_one_allowance():
    """The loop this closes: mint, spend ten, mint again."""
    assert m._free_bucket("someone@gmail.com") == m._free_bucket("someone@gmail.com")


def test_a_plus_tag_does_not_buy_a_new_allowance():
    """Every major provider delivers +tag to the same inbox, so leaving it in
    would hand the loop straight back."""
    assert m._free_bucket("someone+1@gmail.com") == m._free_bucket("someone@gmail.com")
    assert m._free_bucket("someone+a+b@gmail.com") == m._free_bucket("someone@gmail.com")


def test_case_and_whitespace_do_not_buy_one_either():
    assert m._free_bucket("  SomeOne@Gmail.com ") == m._free_bucket("someone@gmail.com")


def test_two_different_mailboxes_stay_separate():
    """It must still be an allowance, not a global counter."""
    assert m._free_bucket("a@gmail.com") != m._free_bucket("b@gmail.com")


def test_the_bucket_is_a_fingerprint_not_an_address():
    """premium_seen has no business holding addresses in clear -- same doctrine
    as the visitor IP, which is hashed and never stored."""
    b = m._free_bucket("someone@gmail.com")
    assert b.startswith("email:")
    assert "someone" not in b and "gmail" not in b
    assert len(b) == len("email:") + 32


def test_a_missing_address_still_yields_a_bucket():
    """A key with no email must not crash the gate, and must not be free of it
    either."""
    assert m._free_bucket(None).startswith("email:")
    assert m._free_bucket(None) != m._free_bucket("someone@gmail.com")


def test_paid_tiers_are_still_identified_by_their_key():
    """A paid key was bought; it does not share a bucket with anything."""
    fn = SRC.split("def _premium_caller(")[1].split("\ndef ")[0]
    assert 'f"key:{key}"' in fn, "enterprise/pro/ladder keep key identity"
    assert "_free_bucket(row[2])" in fn, "only the free tier moves to the mailbox"


def test_the_anonymous_caller_is_untouched():
    """Someone with no key at all is still bucketed on a hashed IP."""
    fn = SRC.split("def _premium_caller(")[1].split("\ndef ")[0]
    assert 'caller = "ip:" + hashlib.sha256' in fn
