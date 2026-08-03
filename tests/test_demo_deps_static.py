"""Front-end dependency pinning for the demo (issue #103).

The demo loads MapLibre from a CDN. Two rules:

1. **Pin an exact version.** A floating `@5` ships an unattended upgrade on the
   most public page we have (time-slider.confinia.io, linked from the OSM and
   OHM posts and the OpenCage backlink).

2. **Stay on v5 while we load from a CDN.** v6 is ESM-only and splits into
   three chunks (`maplibre-gl.mjs`, `maplibre-gl-shared.mjs`,
   `maplibre-gl-worker.mjs`). Loaded cross-origin from a CDN, the worker chunk
   never starts and the map silently never finishes loading. Self-hosted, v6
   works: see issue #105.

Measured on our VM, 2026-08-03 (Playwright, Chromium 131, SwiftShader):

    6.1.0 from unpkg (CDN)          styledata=1 load=0 idle=0 error=null
    6.1.0 self-hosted, all chunks   styledata=1 load=1 idle=1 error=null

**The signature is what matters, and it is why this took three wrong turns to
diagnose**: whenever the worker fails, `load` and `idle` never fire and **no
error is emitted at all**. Counting console errors sees a healthy page. A
screenshot still shows the basemap, because only the `map.on("load", ...)`
callbacks are skipped, and that is exactly where this demo adds every source and
layer. So the map looks plausible and carries no data.

**Verify by instrumenting `load` and `idle`, never by pixels or error counts.**

Two self-inflicted repeats of the same silent failure, worth knowing: serving
`.mjs` as `application/octet-stream` (browsers reject it for modules), and
self-hosting only two of the three chunks. Same symptom, no error, both times.

Upstream on the silence: maplibre/maplibre-gl-js#8074.
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


def test_demo_stays_on_v5_while_loading_from_a_cdn():
    html = _html()
    from_cdn = "unpkg.com/maplibre-gl@" in html
    urls = re.findall(r"unpkg\.com/maplibre-gl@([^/]+)/", html)
    if from_cdn:
        assert all(v.startswith("5.") for v in urls), (
            "v6 loaded cross-origin from a CDN never starts its worker chunk and "
            "hangs silently; self-host all three chunks first (issue #105)")
        # the UMD bundle only exists in v5; v6 would 404 on this path
        assert "maplibre-gl.js" in html
