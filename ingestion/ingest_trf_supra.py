#!/usr/bin/env python3
"""TRF-GIS supra-communal levels (Gay, CC BY 4.0): arrondissements and
cantons 1870-1940 as first-class temporal units, with geometries.

Same discipline as ingest_trf_dept.py: yearly edition diff on (code, name),
each period carries the geometry of its STARTING year (geometry_vintage =
that year, geometry_approx = true), periods alive in 1940 are cut at
1943-01-01 (floor of the modern model). Accented names come from the
original COG_*.txt nomenclature files (the shapefile DBFs are unaccented);
any U+FFFD in a source is a hard refusal.

Codes are nationally unique composites: departement (2 digits) + unit
number (2 digits), e.g. arrondissement 0101, canton 0104.

Usage: ingest_trf_supra.py --data-dir /data/raw/trf [--levels arrondissement,canton]
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import os
import sys
import zipfile
from datetime import date

import shapefile  # pyshp
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform

YEARS = list(range(1870, 1941))
SOURCE = "trf-gis"
L93_TO_WGS84 = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)

LEVELS = {
    "arrondissement": {
        "dir": "arrondissements", "zip": "ARRONDISSEMENTS_{y}.zip",
        "txt": "COG_ARRONDISSEMENTS_{y}.txt",
        "dbf_num": "ar", "txt_num": "ar", "txt_name": "ar_name_prop",
        "simplify": 0.002,
    },
    "canton": {
        "dir": "cantons", "zip": "CANTONS_{y}.zip",
        "txt": "COG_CANTONS_{y}.txt",
        "dbf_num": "pct", "txt_num": "ct", "txt_name": "ct_name_prop",
        "simplify": 0.001,
    },
}


def code_of(dep, num) -> str:
    return f"{str(dep).zfill(2)}{int(num):02d}"


def proper_names(base: str, cfg: dict, year: int) -> dict[str, str]:
    """{composite code: accented name} from the original nomenclature txt."""
    path = os.path.join(base, cfg["dir"], cfg["txt"].format(y=year))
    if not os.path.exists(path):
        return {}
    raw = open(path, "rb").read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    if "�" in text:
        raise SystemExit(f"Corrupted source (U+FFFD): {path}: refusing to ingest.")
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        nom = (row.get(cfg["txt_name"]) or "").strip()
        try:
            code = code_of(row["dep"], row[cfg["txt_num"]])
        except (KeyError, ValueError):
            continue
        if nom:
            out.setdefault(code, nom)
    return out


def read_year(base: str, cfg: dict, year: int) -> dict[str, tuple[str, object]]:
    """{composite code: (name, wgs84 geometry)} for one yearly edition."""
    zpath = os.path.join(base, cfg["dir"], cfg["zip"].format(y=year))
    xdir = os.path.join(base, cfg["dir"], "extract", str(year))
    if not os.path.isdir(xdir):
        os.makedirs(xdir, exist_ok=True)
        zipfile.ZipFile(zpath).extractall(xdir)
    shp = glob.glob(os.path.join(xdir, "**", "*.shp"), recursive=True)[0]
    names = proper_names(base, cfg, year)
    out = {}
    r = shapefile.Reader(shp)
    fields = [f[0] for f in r.fields[1:]]
    for sr in r.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        try:
            code = code_of(rec["dep"], rec[cfg["dbf_num"]])
        except (KeyError, ValueError):
            continue
        nom = names.get(code) or str(rec.get(f"{cfg['dbf_num']}_name", "")).strip().title()
        geom = shp_transform(lambda x, y: L93_TO_WGS84.transform(x, y),
                             shape(sr.shape.__geo_interface__))
        prev = out.get(code)
        # A few units span several polygons (multi-part records): union them.
        out[code] = (nom, geom if prev is None else prev[1].union(geom))
    return out


def ingest_level(conn, base: str, unit_type: str) -> bool:
    cfg = LEVELS[unit_type]
    states = {y: read_year(base, cfg, y) for y in YEARS}
    for y in (1870, 1900, 1940):
        print(f"  {unit_type} {y}: {len(states[y])} units")

    periods = []
    open_: dict[str, tuple[str, int]] = {}
    for y in YEARS:
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
    print(f"  {len(periods)} {unit_type} periods")

    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM commune_version WHERE source=%s AND unit_type=%s",
                    (SOURCE, unit_type))
        print(f"  {cur.rowcount} previous rows deleted (idempotent replay)")
        for code, nom, start, end in periods:
            geom = states[start][code][1]
            gj = json.dumps(mapping(geom))
            gj_s = json.dumps(mapping(geom.simplify(cfg["simplify"],
                                                    preserve_topology=True)))
            cur.execute(
                "INSERT INTO commune_version (code, nom, valid_from, valid_to, "
                " unit_type, country, source, geometry_vintage, geometry_approx, "
                " geom, geom_simple) "
                "VALUES (%s,%s,%s,%s,%s,'FR',%s,%s,true, "
                " ST_SetSRID(ST_GeomFromGeoJSON(%s),4326), "
                " ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))",
                (code, nom, date(start, 1, 1),
                 date(end, 1, 1) if end else date(1943, 1, 1),
                 unit_type, SOURCE, date(start, 1, 1), gj, gj_s))
        ok = True
        for y in (1875, 1900, 1921, 1939):
            d = date(y, 6, 1)
            cur.execute("SELECT count(*) FROM commune_version "
                        "WHERE source=%s AND unit_type=%s AND valid_from<=%s AND valid_to>%s",
                        (SOURCE, unit_type, d, d))
            got, want = cur.fetchone()[0], len(states[y])
            tag = "OK " if got == want else "DRIFT"
            ok = ok and got == want
            print(f"  control {d}: rebuilt {got} vs edition {want}  {tag}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/data/raw/trf")
    ap.add_argument("--levels", default="arrondissement,canton")
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN"))
    args = ap.parse_args()

    import psycopg2
    conn = psycopg2.connect(args.dsn)
    ok = True
    for unit_type in args.levels.split(","):
        print(f"== {unit_type}")
        ok = ingest_level(conn, args.data_dir, unit_type.strip()) and ok
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
