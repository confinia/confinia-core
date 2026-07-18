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
BOGUS = {"UN"}                  # artefacts GISCO (1 unité sans nom, édition 2022)


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
        if not cntr or not lau_id or cntr in NATIVE or cntr in BOGUS or not f.get("geometry"):
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

    # GISCO n'inclut pas tous les pays dans toutes les éditions (UK absent
    # après 2016, EL et PL intermittents…). La chronologie de chaque pays ne
    # doit compter QUE les éditions où il figure — sinon une édition manquante
    # se lit comme l'extinction de toutes ses communes.
    per_country: dict[str, dict[str, dict]] = {}   # cntr -> date -> {id: (nom, geom)}
    for d, snap in snapshots.items():
        for key, val in snap.items():
            cntr, _, lau_id = key.partition(":")
            per_country.setdefault(cntr, {}).setdefault(d, {})[lau_id] = val

    if args.dsn == "ENV":
        args.dsn = os.environ.get("PG_DSN") or sys.exit("--dsn sans valeur mais $PG_DSN absent.")

    grand_total = 0
    for cntr in sorted(per_country):
        # Une édition ne compte pour un pays que si elle est raisonnablement
        # complète. Référence = MÉDIANE des éditions présentes (pas le pic :
        # la réforme LAU 2017 a réellement fait passer le Danemark de 2 168
        # paroisses à 99 kommuner — c'est une transition à enregistrer, pas un
        # trou). Les vraies éditions partielles (PL 2023 : 14 unités sur
        # ~2 480) restent écartées.
        counts = sorted(len(per_country[cntr][d]) for d in per_country[cntr])
        median = counts[len(counts) // 2]
        cdates = [d for d in dates
                  if len(per_country[cntr].get(d, {})) >= max(1, median // 2)]
        gaps = [d for d in dates if d not in cdates]
        if gaps:
            print(f"  [i] {cntr} : éditions absentes/partielles ignorées : "
                  f"{', '.join(g[:4] for g in gaps)}")
        periods = build_periods(cdates, per_country[cntr])
        grand_total += len(periods)
        if args.dsn:
            load_postgis(periods, "lau", cntr, args.dsn)
    print(f"Périodes LAU reconstruites : {grand_total} sur {len(per_country)} pays")


if __name__ == "__main__":
    main()
