#!/usr/bin/env python3
"""ONS Code History Database (OGL v3) -> temporal model: UK local authorities.

Source: CHD (geoportal.statistics.gov.uk), the British equivalent of the INSEE
movements file: each GSS code carries its legal effective date (OPER_DATE)
and its termination date (TERM_DATE), and Changes.csv links predecessors/
successors with the reference of the statutory instrument.

v1 scope: the "local authority" level (the UK working equivalent of the
departement/commune): E06 unitary, E07 district, E08 metropolitan borough,
E09 London borough, S12 council areas (Scotland), W06 (Wales), N09 (Northern
Ireland). EXACT dates (unlike the TRF annual diff).
No geometry for now (ONS boundary editions: next work stream).

Usage (VM):
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
    raise ValueError(f"unreadable date: {s!r}")


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
    print(f"{len(rows)} LAD versions retained (entities {sorted(LAD_ENTITIES)})")

    parents: dict[str, set] = defaultdict(set)   # new code -> predecessors
    children: dict[str, set] = defaultdict(set)  # old code -> successors
    with open(os.path.join(args.data_dir, "Changes.csv"),
              encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            new, old = r["GEOGCD"].strip(), r["GEOGCD_P"].strip()
            if r["ENTITYCD"] in LAD_ENTITIES and new and old and new != old:
                parents[new].add(old)
                children[old].add(new)
    print(f"{sum(map(len, parents.values()))} predecessor/successor links")

    import psycopg2
    from psycopg2.extras import execute_values
    conn = psycopg2.connect(args.dsn)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM commune_version WHERE source = %s", (SOURCE,))
        print(f"{cur.rowcount} old {SOURCE} rows deleted (idempotent replay)")
        execute_values(cur,
            "INSERT INTO commune_version "
            "(code, nom, valid_from, valid_to, parents, children, unit_type, country, source) "
            "VALUES %s",
            [(r["code"], r["nom"], r["from"], r["to"],
              sorted(parents.get(r["code"], ())), sorted(children.get(r["code"], ())),
              "lad", "UK", SOURCE) for r in rows],
            page_size=2000)
        # Checks: number of authorities alive today (expected ~361 in 2025:
        # 296 England + 22 Wales + 32 Scotland + 11 Northern Ireland), and a
        # known case: Cumbria, abolished on April 1st, 2023.
        cur.execute("SELECT count(*) FROM commune_version "
                    "WHERE source=%s AND valid_to = %s", (SOURCE, FAR_FUTURE))
        live = cur.fetchone()[0]
        print(f"authorities alive today: {live} (expected ~361)")
        cur.execute("SELECT nom, valid_from, valid_to, children FROM commune_version "
                    "WHERE source=%s AND nom='Cumbria'", (SOURCE,))
        for nom, vf, vt, ch in cur.fetchall():
            print(f"  {nom}: {vf} -> {vt} ; successors: {ch}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
