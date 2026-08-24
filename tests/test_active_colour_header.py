"""Which colour is serving production, told to the platform dashboard.

The platform edge does dumb routing: it cannot see which colour sits behind our
caddy, so the only way its dashboard can show it is if we say so. A header that
says blue while green is serving is worse than no header at all -- it turns the
dashboard into a confident liar -- so the rule that matters is not that the
header exists but that it is REWRITTEN BY THE PROMOTION, never by hand.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
STACKS = open(os.path.join(ROOT, "deploy", "stacks.sh"), encoding="utf-8").read()
CADDY = open(os.path.join(ROOT, "deploy", "caddy", "Caddyfile"), encoding="utf-8").read()
EDGE = open(os.path.join(ROOT, "deploy", "deploy-edge.sh"), encoding="utf-8").read()


def _promote_block():
    """The write-upstreams|promote branch: everything a promotion generates."""
    return STACKS.split("write-upstreams|promote)")[1].split("\nstatus)")[0]


def test_the_header_is_written_by_the_promotion_and_nowhere_else():
    """Hand-written once, it would keep saying blue through every promotion."""
    assert "X-Active-Colour" in _promote_block()
    assert CADDY.count("X-Active-Colour") == 0, "never a literal in the checked-in config"


def test_it_is_generated_from_the_same_variable_as_the_ports():
    """The colour that picks the upstream ports must be the colour announced.
    Two variables would eventually disagree, and the disagreement would be
    invisible: caddy would serve fine and the dashboard would lie."""
    block = _promote_block()
    assert 'header X-Active-Colour "$c"' in block
    ports = block.index("blue)  ACT=")
    header = block.index("X-Active-Colour")
    assert ports < header, "the colour is resolved before it is announced"
    # same heredoc as the upstreams: one write, so they cannot drift apart
    heredoc = block.split("<<CADDY")[1].split("\nCADDY")[0]
    assert "X-Active-Colour" in heredoc and "(api_upstreams)" in heredoc


def test_both_production_hostnames_emit_it():
    for host in ("http://www.confinia.io:11000", "http://api.confinia.io:11000"):
        block = CADDY.split(host + " {")[1].split("\n}\n")[0]
        assert "import active_colour" in block, f"{host} does not announce its colour"


def test_no_other_environment_claims_to_be_production():
    """Staging and sandbox are not blue/green production. A colour header there
    would be a false signal about a deployment model they do not have."""
    for host in ("http://staging.confinia.io", "http://sandbox.confinia.io",
                 "http://staging.api.confinia.io"):
        block = CADDY.split(host)[1].split("\n}\n")[0]
        assert "import active_colour" not in block, f"{host} must stay neutral"


def test_it_is_emitted_at_the_proxy_so_it_survives_an_error_response():
    """Emitted by the app it would vanish on the responses that matter most --
    a 302, a 401, a 500 -- which is when you want to know which colour answered."""
    block = _promote_block()
    snippet = block.split("(active_colour) {")[1].split("}")[0]
    assert "header X-Active-Colour" in snippet
    for host in ("http://www.confinia.io:11000", "http://api.confinia.io:11000"):
        site = CADDY.split(host + " {")[1].split("\n}\n")[0]
        # at site level, not inside a handle/route: it must cover every response
        before_handler = site.split("handle")[0].split("route")[0]
        assert "import active_colour" in before_handler


def test_a_new_snippet_cannot_break_the_edge_before_the_next_promotion():
    """The Caddyfile imports snippets from the GENERATED state file. Merged
    together, a new snippet would fail validation until someone promoted."""
    assert "write-upstreams" in EDGE
    assert EDGE.index("write-upstreams") < EDGE.index("caddy validate")
