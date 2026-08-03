"""Front-end dependency rules for the demo (issues #103, #105).

The demo is the most public artefact we have: time-slider.confinia.io, linked
from the OSM and OHM posts and from the OpenCage backlink. Two rules.

1. **MapLibre is vendored, never loaded from a CDN.** A third party being slow,
   blocked or down must not take the map with it. Vendoring also unblocks v6,
   whose three ESM chunks hang when loaded cross-origin.

2. **The version lives in the path**, so an upgrade is a visible diff rather
   than something that happens to us.

Measured on the VM, 2026-08-03 (Playwright, Chromium 131, SwiftShader):

    6.1.0 from unpkg (CDN)          styledata=1 load=0 idle=0 error=null
    6.1.0 self-hosted, all chunks   styledata=1 load=1 idle=1 error=null

**Every failure in this family is silent**: no error event, nothing in the
console, and the basemap still renders because only the `map.on("load", ...)`
callbacks are skipped, which is exactly where this demo adds its sources and
layers. The map looks plausible and carries no data. It caught me three times:
the CDN worker chunk, `.mjs` served as `application/octet-stream`, and
self-hosting two chunks out of three.

**Verify by behaviour, never by pixels or error counts.** The check that works
is loading the page and reading a value that only exists once the data pipeline
ran, for example `#count` ("+0 new · ~6 changed · -14 gone").

Upstream on the silence itself: maplibre/maplibre-gl-js#8074.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_vendored_path_is_not_blocked_by_the_scanner_filter():
    """`/vendor/*` is a Composer/PHP probe path that our own anti-scanner filter
    aborts (deploy/caddy/Caddyfile, @scan_php). Serving vendored assets from
    there returns 502 in production and kills the map, which is exactly what
    happened on 2026-08-03. Keep the assets out of any blocked prefix."""
    html = _read("demo", "index.html")
    caddy = _read("deploy", "caddy", "Caddyfile")
    blocked = [p.strip("*/") for p in caddy.split("@scan_php path")[1].split("\n")[0].split()
               if p.startswith("/")]
    for ref in re.findall(r'(?:src|href)="([^"]+maplibre[^"]*)"', html):
        for b in blocked:
            assert not ref.startswith(b.lstrip("/") + "/"), \
                f"asset {ref!r} sits under the blocked prefix /{b}/"


def test_maplibre_is_not_loaded_from_a_cdn():
    html = _read("demo", "index.html")
    assert "unpkg.com/maplibre" not in html
    assert "cdn.jsdelivr.net/npm/maplibre" not in html
    assert "esm.sh/maplibre" not in html


def test_vendored_path_carries_an_explicit_version():
    html = _read("demo", "index.html")
    refs = re.findall(r"lib/maplibre/([^/]+)/", html)
    assert refs, "the demo no longer references a vendored MapLibre"
    for v in refs:
        assert re.fullmatch(r"\d+\.\d+\.\d+", v), \
            f"vendored path {v!r} must pin an exact x.y.z"


def test_vendored_files_are_present_with_their_licence():
    html = _read("demo", "index.html")
    v = re.findall(r"lib/maplibre/([^/]+)/", html)[0]
    base = os.path.join(ROOT, "demo", "lib", "maplibre", v)
    for f in ("maplibre-gl.js", "maplibre-gl.css", "LICENSE.txt"):
        path = os.path.join(base, f)
        assert os.path.exists(path), f"missing vendored file: {f}"
        assert os.path.getsize(path) > 100, f"vendored {f} looks truncated"


def test_publish_target_ships_the_whole_demo_directory():
    # Copying index.html alone would leave the vendored MapLibre behind and
    # break the map on GitHub Pages, silently. `git add -A` matters too: a new
    # directory is untracked, so `commit -am` would skip it.
    mk = _read("Makefile")
    block = mk.split("demo-publish:")[1].split("\n\n")[0]
    assert "rsync" in block and "demo/" in block, \
        "demo-publish must ship the whole demo/ directory, not index.html alone"
    assert "git add -A" in block, \
        "demo-publish must stage new files, or vendored assets never reach Pages"
