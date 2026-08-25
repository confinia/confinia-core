"""A partner application is never refused, and always counted.

Founder's decision, 2026-08-25: EcoBuilding may call Confinia freely while
Confinia's value to another product is unproven. The quota must stop REFUSING
it -- and must keep COUNTING it, because that usage is the only evidence that
will settle whether this data is worth anything to a building report.

The distinction this file guards: `enterprise` is unlimited AND unmetered, so
using it here would have thrown the evidence away to save one row per town.
"""
import os

SRC = open(os.path.join(os.path.dirname(__file__), "..", "api", "main.py"),
           encoding="utf-8").read()


def _fn(name):
    body = SRC.split(f"def {name}(")[1].split("\ndef ")[0]
    if '"""' in body:
        head, _, rest = body.partition('"""')
        body = head + rest.partition('"""')[2]
    return body


def test_a_partner_is_never_refused():
    """limit None is what premium_gate reads to skip the 402 path."""
    fn = _fn("_premium_caller")
    line = [l for l in fn.split("\n") if '"partner", None' in l]
    assert line, "partner must resolve to limit None"


def test_a_partner_is_still_recorded():
    """A period of None means unmetered; partner must carry a real period so
    premium_gate inserts into premium_seen."""
    fn = _fn("_premium_caller")
    line = [l for l in fn.split("\n") if '"partner", None' in l][0]
    assert "date.today().replace(day=1)" in line, "must have a period, or nothing is counted"
    assert "None, None" not in line, "that is the enterprise shape: unmetered"


def test_partner_is_not_enterprise():
    """The whole point: enterprise records nothing."""
    fn = _fn("_premium_caller")
    ent = [l for l in fn.split("\n") if '"enterprise", None, None' in l]
    assert ent, "enterprise stays unmetered"
    assert ent[0] != [l for l in fn.split("\n") if '"partner", None' in l][0]


def test_the_gate_refuses_only_when_a_limit_exists():
    """The property partner depends on, asserted where it lives."""
    fn = _fn("premium_gate")
    assert "if limit is not None and used >= limit:" in fn
    assert "INSERT INTO public.premium_seen" in fn


def test_a_partner_key_is_resolved_before_the_paid_ladder():
    """Order matters: a partner key must not fall through to a paid tier's
    allowance and start being refused at 100 a month."""
    fn = _fn("_premium_caller")
    assert fn.index('"partner"') < fn.index('if row[1] == "pro"')


def test_usage_stays_visible_to_the_quota_endpoint():
    """The evidence is only useful if it can be read back."""
    fn = _fn("premium_status")
    assert "premium_seen" in fn and "count(*)" in fn
