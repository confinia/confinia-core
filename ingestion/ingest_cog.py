#!/usr/bin/env python3
"""
Ingestion of the Code Officiel Géographique (INSEE) + IGN geometries into PostGIS,
with a valid_from / valid_to temporal model queryable by date.

Two INSEE files are enough to get started:
  1. The COMMUNE file of a vintage       -> the state of communes on January 1st of that year
  2. The MVTCOMMUNE file (movements)     -> the events (merger, creation, renaming...)

The script:
  - downloads (or reads locally) these files for several vintages
  - rebuilds a temporal table: one row = one (code, name) valid over [valid_from, valid_to)
  - derives parent/child links from the movements
  - joins the IGN Admin Express geometry when it is provided
  - loads everything into PostGIS (or exports to GeoJSON if no database)

Designed to be robust: if the network or the database is missing, it falls back
to a demonstration mode with a small built-in sample, so it can be run anywhere.
"""

from __future__ import annotations
import argparse
import csv
import io
import json
import os
import sys
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# --------------------------------------------------------------------------
#  Source configuration
# --------------------------------------------------------------------------
# WARNING: INSEE URLs change with every vintage and are not stable over time.
# They are centralized here so they can be fixed easily.
# Fill in the URL of the "commune" and "movements" file per vintage.
# Leave None to use a local file (--data-dir) or demo mode.
INSEE_SOURCES: dict[int, dict] = {
    2020: {"commune": None, "mvt": None},
    2023: {"commune": None, "mvt": None},
    2025: {"commune": None, "mvt": None},
}

# Expected columns in the COMMUNE file (vintage >= 2019):
#   TYPECOM, COM, NCC, NCCENR, LIBELLE, ...
# Expected columns in the MVTCOMMUNE file (movements):
#   MOD, DATE_EFF, TYPECOM_AV, COM_AV, LIBELLE_AV, TYPECOM_AP, COM_AP, LIBELLE_AP, ...
#
# Main MOD codes (event type) — official INSEE labels, kept in French:
#   10 changement de nom (renaming) | 20 création (creation) | 21 rétablissement (re-establishment)
#   30 suppression (abolition) | 31 fusion simple (simple merger) | 32 création commune nouvelle
#   33 fusion-association | 34 transformation de fusion | 41 ... etc.
MOD_LABELS = {
    "10": "changement de nom",
    "20": "création",
    "21": "rétablissement",
    "30": "suppression",
    "31": "fusion",
    "32": "création de commune nouvelle",
    "33": "fusion-association",
    "34": "transformation de commune associée",
    "35": "suppression de commune déléguée",
    "41": "changement de code dû à un transfert de chef-lieu",
    "50": "changement de code dû à un changement de département",
}

# Semantics of AV -> AP rows whose code CHANGES (rows with an identical code
# are handled separately: identity or renaming).
#   - The AV version only ends if the event makes it disappear: abolition,
#     mergers (absorbed side), code changes. A creation (20) or a partial
#     re-establishment (21) lets the source commune live on — that is the
#     "Marseille ends in 1946" bug (creation of Plan-de-Cuques).
#   - The AP version only starts if the event creates it: creation,
#     re-establishment, commune nouvelle, code changes. A simple merger or a
#     fusion-association does not (re)start the absorber — that is the
#     "Manosque starts in 1975" bug (absorption of associated communes).
ENDS_AV_CROSS = {"30", "31", "32", "33", "41", "50"}
STARTS_AP_CROSS = {"20", "21", "32", "41", "50"}

FAR_FUTURE = "9999-01-01"  # convention for "still valid"
COG_FLOOR = "1943-01-01"   # lower bound of the INSEE movements history


# --------------------------------------------------------------------------
#  Data model
# --------------------------------------------------------------------------
@dataclass
class CommuneVersion:
    """A dated version of a commune: (code, name) valid over [valid_from, valid_to).

    The same (code, name) can produce several versions if the commune is
    re-established after a merger (e.g. Celles 15148, gone in 2016, re-established in 2025).
    """
    code: str
    nom: str
    valid_from: str            # ISO date
    valid_to: str              # ISO date or FAR_FUTURE
    parents: list[str] = field(default_factory=list)   # codes it originates from
    children: list[str] = field(default_factory=list)  # codes that replace it
    geometry: dict | None = None                        # GeoJSON geometry (raw)
    geometry_simple: dict | None = None                 # GeoJSON geometry (web-simplified)
    geometry_vintage: str | None = None                 # date of the IGN vintage used
    geometry_approx: bool = False                       # inherited from a neighboring vintage


# --------------------------------------------------------------------------
#  Fetching the files (network or local)
# --------------------------------------------------------------------------
def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    req = Request(url, headers={"User-Agent": "chronocarte-ingest/0.1"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()


def read_csv_from_source(src: str | None, local: Path | None, encoding="utf-8") -> list[dict]:
    """Reads a CSV from a URL (zip or raw) or a local file. Returns a list of dicts."""
    raw: bytes | None = None

    if local and local.exists():
        raw = local.read_bytes()
    elif src:
        raw = fetch_bytes(src)

    if raw is None:
        return []

    # Unzip if needed
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            # take the first .csv/.txt in the zip
            name = next((n for n in z.namelist()
                         if n.lower().endswith((".csv", ".txt"))), None)
            if not name:
                return []
            raw = z.read(name)

    # utf-8-sig: INSEE CSVs >= 2020 come with a BOM
    text = raw.decode("utf-8-sig" if encoding == "utf-8" else encoding, errors="replace")
    # INSEE uses the comma; let the sniffer decide when needed
    delimiter = ","
    first_line = text.splitlines()[0] if text else ""
    if first_line.count(";") > first_line.count(","):
        delimiter = ";"
    # Headers normalized to UPPERCASE: vintages >= 2020 are lowercase
    # (typecom, com, libelle…), the code expects TYPECOM/COM/LIBELLE.
    return [{(k or "").upper().strip(): v for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(text), delimiter=delimiter)]


# --------------------------------------------------------------------------
#  Building the temporal model
# --------------------------------------------------------------------------
def build_versions(millesimes: list[int],
                    data_dir: Path | None,
                    use_network: bool) -> list[CommuneVersion]:
    """
    Rebuilds the temporal versions from the COMMUNE + MVTCOMMUNE files.

    Simple, readable strategy:
      - each COMMUNE file of a vintage gives the set of communes on January 1st
      - set valid_from = January 1st of the oldest vintage where the (code, name) appears
      - valid_to = date of the event that ends this (code, name), read from MVTCOMMUNE
      - the movements give the parent/child links
    """
    # 1. Load the state of communes for each vintage
    snapshots: dict[int, dict[str, str]] = {}   # year -> {code: name}
    all_movements: list[dict] = []

    for y in sorted(millesimes):
        src = INSEE_SOURCES.get(y, {})
        com_url = src.get("commune") if use_network else None
        mvt_url = src.get("mvt") if use_network else None
        com_local = (data_dir / f"commune_{y}.csv") if data_dir else None
        mvt_local = (data_dir / f"mvtcommune_{y}.csv") if data_dir else None

        commune_rows = read_csv_from_source(com_url, com_local)
        mvt_rows = read_csv_from_source(mvt_url, mvt_local)

        snap: dict[str, str] = {}
        for row in commune_rows:
            # keep only full-status communes ("de plein exercice", TYPECOM == COM)
            if row.get("TYPECOM", "COM") != "COM":
                continue
            code = row.get("COM") or row.get("CODGEO") or ""
            nom = row.get("LIBELLE") or row.get("NCCENR") or ""
            if code:
                snap[code] = nom
        if snap:
            snapshots[y] = snap

        for m in mvt_rows:
            m["_millesime"] = y
        all_movements.extend(mvt_rows)

    # Demo mode if nothing was loaded
    if not snapshots:
        print("  [i] No real source available -> built-in demonstration dataset.")
        return demo_versions()

    years = sorted(snapshots)

    # 2. Index the movements by starting and ending (code, name).
    #    DATE_EFF is the source of truth for transitions: it is the actual date
    #    on which "commune before" becomes "commune after".
    #    The same (code, name) can start/end several times (re-establishments),
    #    hence SETS of dates, turned into periods in step 3.
    ends_at: dict[tuple[str, str], set[str]] = {}    # (code, name) -> end dates
    starts_at: dict[tuple[str, str], set[str]] = {}  # (code, name) -> start dates
    child_links: dict[tuple[str, str], set] = {}     # (code, name) -> {(date, code_ap)}
    parent_links: dict[tuple[str, str], set] = {}    # (code, name) -> {(date, code_av)}

    # Pass 1 — identity rows (same COM code+name on both sides): the commune
    # passes through the event. They CANCEL any cross start/end of the same day:
    # a commune nouvelle keeping the chef-lieu's code and name (Osmery 2024,
    # Neufchâteau 2025…) must not have its past erased by the cross row
    # coming from the absorbed commune.
    identity: set[tuple[str, str, str]] = set()
    for m in all_movements:
        d = (m.get("DATE_EFF") or "").strip()
        if len(d) != 10:
            continue
        if ((m.get("TYPECOM_AV") or "COM").strip() == "COM"
                and (m.get("TYPECOM_AP") or "COM").strip() == "COM"):
            code_av = (m.get("COM_AV") or "").strip()
            code_ap = (m.get("COM_AP") or "").strip()
            nom_av = (m.get("LIBELLE_AV") or m.get("NCCENR_AV") or "").strip()
            nom_ap = (m.get("LIBELLE_AP") or m.get("NCCENR_AP") or "").strip()
            if code_av and code_av == code_ap and nom_av == nom_ap:
                identity.add((code_av, nom_av, d))

    for m in all_movements:
        d = (m.get("DATE_EFF") or "").strip()
        if len(d) != 10:
            continue
        # Only full-status communes (TYPECOM == COM) concern us.
        # A merger ALSO produces rows toward COMD/COMA (delegated/associated
        # communes) carrying the same (code, name) as the vanished commune — without
        # this filter, they overwrite the dates of the real version (case 01033 Bellegarde).
        av_is_com = (m.get("TYPECOM_AV") or "COM").strip() == "COM"
        ap_is_com = (m.get("TYPECOM_AP") or "COM").strip() == "COM"
        code_av = (m.get("COM_AV") or "").strip() if av_is_com else ""
        nom_av = (m.get("LIBELLE_AV") or m.get("NCCENR_AV") or "").strip()
        code_ap = (m.get("COM_AP") or "").strip() if ap_is_com else ""
        nom_ap = (m.get("LIBELLE_AP") or m.get("NCCENR_AP") or "").strip()
        # Identity row: the commune passes through the event unchanged (e.g. the
        # absorbing commune of a fusion-association, MOD 33/34). It neither
        # starts nor ends here — ignore, otherwise we kill it at this date.
        if code_av and code_av == code_ap and nom_av == nom_ap:
            continue
        mod = (m.get("MOD") or "").strip()
        if code_av and code_av == code_ap:
            # Same code, different name: renaming (whatever the MOD) —
            # the old version ends, the new one starts.
            if nom_av:
                ends_at.setdefault((code_av, nom_av), set()).add(d)
            if nom_ap:
                starts_at.setdefault((code_ap, nom_ap), set()).add(d)
        else:
            # Different code: the semantics depend on the event type — and an
            # identity row of the same day wins (the commune survives).
            if (code_av and nom_av and mod in ENDS_AV_CROSS
                    and (code_av, nom_av, d) not in identity):
                ends_at.setdefault((code_av, nom_av), set()).add(d)
            if (code_ap and nom_ap and mod in STARTS_AP_CROSS
                    and (code_ap, nom_ap, d) not in identity):
                starts_at.setdefault((code_ap, nom_ap), set()).add(d)
        # dated parent/child links, ignoring self-references (same code+name)
        if code_av and code_ap and (code_av, nom_av) != (code_ap, nom_ap):
            child_links.setdefault((code_av, nom_av), set()).add((d, code_ap))
            parent_links.setdefault((code_ap, nom_ap), set()).add((d, code_av))

    # 3. Build the periods of each (code, name) encountered in the snapshots
    #    OR in the movements. The movements file is complete since 1943: a
    #    (code, name) with no incoming event therefore exists since (at least)
    #    COG_FLOOR — this is what makes the counts correct at any date, even
    #    with a single COG vintage loaded.
    keys: set[tuple[str, str]] = set()
    for y in years:
        for code, nom in snapshots[y].items():
            keys.add((code, nom))
    keys |= set(ends_at) | set(starts_at)

    versions: list[CommuneVersion] = []
    for (code, nom) in sorted(keys):
        k = (code, nom)
        S, E = starts_at.get(k, set()), ends_at.get(k, set())
        dates = sorted(S | E)

        periods: list[tuple[str, str]] = []   # (valid_from, valid_to)
        open_from: str | None = None
        # First event = an END alone: the version existed before our data —
        # unknown start, bounded at COG_FLOOR.
        if dates and dates[0] in E and dates[0] not in S:
            open_from = COG_FLOOR
        for d in dates:
            has_s, has_e = d in S, d in E
            if open_from is not None:
                if has_e:
                    if d > open_from:
                        periods.append((open_from, d))
                    # end + re-start on the same day = continuity (name round-trip)
                    open_from = d if has_s else None
                # start alone while already open: keep the oldest
            else:
                if has_s and has_e:
                    # start + end on the same day with no past: zero-duration
                    # existence, transition artifact (Freigné 44225, Pont-Farcy
                    # 50649: simultaneous department change + merger).
                    pass
                elif has_s:
                    open_from = d
                # end alone with no open period: anomaly, ignored
        if open_from is not None:
            periods.append((open_from, FAR_FUTURE))
        if not dates:
            # present in a snapshot, no event: has existed forever
            periods.append((COG_FLOOR, FAR_FUTURE))

        for vf, vt in periods:
            # Dated links, attached to the period they concern: a parent
            # explains the start of the period OR an absorption during its
            # lifetime (e.g. Coupy -> Bellegarde in 1971, with no version end);
            # a child, a mid-life departure (detached creation) OR the end.
            parents = sorted({c for d, c in parent_links.get(k, ()) if vf <= d < vt})
            children = sorted({c for d, c in child_links.get(k, ()) if vf < d <= vt})
            versions.append(CommuneVersion(
                code=code, nom=nom, valid_from=vf, valid_to=vt,
                parents=parents, children=children,
            ))

    return versions


# --------------------------------------------------------------------------
#  Demonstration dataset (taken from real INSEE cases)
# --------------------------------------------------------------------------
def demo_versions() -> list[CommuneVersion]:
    def rect(x0, y0, x1, y1):
        return {"type": "Polygon",
                "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}

    v = []
    # Valserhône: 2019 merger of 3 communes (real case, COM 01033)
    v.append(CommuneVersion("01033", "Bellegarde-sur-Valserine", "2003-01-01", "2019-01-01",
                            children=["01033"], geometry=rect(5.80, 46.10, 5.88, 46.16)))
    v.append(CommuneVersion("01091", "Châtillon-en-Michaille", "2003-01-01", "2019-01-01",
                            children=["01033"], geometry=rect(5.88, 46.10, 5.96, 46.16)))
    v.append(CommuneVersion("01205", "Lancrans", "2003-01-01", "2019-01-01",
                            children=["01033"], geometry=rect(5.80, 46.04, 5.88, 46.10)))
    v.append(CommuneVersion("01033", "Valserhône", "2019-01-01", FAR_FUTURE,
                            parents=["01033", "01091", "01205"],
                            geometry={"type": "MultiPolygon", "coordinates": [
                                rect(5.80, 46.10, 5.88, 46.16)["coordinates"],
                                rect(5.88, 46.10, 5.96, 46.16)["coordinates"],
                                rect(5.80, 46.04, 5.88, 46.10)["coordinates"]]}))
    # Neussargues en Pinatelle: 2016 merger then 2025 re-establishment (real case, Cantal)
    v.append(CommuneVersion("15148", "Celles", "2003-01-01", "2016-01-01",
                            children=["15148"], geometry=rect(6.02, 46.10, 6.09, 46.15)))
    v.append(CommuneVersion("15148", "Neussargues en Pinatelle", "2016-01-01", "2025-01-01",
                            parents=["15148"], geometry=rect(6.02, 46.10, 6.16, 46.15)))
    v.append(CommuneVersion("15148", "Celles", "2025-01-01", FAR_FUTURE,
                            parents=["15148"], geometry=rect(6.02, 46.10, 6.09, 46.15)))
    return v


# --------------------------------------------------------------------------
#  Outputs: PostGIS or GeoJSON
# --------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

DROP MATERIALIZED VIEW IF EXISTS departement_geom;
DROP TABLE IF EXISTS commune_version CASCADE;
-- General table of temporal administrative units (Step 5): FR communes and
-- NUTS regions share the same model. The name commune_version is historical —
-- a rename to admin_unit_version is being considered for the pre-beta
-- hardening.
CREATE TABLE commune_version (
    id               bigserial PRIMARY KEY,
    code             text        NOT NULL,
    nom              text        NOT NULL,
    unit_type        text        NOT NULL DEFAULT 'commune',  -- commune | nuts0..nuts3 | gemeinde…
    country          text        NOT NULL DEFAULT 'FR',
    valid_from       date        NOT NULL,
    valid_to         date        NOT NULL,
    parents          text[]      NOT NULL DEFAULT '{}',
    children         text[]      NOT NULL DEFAULT '{}',
    geometry_vintage date,
    geometry_approx  boolean     NOT NULL DEFAULT false,
    geom             geometry(Geometry, 4326),   -- raw (source of truth, spatial queries)
    geom_simple      geometry(Geometry, 4326)    -- simplified ~50 m (served to the web)
);
CREATE INDEX idx_cv_type_country  ON commune_version (unit_type, country);

-- Temporal index: speeds up "which communes at a given date"
CREATE INDEX idx_cv_validity      ON commune_version (valid_from, valid_to);
-- API contract index: "this code at a given date" (TODO Step 2)
CREATE INDEX idx_cv_code_validity ON commune_version (code, valid_from, valid_to);
-- Spatial indexes: "which commune contains this point"
CREATE INDEX idx_cv_geom          ON commune_version USING gist (geom);
CREATE INDEX idx_cv_geom_simple   ON commune_version USING gist (geom_simple);
"""

# Department outlines (navigation layer of the demo / API): union of the
# current communes per department, materialized at the end of loading.
DEPT_GEOM_SQL = """
DROP MATERIALIZED VIEW IF EXISTS departement_geom;
-- Union of the RAW geometries: the shared IGN borders dissolve cleanly
-- (the union of the simplified ones generates thousands of artifacts that
-- topological simplification can no longer remove — 13 MB payload).
-- Simplified once here so the API serves a light payload.
CREATE MATERIALIZED VIEW departement_geom AS
SELECT CASE WHEN code LIKE '97%' THEN left(code, 3) ELSE left(code, 2) END AS dept,
       ST_Multi(ST_SimplifyPreserveTopology(ST_Union(geom), 0.002)) AS geom
FROM commune_version
WHERE unit_type = 'commune' AND valid_to = '9999-01-01' AND geom IS NOT NULL
GROUP BY 1;
"""

INSERT_SQL = """
    INSERT INTO commune_version
      (code, nom, valid_from, valid_to, parents, children,
       geometry_vintage, geometry_approx, geom, geom_simple)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
        CASE WHEN %s IS NULL THEN NULL ELSE ST_SetSRID(ST_GeomFromGeoJSON(%s),4326) END,
        CASE WHEN %s IS NULL THEN NULL ELSE ST_SetSRID(ST_GeomFromGeoJSON(%s),4326) END)
"""


def version_row(v: CommuneVersion) -> tuple:
    raw = json.dumps(v.geometry) if v.geometry else None
    simple = json.dumps(v.geometry_simple) if v.geometry_simple else None
    return (v.code, v.nom, v.valid_from, v.valid_to, v.parents, v.children,
            v.geometry_vintage, v.geometry_approx, raw, raw, simple, simple)


def to_postgis(versions: list[CommuneVersion], dsn: str) -> bool:
    try:
        import psycopg2
        from psycopg2.extras import execute_batch
    except ImportError:
        print("  [!] psycopg2 not installed -> cannot write to PostGIS.")
        return False
    try:
        conn = psycopg2.connect(dsn)
    except Exception as e:
        print(f"  [!] PostGIS connection failed ({e}).")
        return False

    with conn, conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        execute_batch(cur, INSERT_SQL, [version_row(v) for v in versions], page_size=200)
    conn.close()
    print(f"  [ok] {len(versions)} versions written to PostGIS.")
    return True


def to_geojson(versions: list[CommuneVersion], out: Path) -> None:
    fc = {"type": "FeatureCollection", "features": []}
    for v in versions:
        fc["features"].append({
            "type": "Feature",
            "geometry": v.geometry,
            "properties": {
                "code": v.code, "nom": v.nom,
                "valid_from": v.valid_from,
                "valid_to": None if v.valid_to == FAR_FUTURE else v.valid_to,
                "parents": v.parents, "children": v.children,
            }
        })
    out.write_text(json.dumps(fc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  [ok] {len(versions)} versions exported -> {out}")


# --------------------------------------------------------------------------
#  Quick quality checks
# --------------------------------------------------------------------------
def sanity_checks(versions: list[CommuneVersion]) -> None:
    print("\nChecks:")
    # 1. no valid_to <= valid_from
    bad = [v for v in versions if v.valid_to <= v.valid_from]
    print(f"  invalid periods (valid_to <= valid_from): {len(bad)}")
    # 2. counts vs published INSEE figures (metropolitan France + DROM, on January 1st)
    published = {"2015-01-02": 36658, "2020-01-02": 34968, "2025-01-02": 34875}
    for d, expected in published.items():
        active = [v for v in versions if v.valid_from <= d < v.valid_to]
        print(f"  active on {d}: {len(active)} (INSEE published: {expected})")


# --------------------------------------------------------------------------
#  Entry point
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="INSEE COG ingestion -> PostGIS (temporal model)")
    ap.add_argument("--millesimes", type=int, nargs="+", default=[2020, 2023, 2025],
                    help="COG vintage years to load")
    ap.add_argument("--data-dir", type=Path, default=None,
                    help="Directory of local CSV files (commune_YYYY.csv, mvtcommune_YYYY.csv)")
    ap.add_argument("--network", action="store_true",
                    help="Allow downloading from the configured INSEE URLs")
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN"),
                    help="PostGIS DSN (e.g. postgresql://user:pwd@localhost/chronocarte)")
    ap.add_argument("--geojson", type=Path, default=Path("communes_temporel.geojson"),
                    help="GeoJSON output path (fallback when no database)")
    args = ap.parse_args()

    print(f"Requested vintages: {args.millesimes}")
    versions = build_versions(args.millesimes, args.data_dir, args.network)
    print(f"Rebuilt versions: {len(versions)}")

    sanity_checks(versions)

    wrote_db = False
    if args.dsn:
        wrote_db = to_postgis(versions, args.dsn)
    if not wrote_db:
        to_geojson(versions, args.geojson)

    print("\nDone.")


if __name__ == "__main__":
    main()
