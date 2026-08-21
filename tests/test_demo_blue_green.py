"""The demo follows blue/green, like the API (founder's decision 2026-08-21).

It was served straight from the checkout -- `./demo:/srv/demo` -- so merging a
change to demo/index.html put it in front of visitors the moment the pipeline
pulled. No promotion, no review: it bypassed the very gate the founder placed on
the API. Verified at the time: the version badge merged in #248 was live on
www.confinia.io before any promotion ran.

That is the dangerous direction of the delivery gap. The Caddyfile that stopped
at the mirror, the edge that was never reloaded and the quadlets nobody copied
all delivered too LITTLE; this one delivered too much.
"""
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*p):
    return open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_each_colour_has_its_own_demo_root():
    compose = _read("docker-compose.yml")
    for colour in ("blue", "green"):
        assert f"/srv/demo-{colour}:ro" in compose, f"{colour} needs a mounted root"


def test_the_public_root_is_generated_by_the_promotion():
    caddy = _read("deploy", "caddy", "Caddyfile")
    assert "import demo_root" in caddy
    assert "root * /srv/demo\n" not in caddy, "no hardcoded root that a merge can change"
    stacks = _read("deploy", "stacks.sh")
    assert "(demo_root)" in stacks, "promote writes it"
    assert "demo.caddy" in stacks


def test_staging_a_colour_fills_that_colour_and_no_other():
    sh = _read("deploy", "deploy-api.sh")
    assert 'rsync -a --delete demo/ "$HOME/demo-$P/"' in sh, \
        "the PASSIVE colour, never the active one"
    assert '"$HOME/demo-$A/"' not in sh, "staging must not touch the live colour"


def test_it_is_copied_rather_than_symlinked():
    """caddy mounts these paths and a symlink resolves at mount time, so
    flipping one would change nothing until caddy were recreated."""
    sh = _read("deploy", "deploy-api.sh")
    assert "ln -s" not in sh.split("demo staged into")[0][-400:]


def test_an_unstaged_colour_keeps_the_site_up():
    """A promotion to a colour whose demo was never staged must not serve an
    empty directory."""
    stacks = _read("deploy", "stacks.sh")
    assert '[ -e "$HOME/demo-$c/index.html" ]' in stacks
    assert 'DEMO_ROOT="/srv/demo"' in stacks, "fall back to the checkout"


def test_the_promotion_still_reloads_the_edge():
    """Writing the snippet changes nothing until caddy re-reads it."""
    stacks = _read("deploy", "stacks.sh")
    assert stacks.index("demo.caddy") < stacks.index("caddy reload")
