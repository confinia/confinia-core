#!/usr/bin/env python3
"""Stats NZ Territorial Authorities -> modèle temporel (country = NZ).

Source : éditions publiées sur l'ArcGIS Hub de Stats NZ (licence CC BY 4.0),
téléchargeables sans clé : 2010, 2013, 2018, 2023, 2025, 2026. Diff
d'éditions comme DE/NL/LAU : transitions aux 1ers janvier d'édition
(approximation documentée). La fusion du super-Auckland (sept conseils
fusionnés fin 2010) apparaît entre les éditions 2010 et 2013.

PÉRIMÈTRE : Territorial Authorities uniquement (limites administratives
standard). Les couches iwi / traités ne sont PAS ingérées : données de
souveraineté autochtone, hors périmètre sans partenariat explicite.

Usage : ingest_nz.py [--download] --data-dir /data/raw/nz
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
            print(f"  {y}: téléchargement impossible ({e})")


def read_edition(path: str) -> dict[str, tuple[str, object]]:
    d = json.load(open(path, encoding="utf-8"))
    feats = d.get("features") or []
    if not feats:
        raise SystemExit(f"Édition vide : {path} : refus d'ingérer.")
    props0 = feats[0]["properties"]
    code_key = next(k for k in props0 if re.fullmatch(r"TA\d{4}_V\d_00", k))
    name_key = code_key + "_NAME"
    out = {}
    for f in feats:
        p = f["properties"]
        code = str(p[code_key]).zfill(3)
        # 999 = « Area Outside Territorial Authority » : unité TECHNIQUE
        # (océans, zones hors TA) dont le polygone chevauche le pays entier
        # et capte les requêtes point-dans-polygone. Exclue, comme l'unité
        # fantôme UN du LAU.
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
        raise SystemExit("Aucune édition ta_YYYY.geojson : lancer avec --download.")
    states = {y: read_edition(os.path.join(args.data_dir, f"ta_{y}.geojson"))
              for y in years}
    for y in years:
        print(f"  édition {y}: {len(states[y])} territorial authorities")

    # Diff d'éditions -> périodes ; géométrie de l'édition de DÉBUT.
    periods = []                                  # (code, nom, y_debut, y_fin|None)
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
    print(f"{len(periods)} périodes TA")

    import psycopg2
    conn = psycopg2.connect(args.dsn)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM commune_version WHERE source=%s", (SOURCE,))
        print(f"{cur.rowcount} anciennes lignes supprimées (rejeu idempotent)")
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
            tag = "OK " if got == want else "ECART"
            ok = ok and got == want
            print(f"  contrôle {d}: reconstruit {got} vs édition {edition} ({want})  {tag}")
        cur.execute("SELECT nom, valid_from, valid_to FROM commune_version "
                    "WHERE source=%s AND nom LIKE '%%Auckland%%' ORDER BY valid_from",
                    (SOURCE,))
        for nom, vf, vt in cur.fetchall():
            print(f"  Auckland : {nom} {vf} -> {vt if vt != FAR_FUTURE else 'today'}")
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
