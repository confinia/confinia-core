#!/usr/bin/env python3
"""TRF-GIS (Victor Gay, CC BY 4.0) -> temporal model: communes 1870-1940.

Source: annual nomenclatures COG_COMMUNES_YYYY.tab (Harvard Dataverse,
doi:10.7910/DVN/LZTZWE), one row per commune per year, retro-reconstructed
INSEE codes (`insee`), proper name (`com_name_prop`), Cassini id.

Method (annual diff, like the DE/NL/LAU snapshot engine):
 - a code appears        -> period starts on January 1st of that year;
 - a code disappears     -> period ends on January 1st of that year;
 - a name changes        -> end + start (new version of the same code).

Assumed, documented limitations:
 - ANNUAL resolution: all dates are approximated to January 1st
   (exact pre-1943 effective dates will come from the EHESS/Cassini corpus);
 - no geometry (the model already carries versions without geometry);
 - 1870 floor (first TRF edition);
 - 1940-1943 seam: periods alive in 1940 whose code exists in the post-war
   INSEE model are welded to its floor (valid_to = 1943-01-01); the others
   are closed at 1941-01-01 (war, annexations).
No overlap possible with the existing data: all FR INSEE >= 1943-01-01.

Usage (VM, ingest container):
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


def load_year_text(base: str, year: int) -> tuple[str, str]:
    """(text, delimiter) for a year. Prefers the ORIGINAL .txt (cp1252, CSV)
    over Dataverse's derived .tab: Dataverse's Stata conversion replaces
    accented characters with U+FFFD (discovered on 2026-07-21: « G��nicourt »).
    SAFEGUARD: refuses any source containing a U+FFFD, rather than silently
    ingesting garbage data."""
    txt = os.path.join(base, f"COG_COMMUNES_{year}.txt")
    tab = os.path.join(base, f"COG_COMMUNES_{year}.tab")
    if os.path.exists(txt):
        raw = open(txt, "rb").read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")
        delim = ","
    else:
        text = open(tab, "rb").read().decode("utf-8")
        delim = "\t"
    if "\ufffd" in text:
        raise SystemExit(f"Corrupted source (U+FFFD): {txt or tab}: refusing to ingest.")
    return text, delim


def read_year(base: str, year: int) -> dict[str, str]:
    """-> {insee code: proper name}. First occurrence kept in case of a
    duplicate (rare), rows without a code ignored."""
    import io as _io
    text, delim = load_year_text(base, year)
    out: dict[str, str] = {}
    dupes = empty = 0
    if True:
        for row in csv.DictReader(_io.StringIO(text), delimiter=delim):
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
        print(f"  {year}: {dupes} duplicates, {empty} empty rows ignored")
    return out


def build_periods(states: dict[int, dict[str, str]]) -> list[tuple[str, str, int, int | None]]:
    """[(code, name, start_year, end_year | None if alive in 1940)]"""
    periods: list[tuple[str, str, int, int | None]] = []
    open_: dict[str, tuple[str, int]] = {}          # code -> (name, start)
    for y in YEARS:
        cur = states[y]
        for code, (nom, start) in list(open_.items()):
            if code not in cur:
                periods.append((code, nom, start, y))
                del open_[code]
            elif cur[code] != nom:                   # renaming: new version
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
        print("PG_DSN missing", file=sys.stderr)
        return 2

    print("Reading the 71 annual nomenclatures (original .txt files)…")
    states = {y: read_year(args.data_dir, y) for y in YEARS}
    for y in (1870, 1900, 1921, 1940):
        print(f"  {y}: {len(states[y])} communes")

    periods = build_periods(states)
    print(f"{len(periods)} periods built")

    import psycopg2
    from psycopg2.extras import execute_values
    conn = psycopg2.connect(args.dsn)
    with conn, conn.cursor() as cur:
        # Codes known to the exact post-war model: targets of the weld.
        cur.execute("SELECT DISTINCT code FROM commune_version "
                    "WHERE country='FR' AND unit_type='commune' AND source='insee-cog'")
        post_war = {r[0] for r in cur.fetchall()}

        rows = []
        welded = 0
        for code, nom, start, end in periods:
            if end is None:                          # alive in 1940
                if code in post_war:
                    end_date, welded = date(1943, 1, 1), welded + 1
                else:
                    end_date = date(1941, 1, 1)
            else:
                end_date = date(end, 1, 1)
            rows.append((code, nom, date(start, 1, 1), end_date, SOURCE))

        cur.execute("DELETE FROM commune_version WHERE source = %s", (SOURCE,))
        print(f"{cur.rowcount} old {SOURCE} rows deleted (idempotent replay)")
        execute_values(cur,
            "INSERT INTO commune_version (code, nom, valid_from, valid_to, source) VALUES %s",
            rows, page_size=5000)
        print(f"{len(rows)} versions inserted ({welded} welded to the 1943 INSEE floor)")

        # Check: the state rebuilt at a date must equal the raw nomenclature.
        ok = True
        for y in (1875, 1900, 1921, 1939):
            d = date(y, 6, 1)
            cur.execute("SELECT count(*) FROM commune_version "
                        "WHERE source=%s AND valid_from<=%s AND valid_to>%s", (SOURCE, d, d))
            got, want = cur.fetchone()[0], len(states[y])
            tag = "OK " if got == want else "MISMATCH"
            if got != want:
                ok = False
            print(f"  check {d}: rebuilt {got} vs nomenclature {want}  {tag}")
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
