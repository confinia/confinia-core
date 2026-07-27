"""Embedded checkout wiring (issue #49): the pricing and account pages open the
Polar overlay on-page (no visible redirect) via the self-hosted embed script."""
import os

SITE = os.path.join(os.path.dirname(__file__), "..", "deploy", "site")


def _read(name):
    return open(os.path.join(SITE, name), encoding="utf-8").read()


def test_embed_script_is_self_hosted():
    js = _read("polar-embed.js")
    assert "EmbedCheckout" in js and len(js) > 5000       # the real Polar embed lib


def test_pricing_uses_overlay_not_redirect():
    html = _read("pricing.html")
    assert html.count("data-polar-checkout") >= 2         # both plan buttons
    assert '/polar-embed.js' in html                      # script loaded


def test_account_prefills_email_and_uses_overlay():
    html = _read("account.html")
    assert "data-polar-checkout" in html
    assert "customer_email=" in html                      # webhook match by email
    assert "/polar-embed.js" in html
