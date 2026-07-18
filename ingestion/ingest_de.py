#!/usr/bin/env python3
"""
Ingestion Allemagne : Gemeinden des éditions annuelles BKG VG250 (Stand 01.01).

Source : daten.gdz.bkg.bund.de, archives 2016→2025 (~70 Mo/édition, shapefile
UTM32s). Couche VG250_GEM : AGS (8 chiffres), GEN (nom), GF (4 = territoire
avec structure — on ne garde que ceux-là pour éviter les doublons plans d'eau).

Licence : Datenlizenz Deutschland – Namensnennung 2.0 (dl-de/by-2-0).
Attribution obligatoire : « © GeoBasis-DE / BKG (ANNÉE), dl-de/by-2-0 »
+ mention de modification (reprojection, simplification).

Modèle temporel : diff de snapshots (voir ingest_snapshots.py) — transitions
approchées aux 1ers janvier ; les Gebietsänderungen Destatis affineront.
"""
from __future__ import annotations
import argparse
import io
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

import shapefile  # pyshp
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform as shp_transform, unary_union

from ingest_snapshots import build_periods, load_postgis, sanity

URL = ("https://daten.gdz.bkg.bund.de/produkte/vg/vg250_ebenen_0101/"
       "{y}/vg250_01-01.utm32s.shape.ebenen.zip")
T_25832 = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)


def load_year(data_dir: Path, y: int, download: bool) -> dict[str, tuple]:
    z = data_dir / f"vg250_{y}.zip"
    if not z.exists():
        if not download:
            sys.exit(f"{z} absent (utiliser --download).")
        print(f"  téléchargement VG250 {y}…")
        urllib.request.urlretrieve(URL.format(y=y), z)

    with zipfile.ZipFile(z) as zf:
        members = {}
        for n in zf.namelist():
            m = re.search(r"VG250_GEM\.(shp|shx|dbf|cpg)$", n, re.IGNORECASE)
            if m:
                members[m.group(1).lower()] = n
        if not {"shp", "shx", "dbf"} <= set(members):
            sys.exit(f"VG250_GEM introuvable dans {z} ({sorted(members)})")
        shp = io.BytesIO(zf.read(members["shp"]))
        shx = io.BytesIO(zf.read(members["shx"]))
        dbf = io.BytesIO(zf.read(members["dbf"]))
        # Encodage : le .cpg fait foi (éditions récentes en UTF-8 — « München »,
        # pas « MÃ¼nchen ») ; à défaut, latin-1.
        enc = "latin-1"
        if "cpg" in members:
            cpg = zf.read(members["cpg"]).decode("ascii", "ignore").strip().upper()
            if "UTF" in cpg:
                enc = "utf-8"

    per_ags: dict[str, tuple] = {}
    parts: dict[str, list] = {}
    with shapefile.Reader(shp=shp, shx=shx, dbf=dbf, encoding=enc) as r:
        fields = [f[0] for f in r.fields[1:]]
        i_ags, i_gen = fields.index("AGS"), fields.index("GEN")
        i_gf = fields.index("GF") if "GF" in fields else None
        for rec, sh in zip(r.iterRecords(), r.iterShapes()):
            if i_gf is not None and rec[i_gf] != 4:
                continue                      # que le territoire avec structure
            ags = str(rec[i_ags]).strip()
            if not ags:
                continue
            g = shp_transform(T_25832.transform, shape(sh.__geo_interface__))
            parts.setdefault(ags, []).append(g)
            per_ags[ags] = (str(rec[i_gen]).strip(),)
    out = {}
    for ags, (nom,) in per_ags.items():
        gs = parts[ags]
        out[ags] = (nom, gs[0] if len(gs) == 1 else unary_union(gs))
    print(f"  [ok] VG250 {y} : {len(out)} Gemeinden")
    return out


def main():
    ap = argparse.ArgumentParser(description="Ingestion Gemeinden DE (BKG VG250) -> PostGIS")
    ap.add_argument("--years", type=int, nargs="+", default=list(range(2016, 2026)))
    ap.add_argument("--data-dir", type=Path, default=Path("/data/raw/de"))
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--dsn", nargs="?", const="ENV", default=None, metavar="DSN")
    args = ap.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)

    years = sorted(args.years)
    dates = [f"{y}-01-01" for y in years]
    snapshots = {f"{y}-01-01": load_year(args.data_dir, y, args.download) for y in years}
    periods = build_periods(dates, snapshots)
    print(f"Périodes Gemeinden reconstruites : {len(periods)}")
    sanity(periods, dates, "DE")

    if args.dsn == "ENV":
        args.dsn = os.environ.get("PG_DSN") or sys.exit("--dsn sans valeur mais $PG_DSN absent.")
    if args.dsn:
        load_postgis(periods, "gemeinde", "DE", args.dsn)


if __name__ == "__main__":
    main()
