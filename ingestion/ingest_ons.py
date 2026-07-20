#!/usr/bin/env python3
"""ONS Code History Database (OGL v3) -> modèle temporel : UK local authorities.

Source : CHD (geoportal.statistics.gov.uk), l'équivalent britannique du fichier
des mouvements INSEE : chaque code GSS porte sa date d'effet légale (OPER_DATE)
et sa date de fin (TERM_DATE), et Changes.csv relie prédécesseurs/successeurs
avec la référence du texte réglementaire (statutory instrument).

Périmètre v1 : le niveau « local authority » (l'équivalent du département/
commune de travail au UK) : E06 unitary, E07 district, E08 metropolitan
borough, E09 London borough, S12 council areas (Écosse), W06 (Pays de Galles),
N09 (Irlande du Nord). Dates EXACTES (contrairement au diff annuel TRF).
Sans géométrie pour l'instant (éditions de contours ONS : chantier suivant).

Usage (VM) :
    podman-compose --profile tools run --rm --no-deps ingest \
        /app/ingest_ons.py --data-dir /data/raw/uk/chd
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import date, datetime

LAD_ENTITIES = {"E06", "E07", "E08", "E09", "S12", "W06", "N09"}
SOURCE = "ons-chd"
FAR_FUTURE = date(9999, 1, 1)


def parse_dt(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"date illisible : {s!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/data/raw/uk/chd")
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN"))
    args = ap.parse_args()

    rows = []
    with open(os.path.join(args.data_dir, "ChangeHistory.csv"),
              encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r["ENTITYCD"] not in LAD_ENTITIES:
                continue
            code, nom = r["GEOGCD"].strip(), (r["GEOGNM"] or "").strip()
            start = parse_dt(r["OPER_DATE"])
            end = parse_dt(r["TERM_DATE"]) or FAR_FUTURE
            if not code or not nom or start is None:
                continue
            rows.append({"code": code, "nom": nom, "from": start, "to": end})
    print(f"{len(rows)} versions LAD retenues (entités {sorted(LAD_ENTITIES)})")

    parents: dict[str, set] = defaultdict(set)   # nouveau code -> prédécesseurs
    children: dict[str, set] = defaultdict(set)  # ancien code  -> successeurs
    with open(os.path.join(args.data_dir, "Changes.csv"),
              encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            new, old = r["GEOGCD"].strip(), r["GEOGCD_P"].strip()
            if r["ENTITYCD"] in LAD_ENTITIES and new and old and new != old:
                parents[new].add(old)
                children[old].add(new)
    print(f"{sum(map(len, parents.values()))} liens prédécesseur/successeur")

    import psycopg2
    from psycopg2.extras import execute_values
    conn = psycopg2.connect(args.dsn)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM commune_version WHERE source = %s", (SOURCE,))
        print(f"{cur.rowcount} anciennes lignes {SOURCE} supprimées (rejeu idempotent)")
        execute_values(cur,
            "INSERT INTO commune_version "
            "(code, nom, valid_from, valid_to, parents, children, unit_type, country, source) "
            "VALUES %s",
            [(r["code"], r["nom"], r["from"], r["to"],
              sorted(parents.get(r["code"], ())), sorted(children.get(r["code"], ())),
              "lad", "UK", SOURCE) for r in rows],
            page_size=2000)
        # Contrôles : nombre d'autorités vivantes aujourd'hui (attendu ~361 en
        # 2025 : 296 Angleterre + 22 Pays de Galles + 32 Écosse + 11 Irlande
        # du Nord), et un cas connu : Cumbria, abolie au 1er avril 2023.
        cur.execute("SELECT count(*) FROM commune_version "
                    "WHERE source=%s AND valid_to = %s", (SOURCE, FAR_FUTURE))
        live = cur.fetchone()[0]
        print(f"autorités vivantes aujourd'hui : {live} (attendu ~361)")
        cur.execute("SELECT nom, valid_from, valid_to, children FROM commune_version "
                    "WHERE source=%s AND nom='Cumbria'", (SOURCE,))
        for nom, vf, vt, ch in cur.fetchall():
            print(f"  {nom}: {vf} -> {vt} ; successeurs : {ch}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
