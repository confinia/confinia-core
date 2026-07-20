#!/usr/bin/env python3
"""TRF-GIS (Victor Gay, CC BY 4.0) -> modèle temporel : communes 1870-1940.

Source : nomenclatures annuelles COG_COMMUNES_YYYY.tab (Harvard Dataverse,
doi:10.7910/DVN/LZTZWE), une ligne par commune et par an, codes INSEE
rétro-reconstruits (`insee`), nom propre (`com_name_prop`), id Cassini.

Méthode (diff annuel, comme le moteur snapshots DE/NL/LAU) :
 - apparition d'un code       -> début de période au 1er janvier de l'année ;
 - disparition                -> fin de période au 1er janvier de l'année ;
 - changement de nom          -> fin + début (nouvelle version du même code).

Limites assumées, documentées :
 - résolution ANNUELLE : toutes les dates sont approximées au 1er janvier
   (les dates d'effet exactes pré-1943 viendront du corpus EHESS/Cassini) ;
 - pas de géométrie (le modèle porte déjà des versions sans géométrie) ;
 - plancher 1870 (première édition TRF) ;
 - couture 1940-1943 : les périodes vivantes en 1940 dont le code existe dans
   le modèle INSEE d'après-guerre sont soudées à son plancher (valid_to =
   1943-01-01) ; les autres sont fermées à 1941-01-01 (guerre, annexions).
Aucun chevauchement possible avec l'existant : tout le FR INSEE >= 1943-01-01.

Usage (VM, conteneur ingest) :
    podman-compose --profile tools run --rm ingest /app/ingest_trf.py \
        --data-dir /data/raw/trf/communes
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date

YEARS = list(range(1870, 1941))
SOURCE = "trf-gis"


def read_year(path: str) -> dict[str, str]:
    """COG_COMMUNES_YYYY.tab -> {code insee: nom propre}. Première occurrence
    gardée en cas de doublon (rare), lignes sans code ignorées."""
    out: dict[str, str] = {}
    dupes = empty = 0
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            code = (row.get("insee") or "").strip()
            nom = (row.get("com_name_prop") or row.get("com_name") or "").strip()
            if not code or not nom:
                empty += 1
                continue
            if code in out:
                dupes += 1
                continue
            out[code] = nom
    if dupes or empty:
        print(f"  {os.path.basename(path)}: {dupes} doublons, {empty} lignes vides ignorés")
    return out


def build_periods(states: dict[int, dict[str, str]]) -> list[tuple[str, str, int, int | None]]:
    """[(code, nom, année_début, année_fin | None si vivant en 1940)]"""
    periods: list[tuple[str, str, int, int | None]] = []
    open_: dict[str, tuple[str, int]] = {}          # code -> (nom, début)
    for y in YEARS:
        cur = states[y]
        for code, (nom, start) in list(open_.items()):
            if code not in cur:
                periods.append((code, nom, start, y))
                del open_[code]
            elif cur[code] != nom:                   # renommage : nouvelle version
                periods.append((code, nom, start, y))
                open_[code] = (cur[code], y)
        for code, nom in cur.items():
            if code not in open_:
                open_[code] = (nom, y)
    for code, (nom, start) in open_.items():
        periods.append((code, nom, start, None))
    return periods


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/data/raw/trf/communes")
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN"))
    args = ap.parse_args()
    if not args.dsn:
        print("PG_DSN manquant", file=sys.stderr)
        return 2

    print("Lecture des 71 nomenclatures annuelles…")
    states = {y: read_year(os.path.join(args.data_dir, f"COG_COMMUNES_{y}.tab"))
              for y in YEARS}
    for y in (1870, 1900, 1921, 1940):
        print(f"  {y}: {len(states[y])} communes")

    periods = build_periods(states)
    print(f"{len(periods)} périodes construites")

    import psycopg2
    from psycopg2.extras import execute_values
    conn = psycopg2.connect(args.dsn)
    with conn, conn.cursor() as cur:
        # Codes connus du modèle exact d'après-guerre : cibles de la soudure.
        cur.execute("SELECT DISTINCT code FROM commune_version "
                    "WHERE country='FR' AND unit_type='commune' AND source='insee-cog'")
        post_war = {r[0] for r in cur.fetchall()}

        rows = []
        welded = 0
        for code, nom, start, end in periods:
            if end is None:                          # vivant en 1940
                if code in post_war:
                    end_date, welded = date(1943, 1, 1), welded + 1
                else:
                    end_date = date(1941, 1, 1)
            else:
                end_date = date(end, 1, 1)
            rows.append((code, nom, date(start, 1, 1), end_date, SOURCE))

        cur.execute("DELETE FROM commune_version WHERE source = %s", (SOURCE,))
        print(f"{cur.rowcount} anciennes lignes {SOURCE} supprimées (rejeu idempotent)")
        execute_values(cur,
            "INSERT INTO commune_version (code, nom, valid_from, valid_to, source) VALUES %s",
            rows, page_size=5000)
        print(f"{len(rows)} versions insérées ({welded} soudées au plancher INSEE 1943)")

        # Contrôle : l'état reconstruit à une date doit égaler la nomenclature brute.
        ok = True
        for y in (1875, 1900, 1921, 1939):
            d = date(y, 6, 1)
            cur.execute("SELECT count(*) FROM commune_version "
                        "WHERE source=%s AND valid_from<=%s AND valid_to>%s", (SOURCE, d, d))
            got, want = cur.fetchone()[0], len(states[y])
            tag = "OK " if got == want else "ECART"
            if got != want:
                ok = False
            print(f"  contrôle {d}: reconstruit {got} vs nomenclature {want}  {tag}")
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
