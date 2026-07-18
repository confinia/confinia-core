#!/usr/bin/env python3
"""
Ingestion des régions NUTS (Eurostat GISCO) vers le modèle temporel.

Source : gisco-services.ec.europa.eu, couches NUTS_RG_20M_<année>_4326.geojson
(7 versions : 2003, 2006, 2010, 2013, 2016, 2021, 2024 — attributs NUTS_ID,
LEVL_CODE 0..3, CNTR_CODE, NUTS_NAME). © EuroGeographics pour les limites
administratives — attribution obligatoire.

Modèle : une ligne = un (code, nom) valide sur [valid_from, valid_to), avec
unit_type = nuts0..nuts3 et country = CNTR_CODE. Les dates de transition sont
les dates d'entrée en application officielles des versions NUTS (pas les
millésimes). Les versions consécutives où (code, nom) est inchangé fusionnent
en une seule période ; la géométrie retenue est celle de la dernière version
de la période (geometry_vintage la date correspondante).

Limites v1 (documentées) : parents = père hiérarchique (préfixe du code),
children vides — les correspondances entre versions (splits/merges NUTS)
viendront des tables de correspondance Eurostat plus tard.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

FAR_FUTURE = "9999-01-01"

# Dates d'entrée en application officielles des versions NUTS.
APPLICATION = {
    2003: "2003-07-11",   # règlement (CE) n° 1059/2003
    2006: "2008-01-01",
    2010: "2012-01-01",
    2013: "2015-01-01",
    2016: "2018-01-01",
    2021: "2021-01-01",
    2024: "2024-01-01",
}
GISCO_URL = ("https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
             "NUTS_RG_20M_{y}_4326.geojson")

NUTS_INSERT = """
    INSERT INTO commune_version
      (code, nom, unit_type, country, valid_from, valid_to, parents, children,
       geometry_vintage, geometry_approx, geom, geom_simple)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,false,
        ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),
        ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))
"""


def load_version(data_dir: Path, y: int, download: bool) -> dict[str, tuple]:
    p = data_dir / f"NUTS_RG_20M_{y}_4326.geojson"
    if not p.exists():
        if not download:
            sys.exit(f"{p} absent (utiliser --download).")
        print(f"  téléchargement GISCO {y}…")
        urllib.request.urlretrieve(GISCO_URL.format(y=y), p)
    feats = json.loads(p.read_text(encoding="utf-8"))["features"]
    out = {}
    for f in feats:
        pr = f["properties"]
        out[pr["NUTS_ID"]] = (int(pr["LEVL_CODE"]), pr["CNTR_CODE"],
                              pr.get("NUTS_NAME") or pr["NUTS_ID"], f["geometry"])
    print(f"  [ok] NUTS {y} : {len(out)} unités")
    return out


def build_rows(versions: list[int], snapshots: dict[int, dict]) -> list[tuple]:
    """Fusionne les versions consécutives inchangées en périodes temporelles."""
    codes = sorted({c for s in snapshots.values() for c in s})
    rows = []
    for code in codes:
        run: list[int] = []
        for i, y in enumerate(versions):
            here = snapshots[y].get(code)
            prev = snapshots[run[-1]][code] if run else None
            if here and (not run or here[2] == prev[2]):     # même nom -> continuité
                run.append(y)
            else:
                if run:
                    rows.append(_row(code, run, versions, snapshots))
                run = [y] if here else []
        if run:
            rows.append(_row(code, run, versions, snapshots))
    return rows


def _row(code: str, run: list[int], versions: list[int], snapshots: dict) -> tuple:
    level, country, name, geom = snapshots[run[-1]][code]
    vf = APPLICATION[run[0]]
    nxt = versions.index(run[-1]) + 1
    vt = APPLICATION[versions[nxt]] if nxt < len(versions) else FAR_FUTURE
    parents = [code[:-1]] if level > 0 else []
    gj = json.dumps(geom)
    return (code, name, f"nuts{level}", country, vf, vt, parents, [],
            APPLICATION[run[-1]], gj, gj)


def sanity(rows: list[tuple]) -> None:
    print("\nContrôles :")
    for d in ("2013-06-01", "2022-06-01", "2025-06-01"):
        for lvl in ("nuts1", "nuts2", "nuts3"):
            n = sum(1 for r in rows if r[2] == lvl and r[4] <= d < r[5])
            fr = sum(1 for r in rows if r[2] == lvl and r[3] == "FR" and r[4] <= d < r[5])
            print(f"  {d} {lvl}: {n} (dont FR {fr})", end="")
        print()


def main():
    ap = argparse.ArgumentParser(description="Ingestion NUTS GISCO -> PostGIS (modèle temporel)")
    ap.add_argument("--versions", type=int, nargs="+", default=sorted(APPLICATION))
    ap.add_argument("--data-dir", type=Path, default=Path("/data/raw/nuts"))
    ap.add_argument("--download", action="store_true",
                    help="télécharger les versions manquantes depuis GISCO")
    ap.add_argument("--dsn", nargs="?", const="ENV", default=None, metavar="DSN",
                    help="charge dans PostGIS ; sans valeur, utilise $PG_DSN")
    args = ap.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)

    versions = sorted(args.versions)
    unknown = [y for y in versions if y not in APPLICATION]
    if unknown:
        sys.exit(f"Versions sans date d'application connue : {unknown}")

    snapshots = {y: load_version(args.data_dir, y, args.download) for y in versions}
    rows = build_rows(versions, snapshots)
    print(f"Périodes NUTS reconstruites : {len(rows)}")
    sanity(rows)

    if args.dsn == "ENV":
        args.dsn = os.environ.get("PG_DSN") or sys.exit("--dsn sans valeur mais $PG_DSN absent.")
    if not args.dsn:
        print("\n(pas de --dsn : aucune écriture en base)")
        return

    import psycopg2
    from psycopg2.extras import execute_batch
    conn = psycopg2.connect(args.dsn)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM commune_version WHERE unit_type LIKE 'nuts%'")
        execute_batch(cur, NUTS_INSERT, rows, page_size=100)
    conn.close()
    print(f"  [ok] {len(rows)} périodes NUTS écrites dans PostGIS.")


if __name__ == "__main__":
    main()
