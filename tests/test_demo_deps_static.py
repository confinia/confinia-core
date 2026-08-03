"""Front-end dependency pinning for the demo (issue #103).

The demo loads MapLibre from a CDN. Two rules, both learned the hard way:

1. **Pin an exact version.** A floating `@5` ships an unattended upgrade on the
   most public page we have (time-slider.confinia.io, linked from the OSM and
   OHM posts and the OpenCage backlink).
2. **Stay on v5 for now.** v6 is ESM-only (`dist/maplibre-gl.js` returns 404,
   which alone would take the demo down) and has a known headless 3D rendering
   bug, which matters because we capture screenshots and GIFs headlessly.
"""
import os
import re

DEMO = os.path.join(os.path.dirname(__file__), "..", "demo", "index.html")


def _html():
    return open(DEMO, encoding="utf-8").read()


def test_maplibre_version_is_pinned_exactly():
    urls = re.findall(r"unpkg\.com/maplibre-gl@([^/]+)/", _html())
    assert urls, "the demo no longer loads MapLibre from unpkg"
    for v in urls:
        assert re.fullmatch(r"\d+\.\d+\.\d+", v), \
            f"floating MapLibre version {v!r}: pin an exact x.y.z"


def test_demo_stays_on_maplibre_v5():
    urls = re.findall(r"unpkg\.com/maplibre-gl@([^/]+)/", _html())
    assert all(v.startswith("5.") for v in urls), \
        "moving to v6 needs the ESM migration AND the headless 3D bug resolved"
    # the UMD bundle only exists in v5; v6 would 404 here
    assert "maplibre-gl.js" in _html()
