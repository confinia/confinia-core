#!/usr/bin/env python3
"""
Ingestion du Code Officiel Géographique (INSEE) + géométries IGN vers PostGIS,
avec un modèle temporel valid_from / valid_to interrogeable par date.

Deux fichiers INSEE suffisent pour démarrer :
  1. Le fichier COMMUNE d'un millésime  -> l'état des communes au 1er janvier de l'année
  2. Le fichier MVTCOMMUNE (mouvements)  -> les événements (fusion, création, renommage...)

Le script :
  - télécharge (ou lit en local) ces fichiers pour plusieurs millésimes
  - reconstruit une table temporelle : une ligne = un (code, nom) valide sur [valid_from, valid_to)
  - déduit les liens parent/enfant à partir des mouvements
  - joint la géométrie IGN Admin Express quand elle est fournie
  - charge le tout dans PostGIS (ou exporte en GeoJSON si pas de base)

Conçu pour être robuste : si le réseau ou la base manquent, il bascule en mode
démonstration sur un petit échantillon intégré, pour qu'on puisse le lancer partout.
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
#  Configuration des sources
# --------------------------------------------------------------------------
# ATTENTION : les URLs INSEE changent à chaque millésime et ne sont pas stables
# dans le temps. On les centralise ici pour pouvoir les corriger facilement.
# Renseignez l'URL du fichier "commune" et "mouvements" par millésime.
# Laissez None pour utiliser un fichier local (--data-dir) ou le mode démo.
INSEE_SOURCES: dict[int, dict] = {
    2020: {"commune": None, "mvt": None},
    2023: {"commune": None, "mvt": None},
    2025: {"commune": None, "mvt": None},
}

# Colonnes attendues dans le fichier COMMUNE (millésime >= 2019)
#   TYPECOM, COM, NCC, NCCENR, LIBELLE, ...
# Colonnes attendues dans le fichier MVTCOMMUNE (mouvements) :
#   MOD, DATE_EFF, TYPECOM_AV, COM_AV, LIBELLE_AV, TYPECOM_AP, COM_AP, LIBELLE_AP, ...
#
# Codes MOD (type d'événement) principaux :
#   10 changement de nom | 20 création | 21 rétablissement
#   30 suppression | 31 fusion simple | 32 création commune nouvelle
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

FAR_FUTURE = "9999-01-01"  # convention pour "toujours valide"


# --------------------------------------------------------------------------
#  Modèle de données
# --------------------------------------------------------------------------
@dataclass
class CommuneVersion:
    """Une version datée d'une commune : (code, nom) valide sur [valid_from, valid_to)."""
    code: str
    nom: str
    valid_from: str            # ISO date
    valid_to: str              # ISO date ou FAR_FUTURE
    parents: list[str] = field(default_factory=list)   # codes dont elle est issue
    children: list[str] = field(default_factory=list)  # codes qui la remplacent
    geometry: dict | None = None                        # GeoJSON geometry


# --------------------------------------------------------------------------
#  Récupération des fichiers (réseau ou local)
# --------------------------------------------------------------------------
def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    req = Request(url, headers={"User-Agent": "chronocarte-ingest/0.1"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()


def read_csv_from_source(src: str | None, local: Path | None, encoding="utf-8") -> list[dict]:
    """Lit un CSV depuis une URL (zip ou brut) ou un fichier local. Retourne une liste de dicts."""
    raw: bytes | None = None

    if local and local.exists():
        raw = local.read_bytes()
    elif src:
        raw = fetch_bytes(src)

    if raw is None:
        return []

    # Dézipper si nécessaire
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            # on prend le premier .csv/.txt du zip
            name = next((n for n in z.namelist()
                         if n.lower().endswith((".csv", ".txt"))), None)
            if not name:
                return []
            raw = z.read(name)

    text = raw.decode(encoding, errors="replace")
    # l'INSEE utilise la virgule ; on laisse le sniffer trancher au besoin
    delimiter = ","
    first_line = text.splitlines()[0] if text else ""
    if first_line.count(";") > first_line.count(","):
        delimiter = ";"
    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))


# --------------------------------------------------------------------------
#  Construction du modèle temporel
# --------------------------------------------------------------------------
def build_versions(millesimes: list[int],
                    data_dir: Path | None,
                    use_network: bool) -> list[CommuneVersion]:
    """
    Reconstruit les versions temporelles à partir des fichiers COMMUNE + MVTCOMMUNE.

    Stratégie simple et lisible :
      - chaque fichier COMMUNE d'un millésime donne l'ensemble des communes au 1er janvier
      - on pose valid_from = 1er janvier du plus ancien millésime où le (code, nom) apparaît
      - valid_to = date de l'événement qui met fin à ce (code, nom), lu dans MVTCOMMUNE
      - les mouvements donnent les liens parent/enfant
    """
    # 1. Charger l'état des communes pour chaque millésime
    snapshots: dict[int, dict[str, str]] = {}   # année -> {code: nom}
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
            # on ne garde que les communes "de plein exercice" (TYPECOM == COM)
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

    # Mode démo si rien n'a été chargé
    if not snapshots:
        print("  [i] Aucune source réelle disponible -> jeu de démonstration intégré.")
        return demo_versions()

    years = sorted(snapshots)
    default_from = f"{years[0]}-01-01"

    # 2. Indexer les mouvements par (code, nom) de départ et d'arrivée.
    #    DATE_EFF est la source de vérité des transitions : c'est la vraie date
    #    à laquelle "commune avant" devient "commune après".
    ends_at: dict[tuple[str, str], str] = {}    # (code, nom) -> date de fin (événement sortant)
    starts_at: dict[tuple[str, str], str] = {}  # (code, nom) -> date de début (événement entrant)
    child_links: dict[tuple[str, str], set] = {}
    parent_links: dict[tuple[str, str], set] = {}

    for m in all_movements:
        d = (m.get("DATE_EFF") or "").strip()
        if len(d) != 10:
            continue
        code_av = (m.get("COM_AV") or "").strip()
        nom_av = (m.get("LIBELLE_AV") or m.get("NCCENR_AV") or "").strip()
        code_ap = (m.get("COM_AP") or "").strip()
        nom_ap = (m.get("LIBELLE_AP") or m.get("NCCENR_AP") or "").strip()
        if code_av and nom_av:
            k = (code_av, nom_av)
            # la version "avant" prend fin à la date de l'événement (on garde la plus ancienne)
            if k not in ends_at or d < ends_at[k]:
                ends_at[k] = d
        if code_ap and nom_ap:
            k = (code_ap, nom_ap)
            # la version "après" démarre à la date de l'événement (on garde la plus récente)
            if k not in starts_at or d > starts_at[k]:
                starts_at[k] = d
        # liens parent/enfant, en ignorant les auto-références (même code+nom)
        if code_av and code_ap and (code_av, nom_av) != (code_ap, nom_ap):
            child_links.setdefault((code_av, nom_av), set()).add(code_ap)
            parent_links.setdefault((code_ap, nom_ap), set()).add(code_av)

    # 3. Construire une version par (code, nom) rencontré, dans les snapshots OU les mouvements
    keys: set[tuple[str, str]] = set()
    for y in years:
        for code, nom in snapshots[y].items():
            keys.add((code, nom))
    keys |= set(ends_at) | set(starts_at)

    versions: list[CommuneVersion] = []
    for (code, nom) in sorted(keys):
        start = starts_at.get((code, nom))      # date d'un événement entrant, ou None
        end = ends_at.get((code, nom))          # date d'un événement sortant, ou None

        # valid_to : date de l'événement de fin, sinon "toujours valide"
        vt = end if end else FAR_FUTURE

        # valid_from : date de l'événement de création si connue.
        # Sinon on prend le 1er millésime chargé — SAUF si la version se termine
        # avant ce millésime (commune disparue avant notre plus ancien COG) :
        # dans ce cas on ne connaît pas la vraie date de début, on la borne juste
        # avant la fin pour garder une période cohérente, et on le signale.
        if start:
            vf = start
        elif vt != FAR_FUTURE and vt <= default_from:
            # début réel inconnu (antérieur aux données chargées)
            vf = "1943-01-01"   # borne basse conventionnelle du COG
        else:
            vf = default_from

        versions.append(CommuneVersion(
            code=code, nom=nom, valid_from=vf, valid_to=vt,
            parents=sorted(parent_links.get((code, nom), set())),
            children=sorted(child_links.get((code, nom), set())),
        ))

    return versions


# --------------------------------------------------------------------------
#  Jeu de démonstration (repris de vrais cas INSEE)
# --------------------------------------------------------------------------
def demo_versions() -> list[CommuneVersion]:
    def rect(x0, y0, x1, y1):
        return {"type": "Polygon",
                "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}

    v = []
    # Valserhône : fusion 2019 de 3 communes (cas réel, COM 01033)
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
    # Neussargues en Pinatelle : fusion 2016 puis rétablissement 2025 (cas réel, Cantal)
    v.append(CommuneVersion("15148", "Celles", "2003-01-01", "2016-01-01",
                            children=["15148"], geometry=rect(6.02, 46.10, 6.09, 46.15)))
    v.append(CommuneVersion("15148", "Neussargues en Pinatelle", "2016-01-01", "2025-01-01",
                            parents=["15148"], geometry=rect(6.02, 46.10, 6.16, 46.15)))
    v.append(CommuneVersion("15148", "Celles", "2025-01-01", FAR_FUTURE,
                            parents=["15148"], geometry=rect(6.02, 46.10, 6.09, 46.15)))
    return v


# --------------------------------------------------------------------------
#  Sorties : PostGIS ou GeoJSON
# --------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS commune_version (
    id           bigserial PRIMARY KEY,
    code         text        NOT NULL,
    nom          text        NOT NULL,
    valid_from   date        NOT NULL,
    valid_to     date        NOT NULL,
    parents      text[]      NOT NULL DEFAULT '{}',
    children     text[]      NOT NULL DEFAULT '{}',
    geom         geometry(Geometry, 4326)
);

-- Index temporel : accélère "quelle commune à telle date"
CREATE INDEX IF NOT EXISTS idx_cv_validity  ON commune_version (valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_cv_code      ON commune_version (code);
-- Index spatial : accélère "quelle commune contient ce point"
CREATE INDEX IF NOT EXISTS idx_cv_geom      ON commune_version USING gist (geom);
"""


def to_postgis(versions: list[CommuneVersion], dsn: str) -> bool:
    try:
        import psycopg2
    except ImportError:
        print("  [!] psycopg2 non installé -> impossible d'écrire dans PostGIS.")
        return False
    try:
        conn = psycopg2.connect(dsn)
    except Exception as e:
        print(f"  [!] Connexion PostGIS impossible ({e}).")
        return False

    with conn, conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.execute("TRUNCATE commune_version RESTART IDENTITY;")
        for v in versions:
            geom_json = json.dumps(v.geometry) if v.geometry else None
            cur.execute("""
                INSERT INTO commune_version
                  (code, nom, valid_from, valid_to, parents, children, geom)
                VALUES (%s,%s,%s,%s,%s,%s,
                    CASE WHEN %s IS NULL THEN NULL
                         ELSE ST_SetSRID(ST_GeomFromGeoJSON(%s),4326) END)
            """, (v.code, v.nom, v.valid_from, v.valid_to,
                  v.parents, v.children, geom_json, geom_json))
    conn.close()
    print(f"  [ok] {len(versions)} versions écrites dans PostGIS.")
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
    print(f"  [ok] {len(versions)} versions exportées -> {out}")


# --------------------------------------------------------------------------
#  Contrôles qualité rapides
# --------------------------------------------------------------------------
def sanity_checks(versions: list[CommuneVersion]) -> None:
    print("\nContrôles :")
    # 1. pas de valid_to <= valid_from
    bad = [v for v in versions if v.valid_to <= v.valid_from]
    print(f"  périodes invalides (valid_to <= valid_from) : {len(bad)}")
    # 2. requête ponctuelle "communes actives à une date"
    for d in ["2015-06-01", "2019-06-01", "2025-06-01"]:
        active = [v for v in versions if v.valid_from <= d < v.valid_to]
        print(f"  actives au {d} : {len(active)}")


# --------------------------------------------------------------------------
#  Point d'entrée
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Ingestion COG INSEE -> PostGIS (modèle temporel)")
    ap.add_argument("--millesimes", type=int, nargs="+", default=[2020, 2023, 2025],
                    help="Années de COG à charger")
    ap.add_argument("--data-dir", type=Path, default=None,
                    help="Dossier de fichiers CSV locaux (commune_YYYY.csv, mvtcommune_YYYY.csv)")
    ap.add_argument("--network", action="store_true",
                    help="Autoriser le téléchargement depuis les URLs INSEE configurées")
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN"),
                    help="DSN PostGIS (ex: postgresql://user:pwd@localhost/chronocarte)")
    ap.add_argument("--geojson", type=Path, default=Path("communes_temporel.geojson"),
                    help="Chemin de sortie GeoJSON (fallback si pas de base)")
    args = ap.parse_args()

    print(f"Millésimes demandés : {args.millesimes}")
    versions = build_versions(args.millesimes, args.data_dir, args.network)
    print(f"Versions reconstruites : {len(versions)}")

    sanity_checks(versions)

    wrote_db = False
    if args.dsn:
        wrote_db = to_postgis(versions, args.dsn)
    if not wrote_db:
        to_geojson(versions, args.geojson)

    print("\nTerminé.")


if __name__ == "__main__":
    main()
