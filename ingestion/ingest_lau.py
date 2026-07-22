#!/usr/bin/env python3
"""
LAU ingestion (Local Administrative Units, Eurostat GISCO) — the European
BREADTH: every commune/municipality of the EU (+ EFTA/candidates), annual
generalized 1:1M editions. © EuroGeographics — attribution required.

Countries that have a native adapter (FR exact-dates, DE VG250, NL CBS) are
SKIPPED here: native wins on depth, LAU covers the rest.

Temporal model: diff of annual snapshots (ingest_snapshots.py) —
transitions approximated to the edition dates.

code = national LAU_ID (INSEE-like, AGS-like…), country = CNTR_CODE,
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
NATIVE = {"FR", "DE", "NL"}     # countries covered by a dedicated adapter
BOGUS = {"UN"}                  # GISCO artifacts (1 nameless unit, 2022 edition)


def load_year(data_dir: Path, y: int, download: bool) -> dict[str, tuple]:
    p = data_dir / f"lau_{y}.geojson"
    if not p.exists():
        if not download:
            sys.exit(f"{p} missing (use --download).")
        print(f"  downloading GISCO LAU {y} (~125 MB)…")
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
        # key = country + id (national LAU_IDs can overlap across countries)
        out[f"{cntr}:{lau_id}"] = (nom, shape(f["geometry"]))
    print(f"  [ok] LAU {y}: {len(out)} units (excluding {sorted(NATIVE)})")
    return out


def main():
    ap = argparse.ArgumentParser(description="GISCO LAU ingestion (EU breadth) -> PostGIS")
    ap.add_argument("--years", type=int, nargs="+", default=list(range(2016, 2024)))
    ap.add_argument("--data-dir", type=Path, default=Path("/data/raw/lau"))
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--dsn", nargs="?", const="ENV", default=None, metavar="DSN")
    args = ap.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)

    years = sorted(args.years)
    dates = [f"{y}-01-01" for y in years]
    snapshots = {f"{y}-01-01": load_year(args.data_dir, y, args.download) for y in years}

    # GISCO does not include every country in every edition (UK absent after
    # 2016, EL and PL intermittent…). Each country's chronology must count
    # ONLY the editions where it appears — otherwise a missing edition reads
    # as the extinction of all its communes.
    per_country: dict[str, dict[str, dict]] = {}   # cntr -> date -> {id: (name, geom)}
    for d, snap in snapshots.items():
        for key, val in snap.items():
            cntr, _, lau_id = key.partition(":")
            per_country.setdefault(cntr, {}).setdefault(d, {})[lau_id] = val

    if args.dsn == "ENV":
        args.dsn = os.environ.get("PG_DSN") or sys.exit("--dsn without a value but $PG_DSN is unset.")

    grand_total = 0
    for cntr in sorted(per_country):
        # An edition only counts for a country if it is reasonably complete.
        # Reference = MEDIAN of the editions present (not the peak: the 2017
        # LAU reform really did take Denmark from 2,168 parishes to 99
        # kommuner — that is a transition to record, not a gap). Genuinely
        # partial editions (PL 2023: 14 units out of ~2,480) are still
        # discarded.
        counts = sorted(len(per_country[cntr][d]) for d in per_country[cntr])
        median = counts[len(counts) // 2]
        cdates = [d for d in dates
                  if len(per_country[cntr].get(d, {})) >= max(1, median // 2)]
        gaps = [d for d in dates if d not in cdates]
        if gaps:
            print(f"  [i] {cntr}: absent/partial editions ignored: "
                  f"{', '.join(g[:4] for g in gaps)}")
        periods = build_periods(cdates, per_country[cntr])
        grand_total += len(periods)
        if args.dsn:
            load_postgis(periods, "lau", cntr, args.dsn)
    print(f"Rebuilt LAU periods: {grand_total} across {len(per_country)} countries")


if __name__ == "__main__":
    main()
