#!/usr/bin/env python3
"""
Netherlands ingestion: gemeenten from the annual CBS editions (via PDOK).

Source: service.pdok.nl, WFS « cbs/gebiedsindelingen/{year} », layer
gemeente_gegeneraliseerd (statcode GM0363, statnaam). Editions 2016→2026.
License: CC BY 4.0 (CBS / Kadaster) — attribution required.

Dutch herindelingen take effect on January 1st: the diff of annual snapshots
(ingest_snapshots.py) is nearly exact there.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

from ingest_snapshots import build_periods, load_postgis, sanity

URL = ("https://service.pdok.nl/cbs/gebiedsindelingen/{y}/wfs/v1_0"
       "?service=WFS&version=2.0.0&request=GetFeature"
       "&typeName=gebiedsindelingen:gemeente_gegeneraliseerd"
       "&outputFormat=application/json&srsName=EPSG:4326")
T_28992 = Transformer.from_crs("EPSG:28992", "EPSG:4326", always_xy=True)


def first_coord(geom: dict):
    c = geom["coordinates"]
    while isinstance(c[0], (list, tuple)):
        c = c[0]
    return c


def load_year(data_dir: Path, y: int, download: bool) -> dict[str, tuple]:
    p = data_dir / f"gemeente_{y}.geojson"
    if not p.exists():
        if not download:
            sys.exit(f"{p} missing (use --download).")
        print(f"  downloading PDOK {y}…")
        with urllib.request.urlopen(URL.format(y=y), timeout=120) as r:
            p.write_bytes(r.read())
    feats = json.loads(p.read_text(encoding="utf-8"))["features"]
    out = {}
    for f in feats:
        pr = f["properties"]
        code = (pr.get("statcode") or "").strip()
        nom = (pr.get("statnaam") or "").strip()
        if not code or not f.get("geometry"):
            continue
        g = shape(f["geometry"])
        x, _ = first_coord(f["geometry"])
        if abs(x) > 180:                       # the WFS ignored srsName -> RD New
            g = shp_transform(T_28992.transform, g)
        out[code] = (nom, g)
    print(f"  [ok] gemeenten {y}: {len(out)}")
    return out


def main():
    ap = argparse.ArgumentParser(description="NL gemeenten ingestion (CBS/PDOK) -> PostGIS")
    ap.add_argument("--years", type=int, nargs="+", default=list(range(2016, 2027)))
    ap.add_argument("--data-dir", type=Path, default=Path("/data/raw/nl"))
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--dsn", nargs="?", const="ENV", default=None, metavar="DSN")
    args = ap.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)

    years = sorted(args.years)
    dates = [f"{y}-01-01" for y in years]
    snapshots = {f"{y}-01-01": load_year(args.data_dir, y, args.download) for y in years}
    periods = build_periods(dates, snapshots)
    print(f"Rebuilt gemeenten periods: {len(periods)}")
    sanity(periods, dates, "NL")

    if args.dsn == "ENV":
        args.dsn = os.environ.get("PG_DSN") or sys.exit("--dsn without a value but $PG_DSN is unset.")
    if args.dsn:
        load_postgis(periods, "gemeente", "NL", args.dsn)


if __name__ == "__main__":
    main()
