"""The maintenance page must not lie about what is happening (issue #99).

A holding page that answers 200 tells every monitor and every search engine
that the service is fine. A 404 tells them the resource is permanently gone and
they deindex it. Only 503 + Retry-After says "temporarily unavailable", which is
what a maintenance window actually is.
"""
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_the_holding_server_answers_503_not_200_or_404():
    c = _read("deploy", "maintenance", "Caddyfile")
    assert "error 503" in c, "file_server alone answers 200, which defeats the purpose"
    assert "handle_errors" in c, "the page must be served from handle_errors to keep the status"
    directives = [l for l in c.splitlines() if not l.strip().startswith("#")]
    assert not any("404" in l for l in directives), \
        "404 makes search engines deindex the site (the comment may mention it)"


def test_it_tells_clients_when_to_come_back():
    c = _read("deploy", "maintenance", "Caddyfile")
    assert "Retry-After" in c
    assert "no-store" in c, "a cached maintenance page outlives the maintenance"


def test_the_page_is_self_contained():
    # During the window, the stack that would serve fonts, CSS or images is
    # exactly what is down.
    html = _read("deploy", "maintenance", "index.html")
    for pattern in ("src=\"http", "href=\"http://", "@import", "cdn."):
        assert pattern not in html.replace('href="https://github.com', ''), \
            f"external reference {pattern!r}: it will not load during maintenance"


def test_the_page_speaks_both_languages():
    html = _read("deploy", "maintenance", "index.html")
    assert "Maintenance en cours" in html and "Maintenance in progress" in html


def test_the_script_refuses_to_fight_the_real_caddy():
    sh = _read("deploy", "maintenance.sh")
    assert "REFUSING" in sh, "starting it while the real caddy holds :8085 must fail loudly"
    assert "8085" in sh
