#!/usr/bin/env python3
"""
Joining IGN Admin Express geometries onto the COG temporal model.

Inputs:
  - the temporal versions produced by ingest_cog.py (replayed via import)
  - one or more Admin Express vintages, each dated:
      * COMMUNE Shapefile (editions <= 2024, INSEE_COM field; Lambert-93 reprojected)
      * commune GeoParquet (edition 4.0, code_insee / geometrie WKB fields, WGS84)

Matching principle:
  - for each version [valid_from, valid_to), take the vintage whose reference
    date falls WITHIN the period (the most recent one if several) — this is
    what makes code reuse harmless: 01033 taken from the 2018 edition is
    Bellegarde, from the 2019 edition it is Valserhône;
  - otherwise, the version inherits the geometry of the closest vintage that
    knows its code, flagged `geometry_approx: true`;
  - otherwise, no geometry (reported).

Each geometry is output in two qualities: raw (`--geojson-raw`) and simplified
for the web (shapely simplify, topology preserved per geometry).

Examples:
  python3 join_geometry.py \
    --millesimes 2025 --data-dir data/raw/insee \
    --shp 2018-01-01=data/raw/ae2018/extract/**/COMMUNE.shp \
    --shp 2019-01-01=data/raw/ae2019/extract/**/COMMUNE.shp \
    --parquet 2026-01-01=data/raw/ae2026/commune.parquet \
    --dept 01 --geojson communes_01.geojson
"""

from __future__ import annotations
import argparse
import glob
import json
import os
import sys
from datetime import date
from pathlib import Path

import shapefile  # pyshp
from shapely import from_wkb
from shapely.geometry import shape, mapping
from shapely.ops import transform as shp_transform
from pyproj import Transformer, CRS

sys.path.insert(0, str(Path(__file__).parent))
from ingest_cog import (CommuneVersion, build_versions, FAR_FUTURE,  # noqa: E402
                        SCHEMA_SQL, INSERT_SQL, DEPT_GEOM_SQL, version_row)

SIMPLIFY_TOLERANCE_DEG = 0.0005  # ~50 m: sufficient for commune-level web display


# --------------------------------------------------------------------------
#  Loading the geometry vintages
# --------------------------------------------------------------------------
def load_shp_vintage(path_pattern: str) -> dict[str, object]:
    """COMMUNE.shp of an Admin Express edition -> {insee_code: shapely geometry (WGS84)}."""
    matches = glob.glob(path_pattern, recursive=True)
    if not matches:
        raise FileNotFoundError(f"No shapefile matches {path_pattern}")
    shp_path = matches[0]

    cpg_path = Path(shp_path[:-4] + ".cpg")
    enc = "utf-8"
    if cpg_path.exists():
        cpg = cpg_path.read_text().strip().upper()
        if "1252" in cpg or "ANSI" in cpg or "LATIN" in cpg:
            enc = "latin-1"

    # Reproject if the edition is in Lambert-93 (the .prj is authoritative)
    transformer = None
    prj_path = Path(shp_path[:-4] + ".prj")
    if prj_path.exists():
        crs = CRS.from_wkt(prj_path.read_text())
        if crs.to_epsg() != 4326:
            transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    geoms: dict[str, object] = {}
    with shapefile.Reader(shp_path, encoding=enc) as r:
        fields = [f[0] for f in r.fields[1:]]
        code_idx = fields.index("INSEE_COM")
        for i, (rec, shp) in enumerate(zip(r.iterRecords(), r.iterShapes())):
            g = shape(shp.__geo_interface__)
            if transformer:
                g = shp_transform(transformer.transform, g)
            geoms[rec[code_idx]] = g
    print(f"  [ok] {len(geoms)} communes loaded from {shp_path}")
    return geoms


def load_parquet_vintage(path: str) -> dict[str, object]:
    """commune.parquet (Admin Express 4.0, GeoParquet WGS84) -> {insee_code: geometry}."""
    import pyarrow.parquet as pq
    t = pq.read_table(path, columns=["code_insee", "geometrie"])
    geoms = {}
    for code, wkb in zip(t["code_insee"].to_pylist(), t["geometrie"].to_pylist()):
        if code and wkb:
            geoms[code] = from_wkb(wkb)
    print(f"  [ok] {len(geoms)} communes loaded from {path}")
    return geoms


# --------------------------------------------------------------------------
#  Matching version <-> vintage
# --------------------------------------------------------------------------
def pick_vintage(v: CommuneVersion, vintages: list[tuple[str, dict]]) -> tuple[str, bool] | None:
    """Chooses the vintage for a version. Returns (ref_date, approx) or None.

    Exact: the vintage's reference date falls within [valid_from, valid_to)
           AND the vintage knows the code (the most recent of those vintages).
    Approx: otherwise, the vintage knowing the code whose date is closest to
            the validity period.
    """
    exact = [d for d, g in vintages if v.valid_from <= d < v.valid_to and v.code in g]
    if exact:
        return max(exact), False

    def distance(d: str) -> int:
        ref = date.fromisoformat(d)
        if d >= v.valid_to and v.valid_to != FAR_FUTURE:
            return (ref - date.fromisoformat(v.valid_to)).days + 1
        if d < v.valid_from:
            return (date.fromisoformat(v.valid_from) - ref).days
        return 0

    known = [d for d, g in vintages if v.code in g]
    if known:
        return min(known, key=distance), True
    return None


def attach_geometries(versions: list[CommuneVersion],
                      vintages: list[tuple[str, dict]],
                      simplify_tol: float) -> tuple[list[dict], list[dict]]:
    """Returns (simplified_features, raw_features) as GeoJSON."""
    by_date = dict(vintages)
    simple_feats, raw_feats = [], []
    missing = 0
    for v in versions:
        picked = pick_vintage(v, vintages)
        geom = raw = None
        props = {
            "code": v.code, "nom": v.nom,
            "valid_from": v.valid_from,
            "valid_to": None if v.valid_to == FAR_FUTURE else v.valid_to,
            "parents": v.parents, "children": v.children,
            "geometry_vintage": None, "geometry_approx": False,
        }
        if picked:
            ref_date, approx = picked
            raw = by_date[ref_date][v.code]
            geom = raw.simplify(simplify_tol, preserve_topology=True)
            props["geometry_vintage"] = ref_date
            props["geometry_approx"] = approx
        else:
            missing += 1
        simple_feats.append({"type": "Feature", "properties": props,
                             "geometry": mapping(geom) if geom is not None else None})
        raw_feats.append({"type": "Feature", "properties": props,
                          "geometry": mapping(raw) if raw is not None else None})
    if missing:
        print(f"  [!] {missing} versions without geometry (code absent from every vintage)")
    return simple_feats, raw_feats


# --------------------------------------------------------------------------
#  PostGIS loading (streaming, raw + simplified)
# --------------------------------------------------------------------------
def stream_to_postgis(versions: list[CommuneVersion],
                      vintages: list[tuple[str, dict]],
                      simplify_tol: float, dsn: str) -> bool:
    """Loads the versions + geometries into PostGIS in batches (bounded memory)."""
    try:
        import psycopg2
        from psycopg2.extras import execute_batch
    except ImportError:
        print("  [!] psycopg2 not installed -> no PostGIS loading.")
        return False
    try:
        conn = psycopg2.connect(dsn)
    except Exception as e:
        print(f"  [!] PostGIS connection failed ({e}).")
        return False

    by_date = dict(vintages)
    missing = approx_n = 0
    with conn, conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        batch, total = [], 0
        for v in versions:
            picked = pick_vintage(v, vintages)
            if picked:
                ref_date, approx = picked
                raw = by_date[ref_date][v.code]
                v.geometry = mapping(raw)
                v.geometry_simple = mapping(raw.simplify(simplify_tol, preserve_topology=True))
                v.geometry_vintage = ref_date
                v.geometry_approx = approx
                approx_n += approx
            else:
                missing += 1
            batch.append(version_row(v))
            v.geometry = v.geometry_simple = None   # free memory as we go
            if len(batch) >= 200:
                execute_batch(cur, INSERT_SQL, batch, page_size=50)
                total += len(batch)
                batch = []
                if total % 5000 < 200:
                    print(f"  ... {total} versions loaded")
        if batch:
            execute_batch(cur, INSERT_SQL, batch, page_size=50)
            total += len(batch)
        print("  ... materializing department outlines (union of communes)")
        cur.execute(DEPT_GEOM_SQL)
    conn.close()
    print(f"  [ok] {total} versions loaded into PostGIS "
          f"({approx_n} approx geometries, {missing} without geometry)")
    return True


# --------------------------------------------------------------------------
#  Entry point
# --------------------------------------------------------------------------
def parse_vintage_arg(s: str) -> tuple[str, str]:
    d, _, p = s.partition("=")
    date.fromisoformat(d)  # validates the format
    return d, p


def main():
    ap = argparse.ArgumentParser(description="Join Admin Express geometries onto the COG temporal model")
    ap.add_argument("--millesimes", type=int, nargs="+", default=[2025])
    ap.add_argument("--data-dir", type=Path, required=True,
                    help="Directory of INSEE CSVs (commune_YYYY.csv, mvtcommune_YYYY.csv)")
    ap.add_argument("--shp", action="append", default=[], metavar="DATE=GLOB",
                    help="Shapefile vintage: 2019-01-01=path/**/COMMUNE.shp")
    ap.add_argument("--parquet", action="append", default=[], metavar="DATE=PATH",
                    help="GeoParquet vintage: 2026-01-01=commune.parquet")
    ap.add_argument("--dept", default=None, help="Restrict the output to one department (e.g. 01)")
    ap.add_argument("--simplify", type=float, default=SIMPLIFY_TOLERANCE_DEG)
    ap.add_argument("--geojson", type=Path, default=None,
                    help="Simplified GeoJSON output (optional)")
    ap.add_argument("--geojson-raw", type=Path, default=None,
                    help="GeoJSON output with raw geometries (optional)")
    ap.add_argument("--dsn", nargs="?", const="ENV", default=None, metavar="DSN",
                    help="load versions + geometries into PostGIS; without a value, "
                         "uses $PG_DSN (never implicit: join-01 must not "
                         "overwrite the full-France table)")
    args = ap.parse_args()
    if args.dsn == "ENV":
        args.dsn = os.environ.get("PG_DSN") or sys.exit("--dsn without a value but $PG_DSN is unset.")
    if not args.geojson and not args.geojson_raw and not args.dsn:
        sys.exit("No output requested (--geojson, --geojson-raw or --dsn).")

    print("Loading geometry vintages:")
    vintages: list[tuple[str, dict]] = []
    for spec in args.shp:
        d, p = parse_vintage_arg(spec)
        vintages.append((d, load_shp_vintage(p)))
    for spec in args.parquet:
        d, p = parse_vintage_arg(spec)
        vintages.append((d, load_parquet_vintage(p)))
    if not vintages:
        sys.exit("No geometry vintage provided (--shp / --parquet).")
    vintages.sort()

    versions = build_versions(args.millesimes, args.data_dir, use_network=False)
    if args.dept:
        versions = [v for v in versions if v.code.startswith(args.dept)]
    print(f"Temporal versions: {len(versions)}" + (f" (department {args.dept})" if args.dept else ""))

    if args.geojson or args.geojson_raw:
        simple_feats, raw_feats = attach_geometries(versions, vintages, args.simplify)
        with_geom = sum(1 for f in simple_feats if f["geometry"])
        approx = sum(1 for f in simple_feats if f["properties"]["geometry_approx"])
        print(f"Geometry attached: {with_geom}/{len(simple_feats)} (including {approx} inherited approx)")
        if args.geojson:
            args.geojson.write_text(json.dumps(
                {"type": "FeatureCollection", "features": simple_feats}, ensure_ascii=False), encoding="utf-8")
            print(f"  [ok] simplified -> {args.geojson}")
        if args.geojson_raw:
            args.geojson_raw.write_text(json.dumps(
                {"type": "FeatureCollection", "features": raw_feats}, ensure_ascii=False), encoding="utf-8")
            print(f"  [ok] raw        -> {args.geojson_raw}")

    if args.dsn:
        if not stream_to_postgis(versions, vintages, args.simplify, args.dsn):
            sys.exit(1)


if __name__ == "__main__":
    main()
