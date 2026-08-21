"""The product version belongs beside the product name.

It was rendered only in `<span id="ver">`, at the very end of the footer, after
a wall of attributions -- INSEE, IGN, EuroGeographics, BKG, CBS/Kadaster, Stats
NZ, OpenStreetMap, CARTO -- where nobody reads it.

That matters beyond tidiness: on staging this element shows the CANDIDATE
version being validated, which is exactly the moment someone needs to see it at
a glance rather than hunt for it.

Measured in a browser, both viewports: 44 x 19 px, beside the brand, inside the
viewport at 440 px and at 1440 px.
"""
import os

SRC = open(os.path.join(os.path.dirname(__file__), "..", "demo", "index.html"),
           encoding="utf-8").read()


def test_the_version_sits_beside_the_brand():
    head = SRC.split("<header>")[1].split("</header>")[0]
    assert 'id="hver"' in head, "in the header, not only the footer"
    assert head.index("Confinia") < head.index('id="hver"'), "after the name it versions"


def test_it_is_filled_by_the_same_single_fetch():
    """Two fetches could disagree, and the footer's copy is a long-standing ops
    habit that must keep working."""
    assert SRC.count("/healthz") >= 1
    fill = SRC.split("if (d.version)")[1][:220]
    assert '$("ver")' in fill and '$("hver")' in fill, "one fetch fills both"


def test_it_is_quieter_than_the_brand_it_sits_beside():
    """A version badge that competes with the product name is a different bug
    from the one being fixed."""
    css = SRC.split(".hver {")[1].split("}")[0]
    assert "font-size: .72rem" in css
    assert "#9fb4d0" in css, "muted, not the accent reserved for actions"


def test_it_never_wraps_the_header():
    css = SRC.split(".hver {")[1].split("}")[0]
    assert "white-space: nowrap" in css, "a version must not break across lines"


def test_the_footer_copy_is_kept():
    assert 'id="ver"' in SRC.split("<footer>")[1].split("</footer>")[0]
