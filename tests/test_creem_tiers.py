"""Creem webhook + tier ladder (issue #213).

The ladder is configuration, like the secrets: amounts live in the deployment
environment and never in this repository (RULES 19). Every test here uses
synthetic values.
"""
import hashlib
import hmac
import json
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
SRC = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()


def _ns(tiers=None, secret="s3cr3t"):
    """Load the pure Creem helpers with synthetic env."""
    ns = {"os": os, "json": json, "hashlib": hashlib}
    tiers = tiers if tiers is not None else [
        {"product": "prod_a", "cents": 100, "reports": 5},
        {"product": "prod_b", "cents": 300, "reports": 50},
        {"product": "prod_c", "cents": 900, "reports": None},
    ]
    ns["CREEM_WEBHOOK_SECRET"] = secret
    ns["CREEM_MODE"] = "test"
    ns["CREEM_TIERS"] = tiers
    ns["CREEM_TIER_KEYS"] = [f"t{i+1}" for i in range(len(tiers))]
    ns["CREEM_PRODUCT_TIER"] = {t["product"]: f"t{i+1}" for i, t in enumerate(tiers)}
    ns["CREEM_TIER_REPORTS"] = {f"t{i+1}": t.get("reports") for i, t in enumerate(tiers)}
    for fn in ("creem_checkout_url", "creem_verify"):
        m = re.search(rf"^def {fn}\(.*?(?=^def |\Z)", SRC, re.S | re.M)
        assert m, f"{fn} not found"
        exec(m.group(0), ns)
    return ns


def test_signature_is_hex_hmac_of_the_raw_body():
    ns = _ns()
    body = b'{"eventType":"subscription.active"}'
    good = hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
    assert ns["creem_verify"](body, good)
    assert not ns["creem_verify"](body, good[:-1] + "0")
    assert not ns["creem_verify"](body + b" ", good), "raw body means RAW body"


def test_an_empty_secret_verifies_nothing():
    """Never a webhook on blind trust -- same rule as polar_verify."""
    ns = _ns(secret="")
    body = b"{}"
    sig = hmac.new(b"", body, hashlib.sha256).hexdigest()
    assert not ns["creem_verify"](body, sig)


def test_no_amount_is_committed():
    """CREEM_TIERS defaults to an empty list; the ladder exists only in env."""
    m = re.search(r'os\.environ\.get\("CREEM_TIERS", "(.*?)"\)', SRC)
    assert m and m.group(1) == "[]", "a tier ladder is committed to the repo"
    assert 'os.environ.get("CREEM_WEBHOOK_SECRET", "")' in SRC


def test_grant_and_revoke_cover_the_documented_events():
    """Creem's own mapping: active/trialing/paid grant, canceled/paused/expired
    revoke. checkout.completed grants too (their quickstart treats it as the
    payment-successful signal)."""
    route = SRC.split('@app.post("/creem/webhook"')[1].split("\n@app.")[0]
    for ev in ("checkout.completed", "subscription.active",
               "subscription.trialing", "subscription.paid"):
        assert f'"{ev}"' in route, f"grant event {ev} unhandled"
    for ev in ("subscription.canceled", "subscription.paused",
               "subscription.expired"):
        assert f'"{ev}"' in route, f"revoke event {ev} unhandled"


def test_the_webhook_is_idempotent_and_retry_aware():
    """Creem retries 5 times over 24 h; the Polar rehearsal showed what a
    non-2xx loop looks like from the customer's side (FREE forever)."""
    route = SRC.split('@app.post("/creem/webhook"')[1].split("\n@app.")[0]
    assert "ON CONFLICT (email, tier) DO UPDATE" in route, \
        "redelivery must converge, not duplicate"
    assert '"unmatched": True' in route, \
        "an unmapped product must answer 200 -- a retry cannot fix it"
    assert "creem_verify" in route and "401" in route


def test_the_ladder_ranks_above_legacy_pro_and_below_enterprise():
    fn = SRC.split("def polar_apply_tier(")[1].split("\ndef ")[0]
    assert 'reversed(CREEM_TIER_KEYS)' in fn, "t3 must beat t2 must beat t1"
    assert fn.index('"enterprise"') < fn.index("CREEM_TIER_KEYS"), \
        "enterprise stays on top"


def test_the_top_tier_records_without_refusing():
    """A null allowance is unlimited-but-recorded: limit None WITH a period."""
    resolver = SRC.split("def _premium_caller(")[1].split("\ndef ")[0]
    assert "row[1] in CREEM_TIER_REPORTS" in resolver
    assert "CREEM_TIER_REPORTS[row[1]]" in resolver, \
        "the allowance must come from the environment, not a literal"


def test_pricing_endpoint_serves_the_ladder():
    fn = SRC.split("def pricing_config(")[1].split("\ndef ")[0]
    assert '"mode": "tiers"' in fn
    assert "creem_checkout_url" in fn, "each tier must carry its checkout link"
    assert fn.index("CREEM_TIERS") < fn.index("METERED"), \
        "the ladder outranks the metered mode when both are configured"


def test_checkout_urls_follow_the_mode():
    ns = _ns()
    assert ns["creem_checkout_url"]("prod_x") == "https://creem.io/test/product/prod_x"
    ns["CREEM_MODE"] = "live"
    exec(re.search(r"^def creem_checkout_url\(.*?(?=^def |\Z)", SRC, re.S | re.M).group(0), ns)
    assert ns["creem_checkout_url"]("prod_x") == "https://creem.io/product/prod_x"


def test_the_sandbox_api_runs_under_systemd_with_secrets_in_a_file():
    """Issue #123's third bite, plus #202's lesson, in one assertion set.

    A bare `podman run` from a CI job leaves a wedged corpse when the job dies
    (conmon exits with it), and the next deploy's --replace fails with an
    internal libpod error -- which is exactly how two deployments failed on
    2026-08-18. And a Quadlet Environment= line lands on the service command
    line, so the DSNs must travel via a 600 EnvironmentFile instead.
    """
    sh = open(os.path.join(ROOT, "deploy", "sandbox-up.sh"), encoding="utf-8").read()
    body = "\n".join(l for l in sh.splitlines() if not l.lstrip().startswith("#"))
    assert "podman run -d" not in body, "a bare podman run dies with the CI job"
    assert "systemctl --user restart confinia-sbx-api" in body
    assert "podman rm -f --ignore" in body and "reset-failed" in body, \
        "the deploy must outlive one wedged container"
    assert "EnvironmentFile=%h/.config/containers/systemd/confinia-sbx.env" in sh
    unit = sh.split("[Container]")[1].split("UNIT")[0]
    for line in unit.splitlines():
        if line.startswith("Environment="):
            assert not any(k in line for k in ("DSN", "PASSWORD", "SECRET", "TOKEN")), \
                f"secret on the service command line: {line[:50]}"
