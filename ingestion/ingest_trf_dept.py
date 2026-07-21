#!/usr/bin/env python3
"""TRF-GIS départements (Gay, CC BY 4.0) -> modèle temporel AVEC géométries.

Source : DEPARTEMENTS_YYYY.zip (shapefile Lambert-93 par année, 1870-1940).
Diff annuel sur (code, nom) comme ingest_trf ; chaque période porte la
géométrie de l'édition de son année de DÉBUT (geometry_vintage = cette année,
geometry_approx = true : les retouches de tracé internes à une période ne
sont pas suivies en v1). Périodes vivantes en 1940 : coupées à 1943-01-01
(plancher du modèle moderne). unit_type='departement', source='trf-gis'.

C'est la couche du « mode historique » de la démo (1870-1940) et la matière
de l'export admin_level=6 pour OpenHistoricalMap.

Usage : ingest_trf_dept.py --data-dir /data/raw/trf/departements
"""
from __future__ import annotations

import argparse
import glob
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
UNIT_TYPE = "departement"
L93_TO_WGS84 = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)


def proper_dept_names(communes_dir: str, year: int) -> dict[str, str]:
    """{code dept: nom ACCENTUÉ} depuis les originaux communes (dep_name_prop) :
    le dbf TRF des départements est désaccentué (CHARENTE-INFERIEURE)."""
    import io as _io
    txt = os.path.join(communes_dir, f"COG_COMMUNES_{year}.txt")
    if not os.path.exists(txt):
        return {}
    raw = open(txt, "rb").read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    if "\ufffd" in text:
        raise SystemExit(f"Source corrompue (U+FFFD) : {txt} : refus d'ingérer.")
    import csv as _csv
    out = {}
    for row in _csv.DictReader(_io.StringIO(text)):
        code = str(row.get("dep", "")).zfill(2)
        nom = (row.get("dep_name_prop") or "").strip()
        if code != "00" and nom:
            out.setdefault(code, nom)
    return out


def read_year(base: str, year: int, names: dict[str, str] | None = None
              ) -> dict[str, tuple[str, object]]:
    """{code: (nom, geom_wgs84)} pour une année."""
    zpath = os.path.join(base, f"DEPARTEMENTS_{year}.zip")
    xdir = os.path.join(base, "extract", str(year))
    if not os.path.isdir(xdir):
        os.makedirs(xdir, exist_ok=True)
        zipfile.ZipFile(zpath).extractall(xdir)
    shp = glob.glob(os.path.join(xdir, "**", "*.shp"), recursive=True)[0]
    out = {}
    r = shapefile.Reader(shp)
    fields = [f[0] for f in r.fields[1:]]
    for sr in r.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        code = str(rec["dep_id"]).zfill(2)
        nom = (names or {}).get(code) or str(rec["dep_name"]).strip().title()
        geom = shp_transform(lambda x, y: L93_TO_WGS84.transform(x, y),
                             shape(sr.shape.__geo_interface__))
        out[code] = (nom, geom)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/data/raw/trf/departements")
    ap.add_argument("--communes-dir", default="/data/raw/trf/communes")
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN"))
    args = ap.parse_args()

    states = {}
    for y in YEARS:
        states[y] = read_year(args.data_dir, y,
                              proper_dept_names(args.communes_dir, y))
    for y in (1870, 1875, 1900, 1921, 1940):
        print(f"  {y}: {len(states[y])} départements")

    # Diff annuel -> périodes (code, nom, début, fin|None), géométrie du début.
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
    print(f"{len(periods)} périodes départementales")

    import psycopg2
    conn = psycopg2.connect(args.dsn)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM commune_version WHERE source=%s AND unit_type=%s",
                    (SOURCE, UNIT_TYPE))
        print(f"{cur.rowcount} anciennes lignes supprimées (rejeu idempotent)")
        for code, nom, start, end in periods:
            geom = states[start][code][1]
            gj = json.dumps(mapping(geom))
            gj_simple = json.dumps(mapping(geom.simplify(0.002, preserve_topology=True)))
            cur.execute(
                "INSERT INTO commune_version (code, nom, valid_from, valid_to, "
                " unit_type, country, source, geometry_vintage, geometry_approx, "
                " geom, geom_simple) "
                "VALUES (%s,%s,%s,%s,%s,'FR',%s,%s,true, "
                " ST_SetSRID(ST_GeomFromGeoJSON(%s),4326), "
                " ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))",
                (code, nom, date(start, 1, 1),
                 date(end, 1, 1) if end else date(1943, 1, 1),
                 UNIT_TYPE, SOURCE, date(start, 1, 1), gj, gj_simple))
        ok = True
        for y in (1875, 1900, 1921, 1939):
            d = date(y, 6, 1)
            cur.execute("SELECT count(*) FROM commune_version "
                        "WHERE source=%s AND unit_type=%s AND valid_from<=%s AND valid_to>%s",
                        (SOURCE, UNIT_TYPE, d, d))
            got, want = cur.fetchone()[0], len(states[y])
            tag = "OK " if got == want else "ECART"
            ok = ok and got == want
            print(f"  contrôle {d}: reconstruit {got} vs édition {want}  {tag}")
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
