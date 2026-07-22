#!/usr/bin/env python3
"""French EPCI (intercommunalites) as a first-class unit level (issue #5).

Source: BANATIC via data.gouv (Base nationale sur les intercommunalites,
Licence Ouverte). We read the "perimetre-epci-a-fp" file: one row per member
commune with its EPCI (SIREN + name + legal nature). The EPCI geometry is the
UNION of its member communes' current geometries, so no new geometry source is
needed. Codes are the EPCI SIREN (nationally unique). unit_type='epci'.

SCOPE: this is the CURRENT perimeter snapshot (edition year). Historical EPCI
lineage (creations, mergers, the 2017 big-bang) is phase 2, needing the yearly
BANATIC archives. Any U+FFFD in the source is a hard refusal.

Usage: ingest_epci.py --data-dir /data/raw/banatic [--edition 2025]
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import date

SOURCE = "banatic"
UNIT_TYPE = "epci"
FAR_FUTURE = date(9999, 1, 1)


def read_perimetre(path: str) -> dict[str, dict]:
    raw = open(path, "rb").read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    if "�" in text:
        raise SystemExit(f"Corrupted source (U+FFFD): {path}: refusing to ingest.")
    epci: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        siren = (row.get("siren") or "").strip()
        insee = (row.get("insee") or "").strip()
        if not siren or not insee:
            continue
        e = epci.setdefault(siren, {
            "nom": (row.get("raison_sociale") or "").strip(),
            "nature": (row.get("nature_juridique") or "").strip(),
            "members": []})
        e["members"].append(insee)
    return epci


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/data/raw/banatic")
    ap.add_argument("--edition", type=int, default=2025)
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN"))
    args = ap.parse_args()

    epci = read_perimetre(os.path.join(args.data_dir, "perimetre.csv"))
    print(f"{len(epci)} EPCI, {sum(len(e['members']) for e in epci.values())} memberships")

    import psycopg2
    from psycopg2.extras import execute_values
    conn = psycopg2.connect(args.dsn)
    start = date(args.edition, 1, 1)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM commune_version WHERE source=%s AND unit_type=%s",
                    (SOURCE, UNIT_TYPE))
        print(f"{cur.rowcount} previous rows deleted (idempotent replay)")
        inserted, skipped = 0, 0
        for siren, e in epci.items():
            # Geometry = union of the members' geometries valid at the edition.
            cur.execute(
                "SELECT ST_Multi(ST_UnaryUnion(ST_Collect(geom))), "
                "       ST_Multi(ST_UnaryUnion(ST_Collect(geom_simple))) "
                "FROM commune_version "
                "WHERE country='FR' AND unit_type='commune' AND code = ANY(%s) "
                "  AND valid_from <= %s AND valid_to > %s",
                (e["members"], start, start))
            geom, geom_s = cur.fetchone()
            if geom is None:
                skipped += 1
                continue
            cur.execute(
                "INSERT INTO commune_version (code, nom, valid_from, valid_to, "
                " unit_type, country, source, geometry_vintage, geometry_approx, "
                " geom, geom_simple) "
                "VALUES (%s,%s,%s,%s,%s,'FR',%s,%s,true,%s,%s)",
                (siren, e["nom"], start, FAR_FUTURE, UNIT_TYPE, SOURCE, start,
                 geom, geom_s))
            inserted += 1
        print(f"{inserted} EPCI inserted, {skipped} without member geometry")
        cur.execute("SELECT count(*) FROM commune_version WHERE source=%s AND unit_type=%s",
                    (SOURCE, UNIT_TYPE))
        total = cur.fetchone()[0]
    conn.close()
    ok = total == inserted and inserted > 1000
    print(f"control: {total} EPCI live  {'OK' if ok else 'MISMATCH'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
