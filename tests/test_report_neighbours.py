"""Neighbouring communes drawn behind each boundary card (issue #96).

A boundary means nothing against an empty background. Each period card now draws
the units touching the target AT THAT PERIOD's date, subdued and behind it.

The fixture makes the period-awareness visible: 99902 borders 99901 until the
2019 merger, and disappears afterwards. So the first card must show a neighbour
and the second must not. Taking today's neighbours for an old outline would be a
silent anachronism, which is the whole reason the query is dated.
"""
import requests


def _svg(base, code="99901", **params):
    r = requests.get(f"{base}/v1/communes/{code}/report.svg", params=params)
    assert r.status_code == 200, r.text
    return r.text


def test_neighbours_are_drawn_behind_the_target(base):
    svg = _svg(base)
    assert 'fill="#eef1f6"' in svg, "no neighbour drawn"
    assert 'fill="#dbe7fb"' in svg, "the target itself is missing"
    # order matters: the target must sit on top, so it is drawn last
    assert svg.index('fill="#eef1f6"') < svg.index('fill="#dbe7fb"')


def test_neighbours_are_clipped_to_their_card(base):
    # a neighbour extends past the frame by design; without a clip it would
    # bleed into the next card
    assert "clipPath" in _svg(base)


def test_the_neighbourhood_follows_the_period(base):
    d = requests.get(f"{base}/v1/communes/99901/history").json()
    assert len(d["versions"]) == 2
    # 99902 borders 99901 before the merger and is gone after it, so exactly one
    # of the two cards carries a neighbour
    svg = _svg(base)
    assert svg.count('fill="#eef1f6"') == 1


def test_a_period_without_geometry_shows_no_neighbours(base):
    # pre-1943 cards have no outline at all; they must keep saying so rather
    # than drawing neighbours around an absent shape
    svg = _svg(base, code="99910")          # Splitville, 1943 onward
    assert "aucune géométrie" in svg or 'fill="#dbe7fb"' in svg


def test_pdf_still_renders_with_neighbours(base):
    r = requests.get(f"{base}/v1/communes/99901/report.pdf")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF") and len(r.content) > 2000


# --- The same context on the commune page (issue #96) ------------------------

def test_history_exposes_neighbours_only_on_demand(base):
    plain = requests.get(f"{base}/v1/communes/99901/history",
                         params={"geometry": "true"}).json()
    assert not any("neighbours" in f["properties"] for f in plain["versions"]), \
        "neighbours must cost nothing unless asked for"
    asked = requests.get(f"{base}/v1/communes/99901/history",
                         params={"geometry": "true", "neighbours": "true"}).json()
    per_version = [len(f["properties"].get("neighbours", [])) for f in asked["versions"]]
    # dated again: a neighbour before the 2019 merger, none after it
    assert per_version[0] > 0 and per_version[-1] == 0


def test_commune_page_draws_the_neighbourhood():
    import os
    html = open(os.path.join(os.path.dirname(__file__), "..", "deploy", "site",
                             "commune.html"), encoding="utf-8").read()
    assert "neighbours=true" in html          # the page asks for them
    assert "clipPath" in html                 # and clips them to the card
    assert "#161d2b" in html                  # subdued fill, behind the target
