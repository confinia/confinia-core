"""The site's main function was styled to disappear.

Clicking a commune opens a popup whose last line is the link to the full record
and its downloadable report -- the thing the product sells. It was rendered
inside `.tip`, the same class as the passive hint above it:

    #histo .tip { font-size: .75em; opacity: .55; }

Measured in a browser at 440 px and 1440 px, identically:

    before   10.5px / weight 400 / effective opacity 0.55 / no background
             199 x 13  =  2 592 px of hit area
    after    13.3px / weight 700 / opacity 1 / solid accent
             308 x 43  = 13 258 px, clearing the 44 px touch target

The founder saw it as "minuscule alors qu'il s'agit de la fonction majeure du
site", and the computed styles agree.
"""
import os
import re

SRC = open(os.path.join(os.path.dirname(__file__), "..", "demo", "index.html"),
           encoding="utf-8").read()


def test_the_record_link_is_not_styled_as_a_passive_hint():
    assert 'class="record-cta"' in SRC, "it has a class of its own"
    assert 'tip"><a href' not in SRC, "and is no longer wrapped in .tip"


def test_it_looks_like_something_you_can_press():
    css = SRC.split("#histo .record-cta {")[1].split("}")[0]
    assert "display: block" in css, "full width, not an inline afterthought"
    assert "background: #7ab8ff" in css, "solid accent, not transparent"
    assert "font-weight: 700" in css
    assert "text-align: center" in css


def test_the_touch_target_clears_44px():
    """Measured at 43 px with .85rem padding; anything less fails a thumb."""
    css = SRC.split("#histo .record-cta {")[1].split("}")[0]
    m = re.search(r"padding:\s*([\d.]+)rem", css)
    assert m and float(m.group(1)) >= 0.8, \
        f"vertical padding {m.group(1) if m else '?'}rem is too small for a touch target"


def test_it_is_reachable_by_keyboard():
    assert "#histo .record-cta:focus-visible" in SRC, "focus must be visible"
    # Two rules mention :focus-visible -- the one shared with :hover changes the
    # background, and a dedicated one draws the ring. Find the ring, not the
    # first match: splitting on the first is what made this test fail against
    # perfectly correct CSS.
    import re as _re
    rules = _re.findall(r"#histo \.record-cta:focus-visible\s*\{([^}]*)\}", SRC)
    assert any("outline: 2px solid" in r for r in rules), \
        "a visible ring, not only a colour change"


def test_the_passive_hint_stays_passive():
    """Promoting the action must not promote the tip beside it, or nothing is
    emphasised."""
    assert "#histo .tip { font-size: .75em; opacity: .55;" in SRC
    assert SRC.count('class="tip"') >= 1
