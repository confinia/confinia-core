"""Front-end dependency pinning for the demo (issue #103).

The demo loads MapLibre from a CDN. Two rules, both learned the hard way:

1. **Pin an exact version.** A floating `@5` ships an unattended upgrade on the
   most public page we have (time-slider.confinia.io, linked from the OSM and
   OHM posts and the OpenCage backlink).
2. **Stay on v5 for now.** v6 is ESM-only (`dist/maplibre-gl.js` returns 404,
   which alone would take the demo down) and hangs when rendered headlessly on
   software WebGL, which is exactly how we capture screenshots and GIFs.

Reproduced independently on our own VM (Playwright + CARTO style, not the
reporter's Puppeteer + demotiles), 2026-08-03:

    maplibre 5.24.0   styledata=1 load=1 idle=1 error=null
    maplibre 6.1.0    styledata=1 load=0 idle=0 error=null

Upstream: maplibre/maplibre-gl-js#8074 (open, "need more info").

**How to check this before ever attempting v6 again**: instrument `load` and
`idle`, not pixels. The failure emits **no error at all**, so counting console
errors sees nothing wrong, and a screenshot still shows the basemap while every
`map.on("load", ...)` callback silently never runs — which is where this demo
adds all of its sources and layers.
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
