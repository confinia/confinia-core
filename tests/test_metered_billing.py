"""Metered billing: floor, per-report price, hard cap.

The amounts are CONFIGURATION, like secrets: they live in the deployment
environment and never in this repository (RULES 19). Every test here uses
synthetic values, and one test asserts the repo carries none of its own.

Metered billing changes what a bug costs. A wrong number on a page misleads;
a wrong charge takes money. The cap is the behaviour that matters most: past
it, every further report is free and must keep working -- overcharging a
professional customer once is how many chances we get.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
SRC = open(os.path.join(ROOT, "api", "main.py"), encoding="utf-8").read()


def _charge(floor, per, cap):
    """monthly_charge_cents with synthetic amounts injected."""
    ns = {"BILLING_FLOOR_CENTS": floor, "BILLING_PER_REPORT_CENTS": per,
          "BILLING_CAP_CENTS": cap, "METERED": min(floor, per, cap) > 0}
    m = re.search(r"^def monthly_charge_cents\(.*?(?=^[A-Za-z@#]|\Z)", SRC, re.S | re.M)
    assert m, "monthly_charge_cents not found"
    exec(m.group(0), ns)
    return ns["monthly_charge_cents"]


def test_the_cap_stops_the_meter_and_nothing_else():
    """250 reports at synthetic prices: the charge is EXACTLY the cap.

    Not cap-plus-one-report, not cap-rounded: exactly. And the function stays
    flat from there -- report 251 changes nothing, which is the money half of
    'the report past the cap must still work'.
    """
    charge = _charge(floor=700, per=37, cap=8400)
    assert charge(250) == 8400
    assert charge(251) == 8400
    assert charge(10 ** 6) == 8400
    # flat past the crossing point, never above the cap
    crossing = 8400 // 37 + 1
    for used in range(crossing, crossing + 50):
        assert charge(used) <= 8400


def test_the_floor_covers_early_usage():
    """Below the floor the customer pays the floor, not per-report."""
    charge = _charge(floor=700, per=37, cap=8400)
    assert charge(0) == 700
    assert charge(1) == 700
    assert charge(700 // 37) == 700          # still under the floor
    assert charge(700 // 37 + 1) == 37 * (700 // 37 + 1)   # first metered report


def test_the_charge_is_monotone():
    """More reports can never cost less -- an invoice that goes down when usage
    goes up is unexplainable to a customer and to us."""
    charge = _charge(floor=500, per=19, cap=3000)
    prev = 0
    for used in range(0, 400):
        c = charge(used)
        assert c >= prev, f"charge dropped at {used}: {prev} -> {c}"
        prev = c


def test_unconfigured_means_off_and_free():
    """With no amounts in the environment, the meter does not exist."""
    charge = _charge(floor=0, per=0, cap=0)
    assert charge(0) == 0 and charge(10 ** 6) == 0


def test_the_repo_carries_no_amounts():
    """The tariff is configuration, like a secret (RULES 19).

    Every BILLING_* default in code must be '0' -- an amount committed here
    would publish the pricing model on a public repository, which is the exact
    mistake this rule exists because of.
    """
    for var in ("BILLING_FLOOR_CENTS", "BILLING_PER_REPORT_CENTS", "BILLING_CAP_CENTS"):
        m = re.search(rf'{var}", "(\d+)"', SRC)
        assert m and m.group(1) == "0", f"{var} has a non-zero default committed"
    # and the deploy scripts pass them through without a value
    sbx = open(os.path.join(ROOT, "deploy", "sandbox-up.sh"), encoding="utf-8").read()
    for var in ("BILLING_FLOOR_CENTS", "BILLING_PER_REPORT_CENTS", "BILLING_CAP_CENTS"):
        # The PROPERTY, not the mechanism: the amount reaches the sandbox and
        # defaults to 0 when unset. This asserted `-e VAR="${VAR:-0}"` until the
        # launcher moved to a Quadlet env file, at which point it failed while
        # the behaviour was correct -- and nobody saw it, because this file has
        # never run in CI.
        assert f'{var}=${{{var}:-0}}' in sbx or f'-e {var}="${{{var}:-0}}"' in sbx, \
            f"{var} does not reach the sandbox with a zero default"


def test_metered_pro_is_recorded_but_never_refused():
    """A limit refuses; a meter records.

    Under metering the pro tier has no usage ceiling -- the ceiling is on the
    charge. The resolution must return limit=None WITH a period (record, never
    refuse), and the gate must only 402 when a limit exists. Enterprise stays
    unmetered: no period, nothing recorded.
    """
    m = re.search(r'if row\[1\] == "pro":.*?f"key:\{key\}"\)', SRC, re.S)
    assert m and "None if METERED else PRO_MONTHLY" in m.group(0), \
        "metered pro must have no usage limit"
    gate = SRC.split("def premium_gate(")[1].split("\ndef ")[0]
    assert "if period is None:" in gate, "enterprise (no period) must stay unmetered"
    assert "if limit is not None and used >= limit:" in gate, \
        "the 402 must be conditional on a limit existing"


def test_the_two_free_allowances_add_up():
    """Founder decision: anonymous 10 + registered 10 = 20, not a reset.

    This falls out of the caller identity: anonymous usage is keyed on an IP
    hash ("ip:..."), a key's usage on the key ("key:..."). Separate callers,
    separate lifetime buckets -- so registering after exhausting the anonymous
    allowance grants a full fresh one, which is exactly the moment a visitor
    most wants an account. This test pins the two namespaces so a refactor
    cannot silently merge them.
    """
    resolver = SRC.split("def _premium_caller(")[1].split("\ndef ")[0]
    assert 'f"key:{key}"' in resolver, "key callers must be keyed on the key"
    assert '"ip:" + hashlib' in resolver, "anonymous callers must be keyed on the IP hash"


def test_the_account_page_shows_the_running_total():
    """A metered price nobody can see is a metered price nobody trusts."""
    html = open(os.path.join(ROOT, "deploy", "site", "account.html"),
                encoding="utf-8").read()
    assert "p.billing" in html, "the account page must render the billing block"
    assert "charge_cents" in html and "cap_cents" in html
    assert "cap reached, further reports are free" in html, \
        "reaching the cap must read as safety, not as an error"
    for token in re.findall(r"(\d+)\s*[€]", html):
        assert False, f"a euro amount is hardcoded in the page: {token} €"
