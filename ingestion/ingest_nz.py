#!/usr/bin/env python3
"""Stats NZ Territorial Authorities -> temporal model (country = NZ).

Source: editions published on the Stats NZ ArcGIS Hub (CC BY 4.0 license),
downloadable without a key: 2010, 2013, 2018, 2023, 2025, 2026. Edition diff
like DE/NL/LAU: transitions at the January 1st of each edition (documented
approximation). The super-Auckland merger (seven councils merged in late
2010) appears between the 2010 and 2013 editions.

SCOPE: Territorial Authorities only (standard administrative boundaries).
The iwi / treaty layers are NOT ingested: indigenous sovereignty data, out
of scope without an explicit partnership.

Usage: ingest_nz.py [--download] --data-dir /data/raw/nz
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import date

from shapely.geometry import mapping, shape

SOURCE = "statsnz"
UNIT_TYPE = "ta"
FAR_FUTURE = date(9999, 1, 1)
YEARS_KNOWN = [2010, 2013, 2018, 2023, 2025, 2026]
URL = ("https://services2.arcgis.com/vKb0s8tBIA3bdocZ/arcgis/rest/services/"
       "Territorial_Authority_{y}/FeatureServer/0/query"
       "?where=1%3D1&outFields=*&outSR=4326&f=geojson")


def download(data_dir: str) -> None:
    import urllib.request
    os.makedirs(data_dir, exist_ok=True)
    for y in YEARS_KNOWN:
        out = os.path.join(data_dir, f"ta_{y}.geojson")
        if os.path.getsize(out) > 1000 if os.path.exists(out) else False:
            continue
        try:
            urllib.request.urlretrieve(URL.format(y=y), out)
        except Exception as e:
            print(f"  {y}: download failed ({e})")


def read_edition(path: str) -> dict[str, tuple[str, object]]:
    d = json.load(open(path, encoding="utf-8"))
    feats = d.get("features") or []
    if not feats:
        raise SystemExit(f"Empty edition: {path}: refusing to ingest.")
    props0 = feats[0]["properties"]
    code_key = next(k for k in props0 if re.fullmatch(r"TA\d{4}_V\d_00", k))
    name_key = code_key + "_NAME"
    out = {}
    for f in feats:
        p = f["properties"]
        code = str(p[code_key]).zfill(3)
        # 999 = "Area Outside Territorial Authority": a TECHNICAL unit
        # (oceans, areas outside any TA) whose polygon overlaps the whole
        # country and captures point-in-polygon queries. Excluded, like the
        # phantom UN unit of the LAU.
        if code == "999":
            continue
        out[code] = (str(p[name_key]).strip(), f["geometry"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/data/raw/nz")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN"))
    args = ap.parse_args()
    if args.download:
        download(args.data_dir)

    years = sorted(int(re.search(r"ta_(\d{4})", p).group(1))
                   for p in glob.glob(os.path.join(args.data_dir, "ta_*.geojson")))
    if not years:
        raise SystemExit("No ta_YYYY.geojson edition: run with --download.")
    states = {y: read_edition(os.path.join(args.data_dir, f"ta_{y}.geojson"))
              for y in years}
    for y in years:
        print(f"  edition {y}: {len(states[y])} territorial authorities")

    # Edition diff -> periods; geometry of the STARTING edition.
    periods = []                                  # (code, name, start_y, end_y|None)
    open_: dict[str, tuple[str, int]] = {}
    for y in years:
        cur = states[y]
        for code, (nom, start) in list(open_.items()):
            if code not in cur:
                periods.append((code, nom, start, y))
                del open_[code]
            elif cur[code][0] != nom:
                periods.append((code, nom, start, y))
                open_[code] = (cur[code][0], y)
        for code, (nom, _g) in cur.items():
            if code not in open_:
                open_[code] = (nom, y)
    for code, (nom, start) in open_.items():
        periods.append((code, nom, start, None))
    print(f"{len(periods)} TA periods")

    import psycopg2
    conn = psycopg2.connect(args.dsn)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM commune_version WHERE source=%s", (SOURCE,))
        print(f"{cur.rowcount} old rows deleted (idempotent replay)")
        for code, nom, start, end in periods:
            geom = shape(states[start][code][1])
            gj = json.dumps(mapping(geom))
            gj_s = json.dumps(mapping(geom.simplify(0.002, preserve_topology=True)))
            cur.execute(
                "INSERT INTO commune_version (code, nom, valid_from, valid_to, "
                " unit_type, country, source, geometry_vintage, geometry_approx, "
                " geom, geom_simple) "
                "VALUES (%s,%s,%s,%s,%s,'NZ',%s,%s,true, "
                " ST_SetSRID(ST_GeomFromGeoJSON(%s),4326), "
                " ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))",
                (code, nom, date(start, 1, 1),
                 date(end, 1, 1) if end else FAR_FUTURE,
                 UNIT_TYPE, SOURCE, date(start, 1, 1), gj, gj_s))
        ok = True
        for probe, edition in ((2011, 2010), (2015, 2013), (2024, 2023)):
            d = date(probe, 6, 1)
            cur.execute("SELECT count(*) FROM commune_version "
                        "WHERE source=%s AND valid_from<=%s AND valid_to>%s",
                        (SOURCE, d, d))
            got, want = cur.fetchone()[0], len(states[edition])
            tag = "OK " if got == want else "MISMATCH"
            ok = ok and got == want
            print(f"  check {d}: rebuilt {got} vs edition {edition} ({want})  {tag}")
        cur.execute("SELECT nom, valid_from, valid_to FROM commune_version "
                    "WHERE source=%s AND nom LIKE '%%Auckland%%' ORDER BY valid_from",
                    (SOURCE,))
        for nom, vf, vt in cur.fetchall():
            print(f"  Auckland: {nom} {vf} -> {vt if vt != FAR_FUTURE else 'today'}")
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
