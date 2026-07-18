#!/usr/bin/env python3
"""
Raccord des géométries IGN Admin Express sur le modèle temporel COG.

Entrées :
  - les versions temporelles produites par ingest_cog.py (rejouées via import)
  - un ou plusieurs millésimes Admin Express, chacun daté :
      * Shapefile COMMUNE (éditions <= 2024, champ INSEE_COM ; Lambert-93 reprojeté)
      * GeoParquet commune (édition 4.0, champs code_insee / geometrie WKB, WGS84)

Principe d'appariement :
  - pour chaque version [valid_from, valid_to), on prend le millésime dont la
    date de référence tombe DANS la période (le plus récent si plusieurs) —
    c'est ce qui rend le réemploi de code inoffensif : 01033 pris dans
    l'édition 2018 est Bellegarde, dans l'édition 2019 c'est Valserhône ;
  - sinon, la version hérite de la géométrie du millésime le plus proche qui
    connaît son code, marquée `geometry_approx: true` ;
  - sinon, géométrie absente (signalée).

Chaque géométrie sort en deux qualités : brute (`--geojson-raw`) et simplifiée
pour le web (shapely simplify, topologie préservée par géométrie).

Exemples :
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
                        SCHEMA_SQL, INSERT_SQL, version_row)

SIMPLIFY_TOLERANCE_DEG = 0.0005  # ~50 m : suffisant pour l'affichage web communal


# --------------------------------------------------------------------------
#  Chargement des millésimes de géométrie
# --------------------------------------------------------------------------
def load_shp_vintage(path_pattern: str) -> dict[str, object]:
    """COMMUNE.shp d'une édition Admin Express -> {code_insee: shapely geometry (WGS84)}."""
    matches = glob.glob(path_pattern, recursive=True)
    if not matches:
        raise FileNotFoundError(f"Aucun shapefile ne correspond à {path_pattern}")
    shp_path = matches[0]

    cpg_path = Path(shp_path[:-4] + ".cpg")
    enc = "utf-8"
    if cpg_path.exists():
        cpg = cpg_path.read_text().strip().upper()
        if "1252" in cpg or "ANSI" in cpg or "LATIN" in cpg:
            enc = "latin-1"

    # Reprojection si l'édition est en Lambert-93 (le .prj fait foi)
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
    print(f"  [ok] {len(geoms)} communes chargées depuis {shp_path}")
    return geoms


def load_parquet_vintage(path: str) -> dict[str, object]:
    """commune.parquet (Admin Express 4.0, GeoParquet WGS84) -> {code_insee: geometry}."""
    import pyarrow.parquet as pq
    t = pq.read_table(path, columns=["code_insee", "geometrie"])
    geoms = {}
    for code, wkb in zip(t["code_insee"].to_pylist(), t["geometrie"].to_pylist()):
        if code and wkb:
            geoms[code] = from_wkb(wkb)
    print(f"  [ok] {len(geoms)} communes chargées depuis {path}")
    return geoms


# --------------------------------------------------------------------------
#  Appariement version <-> millésime
# --------------------------------------------------------------------------
def pick_vintage(v: CommuneVersion, vintages: list[tuple[str, dict]]) -> tuple[str, bool] | None:
    """Choisit le millésime pour une version. Retourne (ref_date, approx) ou None.

    Exact : la date de référence du millésime tombe dans [valid_from, valid_to)
            ET le millésime connaît le code (le plus récent de ces millésimes).
    Approx : sinon, le millésime connaissant le code dont la date est la plus
             proche de la période de validité.
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
    """Retourne (features_simplifiées, features_brutes) en GeoJSON."""
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
        print(f"  [!] {missing} versions sans géométrie (code absent de tous les millésimes)")
    return simple_feats, raw_feats


# --------------------------------------------------------------------------
#  Chargement PostGIS (streaming, brute + simplifiée)
# --------------------------------------------------------------------------
def stream_to_postgis(versions: list[CommuneVersion],
                      vintages: list[tuple[str, dict]],
                      simplify_tol: float, dsn: str) -> bool:
    """Charge les versions + géométries dans PostGIS par lots (mémoire bornée)."""
    try:
        import psycopg2
        from psycopg2.extras import execute_batch
    except ImportError:
        print("  [!] psycopg2 non installé -> pas de chargement PostGIS.")
        return False
    try:
        conn = psycopg2.connect(dsn)
    except Exception as e:
        print(f"  [!] Connexion PostGIS impossible ({e}).")
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
            v.geometry = v.geometry_simple = None   # libère la mémoire au fil de l'eau
            if len(batch) >= 200:
                execute_batch(cur, INSERT_SQL, batch, page_size=50)
                total += len(batch)
                batch = []
                if total % 5000 < 200:
                    print(f"  ... {total} versions chargées")
        if batch:
            execute_batch(cur, INSERT_SQL, batch, page_size=50)
            total += len(batch)
    conn.close()
    print(f"  [ok] {total} versions chargées dans PostGIS "
          f"({approx_n} géométries approx, {missing} sans géométrie)")
    return True


# --------------------------------------------------------------------------
#  Point d'entrée
# --------------------------------------------------------------------------
def parse_vintage_arg(s: str) -> tuple[str, str]:
    d, _, p = s.partition("=")
    date.fromisoformat(d)  # valide le format
    return d, p


def main():
    ap = argparse.ArgumentParser(description="Raccord géométries Admin Express sur le modèle temporel COG")
    ap.add_argument("--millesimes", type=int, nargs="+", default=[2025])
    ap.add_argument("--data-dir", type=Path, required=True,
                    help="Dossier des CSV INSEE (commune_YYYY.csv, mvtcommune_YYYY.csv)")
    ap.add_argument("--shp", action="append", default=[], metavar="DATE=GLOB",
                    help="Millésime shapefile : 2019-01-01=chemin/**/COMMUNE.shp")
    ap.add_argument("--parquet", action="append", default=[], metavar="DATE=PATH",
                    help="Millésime GeoParquet : 2026-01-01=commune.parquet")
    ap.add_argument("--dept", default=None, help="Limiter la sortie à un département (ex: 01)")
    ap.add_argument("--simplify", type=float, default=SIMPLIFY_TOLERANCE_DEG)
    ap.add_argument("--geojson", type=Path, default=None,
                    help="Sortie GeoJSON simplifiée (optionnel)")
    ap.add_argument("--geojson-raw", type=Path, default=None,
                    help="Sortie GeoJSON avec géométries brutes (optionnel)")
    ap.add_argument("--dsn", nargs="?", const="ENV", default=None, metavar="DSN",
                    help="charge versions + géométries dans PostGIS ; sans valeur, "
                         "utilise $PG_DSN (jamais implicite : join-01 ne doit pas "
                         "écraser la table pleine France)")
    args = ap.parse_args()
    if args.dsn == "ENV":
        args.dsn = os.environ.get("PG_DSN") or sys.exit("--dsn sans valeur mais $PG_DSN absent.")
    if not args.geojson and not args.geojson_raw and not args.dsn:
        sys.exit("Aucune sortie demandée (--geojson, --geojson-raw ou --dsn).")

    print("Chargement des millésimes de géométrie :")
    vintages: list[tuple[str, dict]] = []
    for spec in args.shp:
        d, p = parse_vintage_arg(spec)
        vintages.append((d, load_shp_vintage(p)))
    for spec in args.parquet:
        d, p = parse_vintage_arg(spec)
        vintages.append((d, load_parquet_vintage(p)))
    if not vintages:
        sys.exit("Aucun millésime de géométrie fourni (--shp / --parquet).")
    vintages.sort()

    versions = build_versions(args.millesimes, args.data_dir, use_network=False)
    if args.dept:
        versions = [v for v in versions if v.code.startswith(args.dept)]
    print(f"Versions temporelles : {len(versions)}" + (f" (département {args.dept})" if args.dept else ""))

    if args.geojson or args.geojson_raw:
        simple_feats, raw_feats = attach_geometries(versions, vintages, args.simplify)
        with_geom = sum(1 for f in simple_feats if f["geometry"])
        approx = sum(1 for f in simple_feats if f["properties"]["geometry_approx"])
        print(f"Géométrie attachée : {with_geom}/{len(simple_feats)} (dont {approx} approx héritées)")
        if args.geojson:
            args.geojson.write_text(json.dumps(
                {"type": "FeatureCollection", "features": simple_feats}, ensure_ascii=False), encoding="utf-8")
            print(f"  [ok] simplifié -> {args.geojson}")
        if args.geojson_raw:
            args.geojson_raw.write_text(json.dumps(
                {"type": "FeatureCollection", "features": raw_feats}, ensure_ascii=False), encoding="utf-8")
            print(f"  [ok] brut      -> {args.geojson_raw}")

    if args.dsn:
        if not stream_to_postgis(versions, vintages, args.simplify, args.dsn):
            sys.exit(1)


if __name__ == "__main__":
    main()
