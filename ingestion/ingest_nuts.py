#!/usr/bin/env python3
"""
Ingestion of the NUTS regions (Eurostat GISCO) into the temporal model.

Source: gisco-services.ec.europa.eu, NUTS_RG_20M_<year>_4326.geojson layers
(7 versions: 2003, 2006, 2010, 2013, 2016, 2021, 2024 — attributes NUTS_ID,
LEVL_CODE 0..3, CNTR_CODE, NUTS_NAME). © EuroGeographics for the administrative
boundaries — attribution required.

Model: one row = one (code, name) valid over [valid_from, valid_to), with
unit_type = nuts0..nuts3 and country = CNTR_CODE. The transition dates are the
official entry-into-force dates of the NUTS versions (not the vintages).
Consecutive versions where (code, name) is unchanged are merged into a single
period; the retained geometry is that of the last version of the period
(geometry_vintage set to the corresponding date).

v1 limitations (documented): parents = hierarchical parent (code prefix),
children empty — the correspondences between versions (NUTS splits/merges)
will come from the Eurostat correspondence tables later.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

FAR_FUTURE = "9999-01-01"

# Official entry-into-force dates of the NUTS versions.
APPLICATION = {
    2003: "2003-07-11",   # regulation (EC) No 1059/2003
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
            sys.exit(f"{p} missing (use --download).")
        print(f"  downloading GISCO {y}…")
        urllib.request.urlretrieve(GISCO_URL.format(y=y), p)
    feats = json.loads(p.read_text(encoding="utf-8"))["features"]
    out = {}
    for f in feats:
        pr = f["properties"]
        out[pr["NUTS_ID"]] = (int(pr["LEVL_CODE"]), pr["CNTR_CODE"],
                              pr.get("NUTS_NAME") or pr["NUTS_ID"], f["geometry"])
    print(f"  [ok] NUTS {y}: {len(out)} units")
    return out


def build_rows(versions: list[int], snapshots: dict[int, dict]) -> list[tuple]:
    """Merges consecutive unchanged versions into temporal periods."""
    codes = sorted({c for s in snapshots.values() for c in s})
    rows = []
    for code in codes:
        run: list[int] = []
        for i, y in enumerate(versions):
            here = snapshots[y].get(code)
            prev = snapshots[run[-1]][code] if run else None
            if here and (not run or here[2] == prev[2]):     # same name -> continuity
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
    print("\nChecks:")
    for d in ("2013-06-01", "2022-06-01", "2025-06-01"):
        for lvl in ("nuts1", "nuts2", "nuts3"):
            n = sum(1 for r in rows if r[2] == lvl and r[4] <= d < r[5])
            fr = sum(1 for r in rows if r[2] == lvl and r[3] == "FR" and r[4] <= d < r[5])
            print(f"  {d} {lvl}: {n} (of which FR {fr})", end="")
        print()


def main():
    ap = argparse.ArgumentParser(description="GISCO NUTS ingestion -> PostGIS (temporal model)")
    ap.add_argument("--versions", type=int, nargs="+", default=sorted(APPLICATION))
    ap.add_argument("--data-dir", type=Path, default=Path("/data/raw/nuts"))
    ap.add_argument("--download", action="store_true",
                    help="download the missing versions from GISCO")
    ap.add_argument("--dsn", nargs="?", const="ENV", default=None, metavar="DSN",
                    help="load into PostGIS; without a value, uses $PG_DSN")
    args = ap.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)

    versions = sorted(args.versions)
    unknown = [y for y in versions if y not in APPLICATION]
    if unknown:
        sys.exit(f"Versions with no known entry-into-force date: {unknown}")

    snapshots = {y: load_version(args.data_dir, y, args.download) for y in versions}
    rows = build_rows(versions, snapshots)
    print(f"Rebuilt NUTS periods: {len(rows)}")
    sanity(rows)

    if args.dsn == "ENV":
        args.dsn = os.environ.get("PG_DSN") or sys.exit("--dsn without a value but $PG_DSN is unset.")
    if not args.dsn:
        print("\n(no --dsn: nothing written to the database)")
        return

    import psycopg2
    from psycopg2.extras import execute_batch
    conn = psycopg2.connect(args.dsn)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM commune_version WHERE unit_type LIKE 'nuts%'")
        execute_batch(cur, NUTS_INSERT, rows, page_size=100)
    conn.close()
    print(f"  [ok] {len(rows)} NUTS periods written to PostGIS.")


if __name__ == "__main__":
    main()
