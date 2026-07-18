#!/usr/bin/env python3
"""
Ingestion LAU (Local Administrative Units, Eurostat GISCO) — la LARGEUR
européenne : toutes les communes/municipalités de l'UE (+ EFTA/candidats),
éditions annuelles généralisées 1:1M. © EuroGeographics — attribution requise.

Les pays disposant d'un adaptateur natif (FR exact-dates, DE VG250, NL CBS)
sont SAUTÉS ici : le natif prime en profondeur, LAU couvre le reste.

Modèle temporel : diff de snapshots annuels (ingest_snapshots.py) —
transitions approchées aux dates d'édition.

code = LAU_ID national (INSEE-like, AGS-like…), country = CNTR_CODE,
unit_type = 'lau'.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from shapely.geometry import shape

from ingest_snapshots import build_periods, load_postgis, sanity

URL = ("https://gisco-services.ec.europa.eu/distribution/v2/lau/geojson/"
       "LAU_RG_01M_{y}_4326.geojson")
NATIVE = {"FR", "DE", "NL"}     # pays couverts par un adaptateur dédié


def load_year(data_dir: Path, y: int, download: bool) -> dict[str, tuple]:
    p = data_dir / f"lau_{y}.geojson"
    if not p.exists():
        if not download:
            sys.exit(f"{p} absent (utiliser --download).")
        print(f"  téléchargement GISCO LAU {y} (~125 Mo)…")
        urllib.request.urlretrieve(URL.format(y=y), p)
    feats = json.loads(p.read_text(encoding="utf-8"))["features"]
    out = {}
    for f in feats:
        pr = f["properties"]
        cntr = (pr.get("CNTR_CODE") or "").strip()
        lau_id = str(pr.get("LAU_ID") or "").strip()
        nom = (pr.get("LAU_NAME") or "").strip()
        if not cntr or not lau_id or cntr in NATIVE or not f.get("geometry"):
            continue
        # clé = pays + id (les LAU_ID nationaux peuvent se recouper entre pays)
        out[f"{cntr}:{lau_id}"] = (nom, shape(f["geometry"]))
    print(f"  [ok] LAU {y} : {len(out)} unités (hors {sorted(NATIVE)})")
    return out


def main():
    ap = argparse.ArgumentParser(description="Ingestion LAU GISCO (largeur EU) -> PostGIS")
    ap.add_argument("--years", type=int, nargs="+", default=list(range(2016, 2024)))
    ap.add_argument("--data-dir", type=Path, default=Path("/data/raw/lau"))
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--dsn", nargs="?", const="ENV", default=None, metavar="DSN")
    args = ap.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)

    years = sorted(args.years)
    dates = [f"{y}-01-01" for y in years]
    snapshots = {f"{y}-01-01": load_year(args.data_dir, y, args.download) for y in years}
    periods = build_periods(dates, snapshots)
    # la clé "CNTR:ID" redevient code=ID + country=CNTR au chargement
    by_country: dict[str, list[dict]] = {}
    for p in periods:
        cntr, _, lau_id = p["code"].partition(":")
        p["code"] = lau_id
        by_country.setdefault(cntr, []).append(p)
    print(f"Périodes LAU reconstruites : {len(periods)} sur {len(by_country)} pays")
    sanity(periods, dates, "LAU")

    if args.dsn == "ENV":
        args.dsn = os.environ.get("PG_DSN") or sys.exit("--dsn sans valeur mais $PG_DSN absent.")
    if args.dsn:
        for cntr in sorted(by_country):
            load_postgis(by_country[cntr], "lau", cntr, args.dsn)


if __name__ == "__main__":
    main()
