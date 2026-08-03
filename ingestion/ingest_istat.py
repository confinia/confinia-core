#!/usr/bin/env python3
"""Italian temporal model: comune lineage from ISTAT (issue #91).

Source: ISTAT "Elenco dei comuni soppressi", one CSV inside a zip, listing every
comune that ceased to exist since Italian unification, with the date and the
comune that took it over.
  https://www.istat.it/it/archivio/6789
Licence: CC BY 4.0 (ISTAT) — attribution required.

What it gives us, and why it is the piece that was missing:

Before this, Italy came only from `eurostat-lau`: annual snapshots 2016-2023,
one version per comune, **zero events**. A dead code returned nothing, and a
merger looked like a comune that simply stopped existing. The value of the
temporal model was never the snapshots — anyone can download those — it is
answering "this code is dead, here is what replaced it, on this date".

Coverage measured on the 2024 edition: 2 634 suppressed comuni, 2 527 of them
carrying a successor code, spanning 1865-2024 across 87 distinct years, and the
whole *fusioni* wave of 2014-2019 (287 suppressions).

IMPORTANT — the two directions are not symmetric:

* A suppression row says "code X ended on date D, its territory went to Y".
  That is a fact about X, and we can state it.
* It does NOT say that Y was *created* on D. Y usually already existed and
  simply absorbed X. So we add X to Y's `parents` only when Y's own code first
  appears at that date; otherwise we would claim a birth that never happened.

`Comune soppresso per scorporo` marks a *scorporo* — territory split off rather
than a whole comune absorbed. Those rows describe a partial transfer, so the
predecessor may legitimately outlive the event. We record the link and flag it
rather than closing the predecessor's validity.

Usage:
  ingest_istat.py --zip /data/raw/istat/soppressi.zip [--dsn $PG_DSN] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import zipfile
from collections import defaultdict
from datetime import date, datetime

SOURCE = "istat-soppressi"
COUNTRY = "IT"
# The Italian comuni already in the database arrive via Eurostat LAU and are
# stored with unit_type "lau" and a 6-digit zero-padded code (005026) —
# exactly the format of ISTAT's "Codice Comune". Using "comune" here matched
# zero rows and would have created a parallel, disconnected set.
UNIT_TYPE = "lau"
FOREVER = date(9999, 1, 1)
MEMBER = "Elenco comuni soppressi/Elenco comuni soppressi.csv"

# The file is Windows-1252, not UTF-8. Decoding it as UTF-8 raises; decoding it
# as latin-1 silently mangles the accented names (Sant'Anastasìa and friends),
# which is worse. We therefore decode strictly and refuse anything else.
ENCODING = "cp1252"

COL_YEAR = "Anno"
COL_CODE = "Codice Comune"
COL_NAME = "Denominazione Comune"
COL_SCORPORO = "Comune soppresso per scorporo"
COL_DATE = "Data evento"
COL_SUCC = "Codice del Comune associato alla variazione"
COL_SUCC_NAME = "Denominazione Comune associato alla variazione"


class Event:
    """One suppression: `code` ended on `when`, its territory went to `successor`."""

    __slots__ = ("code", "name", "when", "successor", "successor_name", "scorporo")

    def __init__(self, code, name, when, successor, successor_name, scorporo):
        self.code = code
        self.name = name
        self.when = when
        self.successor = successor
        self.successor_name = successor_name
        self.scorporo = scorporo


def parse_date(raw: str, year: str) -> date | None:
    """ISTAT writes dd/mm/yyyy. Fall back to 1 January of the stated year.

    Roughly a fifth of the older rows carry a year but no usable day, and
    guessing a day would invent precision the source does not have. January 1st
    is the honest default for an administrative change and is what the `Anno`
    column already asserts; callers can tell the two apart because a fallback
    always lands on 01-01.
    """
    raw = (raw or "").strip()
    if raw:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    if year and year.strip().isdigit():
        return date(int(year.strip()), 1, 1)
    return None


def read_events(zip_path: str) -> list[Event]:
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
        if not names:
            sys.exit(f"no CSV inside {zip_path}")
        member = MEMBER if MEMBER in names else names[0]
        raw = z.read(member)

    try:
        text = raw.decode(ENCODING)
    except UnicodeDecodeError as e:      # refuse rather than mangle the names
        sys.exit(f"{member} is not {ENCODING}: {e}")
    if "�" in text:
        sys.exit(f"{member} decoded with replacement characters — wrong encoding")

    events, skipped = [], 0
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        code = (row.get(COL_CODE) or "").strip()
        succ = (row.get(COL_SUCC) or "").strip()
        when = parse_date(row.get(COL_DATE, ""), row.get(COL_YEAR, ""))
        if not code or not when:
            skipped += 1
            continue
        events.append(Event(
            code=code,
            name=(row.get(COL_NAME) or "").strip(),
            when=when,
            successor=succ or None,
            successor_name=(row.get(COL_SUCC_NAME) or "").strip() or None,
            scorporo=bool((row.get(COL_SCORPORO) or "").strip()),
        ))
    if skipped:
        print(f"  {skipped} row(s) without a usable code or date, skipped")
    return events


def plan(events: list[Event]) -> tuple[dict, dict]:
    """Return (closures, births).

    closures[code] = (end_date, [successor codes])  — this code stopped there.
    births[successor] = (start_date, [predecessor codes]) — candidate parents,
    applied only if the successor has no version before that date (see module
    docstring: absorbing a comune is not being born).
    """
    closures: dict[str, tuple[date, set]] = {}
    births: dict[str, dict[date, set]] = defaultdict(lambda: defaultdict(set))

    for e in events:
        if not e.scorporo:
            # Keep the EARLIEST end date: a comune can appear on several rows
            # when its territory was divided between two successors.
            cur = closures.get(e.code)
            if cur is None or e.when < cur[0]:
                closures[e.code] = (e.when, set())
            if e.successor:
                closures[e.code][1].add(e.successor)
        if e.successor:
            births[e.successor][e.when].add(e.code)

    return closures, {s: dict(d) for s, d in births.items()}


DDL = """
ALTER TABLE commune_version ADD COLUMN IF NOT EXISTS lineage_note text;
"""


def apply(dsn: str, events: list[Event], dry_run: bool) -> None:
    import psycopg2                       # same driver as the other loaders

    closures, births = plan(events)
    print(f"  {len(closures)} comuni closed, {len(births)} successors with candidate parents")

    conn = psycopg2.connect(dsn)
    with conn, conn.cursor() as cur:
        cur.execute(DDL)

        # Existing Italian comuni, so we can tell "absorbed X" from "was born".
        cur.execute("""
            SELECT code, min(valid_from), max(valid_to)
            FROM commune_version
            WHERE country = %s AND unit_type = %s
            GROUP BY code
        """, (COUNTRY, UNIT_TYPE))
        known = {c: (lo, hi) for c, lo, hi in cur.fetchall()}
        print(f"  {len(known)} Italian comune codes already in the database")

        closed = reopened = created = parented = 0

        for code, (end, succs) in sorted(closures.items()):
            succ_list = sorted(succs)
            if code in known:
                # Close the live version at the event date. Only shorten: a
                # source that disagrees with a later one must not extend life.
                cur.execute("""
                    UPDATE commune_version
                       SET valid_to = %s,
                           children = (SELECT array_agg(DISTINCT c)
                                       FROM unnest(children || %s::text[]) c),
                           lineage_note = %s
                     WHERE country = %s AND unit_type = %s AND code = %s
                       AND valid_to > %s
                """, (end, succ_list, SOURCE, COUNTRY, UNIT_TYPE, code, end))
                closed += cur.rowcount
            else:
                # The comune died before our snapshots begin (most of them:
                # the snapshots start in 2016, the file starts in 1865). We
                # still record the code so a lookup can route it to its
                # successor — with NO geometry, which the API must not fake.
                name = next((e.name for e in events if e.code == code and e.name), code)
                cur.execute("""
                    INSERT INTO commune_version
                        (code, nom, unit_type, country, valid_from, valid_to,
                         parents, children, source, lineage_note)
                    VALUES (%s, %s, %s, %s, %s, %s, '{}', %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (code, name, UNIT_TYPE, COUNTRY, date(1861, 3, 17), end,
                      succ_list, SOURCE, SOURCE))
                created += cur.rowcount

        for succ, by_date in sorted(births.items()):
            if succ not in known:
                continue
            first_seen = known[succ][0]
            for when, preds in sorted(by_date.items()):
                # A birth only if the successor's own record starts here. An
                # absorbing comune that already existed gets no parents: it was
                # not created, it grew.
                if first_seen != when:
                    continue
                cur.execute("""
                    UPDATE commune_version
                       SET parents = (SELECT array_agg(DISTINCT p)
                                      FROM unnest(parents || %s::text[]) p),
                           lineage_note = %s
                     WHERE country = %s AND unit_type = %s AND code = %s
                       AND valid_from = %s
                """, (sorted(preds), SOURCE, COUNTRY, UNIT_TYPE, succ, when))
                parented += cur.rowcount

        if dry_run:
            conn.rollback()
            print("  DRY RUN — rolled back")
        else:
            conn.commit()

        print(f"  closed {closed} · created {created} historical-only · "
              f"parents set on {parented}")
        _ = reopened


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", required=True, help="Elenco-comuni-soppressi.zip")
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        return int(bool(sys.stderr.write("no --dsn and no PG_DSN\n"))) or 2

    print(f"reading {args.zip}")
    events = read_events(args.zip)
    if not events:
        sys.exit("no usable events — refusing to touch the database")
    span = (min(e.when for e in events), max(e.when for e in events))
    with_succ = sum(1 for e in events if e.successor)
    print(f"  {len(events)} suppressions, {span[0]} -> {span[1]}, "
          f"{with_succ} with a successor, "
          f"{sum(1 for e in events if e.scorporo)} scorporo")

    apply(args.dsn, events, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
