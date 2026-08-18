"""Invariants of the e2e harness (issue #208).

These do not run the browser journey -- that needs the Creem test store and
lives in tests/e2e/run.sh. They guard the properties that keep the harness
honest and test-only.
"""
import json
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
E2E = os.path.join(ROOT, "tests", "e2e")


def _read(name):
    return open(os.path.join(E2E, name), encoding="utf-8").read()


def test_the_journey_is_a_valid_side_project():
    d = json.loads(_read("journey.side"))
    assert d["version"] == "2.0"
    cmds = d["tests"][0]["commands"]
    ids = [c["command"] for c in cmds]
    assert "open" in ids and "select" in ids and "click" in ids
    # it reaches payment and asserts it, and does NOT try the bot-walled card
    assert any("Pay" in c.get("target", "") for c in cmds if c["command"] == "assertElementPresent")
    assert not any("card-form" in c.get("target", "") for c in cmds), \
        "Selenium must not reach for the bot-protected card iframe"


def test_the_harness_is_test_only():
    run = _read("run.sh")
    assert 'creem_test_*) : ;;' in run, "run.sh must refuse a non-test key"
    rep = _read("creem_report.py")
    assert 'creem_test_' in rep and 'env != "test"' in rep, \
        "the verdict must refuse anything but the test environment"
    pay = _read("pay_and_verify.py")
    assert 'startswith("creem_test_")' in pay, "the payer must refuse a live key"


def test_the_verdict_comes_from_the_provider_not_the_page():
    pay = _read("pay_and_verify.py")
    assert "/v1/subscriptions/search" in pay, \
        "the outcome must be read from the provider, not the success screen"
    assert 'status") in ("active", "trialing")' in pay


def test_the_env_carries_no_secret_and_is_gitignored():
    ex = _read(".env.example")
    assert "creem_test_..." in ex and "creem_test_5" not in ex, \
        "the example must not carry a real key"
    gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
    assert "tests/e2e/.env" in gi
