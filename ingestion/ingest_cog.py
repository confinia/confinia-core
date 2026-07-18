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

# Sémantique des lignes AV -> AP dont le code CHANGE (les lignes à code
# identique sont traitées à part : identité ou renommage).
#   - La version AV ne prend fin que si l'événement la fait disparaître :
#     suppression, fusions (côté absorbé), changements de code. Une création
#     (20) ou un rétablissement partiel (21) laissent la commune source vivre —
#     c'est le bug "Marseille finit en 1946" (création de Plan-de-Cuques).
#   - La version AP ne démarre que si l'événement la crée : création,
#     rétablissement, commune nouvelle, changements de code. Une fusion simple
#     ou une fusion-association ne (re)démarre pas l'absorbeur — c'est le bug
#     "Manosque démarre en 1975" (absorption de communes associées).
ENDS_AV_CROSS = {"30", "31", "32", "33", "41", "50"}
STARTS_AP_CROSS = {"20", "21", "32", "41", "50"}

FAR_FUTURE = "9999-01-01"  # convention pour "toujours valide"
COG_FLOOR = "1943-01-01"   # borne basse de l'historique INSEE des mouvements


# --------------------------------------------------------------------------
#  Modèle de données
# --------------------------------------------------------------------------
@dataclass
class CommuneVersion:
    """Une version datée d'une commune : (code, nom) valide sur [valid_from, valid_to).

    Un même (code, nom) peut produire plusieurs versions si la commune est
    rétablie après une fusion (ex. Celles 15148, morte en 2016, rétablie en 2025).
    """
    code: str
    nom: str
    valid_from: str            # ISO date
    valid_to: str              # ISO date ou FAR_FUTURE
    parents: list[str] = field(default_factory=list)   # codes dont elle est issue
    children: list[str] = field(default_factory=list)  # codes qui la remplacent
    geometry: dict | None = None                        # GeoJSON geometry (brute)
    geometry_simple: dict | None = None                 # GeoJSON geometry (simplifiée web)
    geometry_vintage: str | None = None                 # date du millésime IGN utilisé
    geometry_approx: bool = False                       # héritée d'un millésime voisin


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

    # 2. Indexer les mouvements par (code, nom) de départ et d'arrivée.
    #    DATE_EFF est la source de vérité des transitions : c'est la vraie date
    #    à laquelle "commune avant" devient "commune après".
    #    Un même (code, nom) peut commencer/finir plusieurs fois (rétablissements),
    #    d'où des ENSEMBLES de dates, transformés en périodes à l'étape 3.
    ends_at: dict[tuple[str, str], set[str]] = {}    # (code, nom) -> dates de fin
    starts_at: dict[tuple[str, str], set[str]] = {}  # (code, nom) -> dates de début
    child_links: dict[tuple[str, str], set] = {}     # (code, nom) -> {(date, code_ap)}
    parent_links: dict[tuple[str, str], set] = {}    # (code, nom) -> {(date, code_av)}

    # Passe 1 — lignes identité (même code+nom COM des deux côtés) : la commune
    # traverse l'événement. Elles ANNULENT tout début/fin croisé du même jour :
    # une commune nouvelle qui garde code et nom du chef-lieu (Osmery 2024,
    # Neufchâteau 2025…) ne doit pas voir son passé effacé par la ligne croisée
    # venant de la commune absorbée.
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
        # Seules les communes de plein exercice (TYPECOM == COM) nous concernent.
        # Une fusion produit AUSSI des lignes vers COMD/COMA (communes déléguées/
        # associées) portant le même (code, nom) que la commune disparue — sans ce
        # filtre, elles écrasent les dates de la vraie version (cas 01033 Bellegarde).
        av_is_com = (m.get("TYPECOM_AV") or "COM").strip() == "COM"
        ap_is_com = (m.get("TYPECOM_AP") or "COM").strip() == "COM"
        code_av = (m.get("COM_AV") or "").strip() if av_is_com else ""
        nom_av = (m.get("LIBELLE_AV") or m.get("NCCENR_AV") or "").strip()
        code_ap = (m.get("COM_AP") or "").strip() if ap_is_com else ""
        nom_ap = (m.get("LIBELLE_AP") or m.get("NCCENR_AP") or "").strip()
        # Ligne identité : la commune traverse l'événement inchangée (ex. la
        # commune absorbante d'une fusion-association, MOD 33/34). Elle ne
        # commence ni ne finit ici — ignorer, sinon on la tue à cette date.
        if code_av and code_av == code_ap and nom_av == nom_ap:
            continue
        mod = (m.get("MOD") or "").strip()
        if code_av and code_av == code_ap:
            # Même code, nom différent : renommage (quel que soit le MOD) —
            # l'ancienne version finit, la nouvelle commence.
            if nom_av:
                ends_at.setdefault((code_av, nom_av), set()).add(d)
            if nom_ap:
                starts_at.setdefault((code_ap, nom_ap), set()).add(d)
        else:
            # Code différent : la sémantique dépend du type d'événement — et une
            # ligne identité du même jour l'emporte (la commune survit).
            if (code_av and nom_av and mod in ENDS_AV_CROSS
                    and (code_av, nom_av, d) not in identity):
                ends_at.setdefault((code_av, nom_av), set()).add(d)
            if (code_ap and nom_ap and mod in STARTS_AP_CROSS
                    and (code_ap, nom_ap, d) not in identity):
                starts_at.setdefault((code_ap, nom_ap), set()).add(d)
        # liens parent/enfant datés, en ignorant les auto-références (même code+nom)
        if code_av and code_ap and (code_av, nom_av) != (code_ap, nom_ap):
            child_links.setdefault((code_av, nom_av), set()).add((d, code_ap))
            parent_links.setdefault((code_ap, nom_ap), set()).add((d, code_av))

    # 3. Construire les périodes de chaque (code, nom) rencontré dans les
    #    snapshots OU les mouvements. Le fichier des mouvements est complet
    #    depuis 1943 : un (code, nom) sans événement entrant existe donc depuis
    #    (au moins) COG_FLOOR — c'est ce qui rend les comptages corrects à
    #    n'importe quelle date, même avec un seul millésime COG chargé.
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
        # Premier événement = une FIN seule : la version existait avant nos
        # données — début inconnu, borné à COG_FLOOR.
        if dates and dates[0] in E and dates[0] not in S:
            open_from = COG_FLOOR
        for d in dates:
            has_s, has_e = d in S, d in E
            if open_from is not None:
                if has_e:
                    if d > open_from:
                        periods.append((open_from, d))
                    # fin + re-début le même jour = continuité (aller-retour de nom)
                    open_from = d if has_s else None
                # début seul alors que déjà ouvert : on garde le plus ancien
            else:
                if has_s and has_e:
                    # début + fin le même jour sans passé : existence de durée
                    # nulle, artefact de transition (Freigné 44225, Pont-Farcy
                    # 50649 : changement de département + fusion simultanés).
                    pass
                elif has_s:
                    open_from = d
                # fin seule sans période ouverte : anomalie, ignorée
        if open_from is not None:
            periods.append((open_from, FAR_FUTURE))
        if not dates:
            # présent dans un snapshot, aucun événement : existe depuis toujours
            periods.append((COG_FLOOR, FAR_FUTURE))

        for vf, vt in periods:
            # Liens datés, rattachés à la période qu'ils concernent : un parent
            # explique le début de la période OU une absorption en cours de vie
            # (ex. Coupy -> Bellegarde en 1971, sans fin de version) ; un enfant,
            # une sortie en cours de vie (création détachée) OU la fin.
            parents = sorted({c for d, c in parent_links.get(k, ()) if vf <= d < vt})
            children = sorted({c for d, c in child_links.get(k, ()) if vf < d <= vt})
            versions.append(CommuneVersion(
                code=code, nom=nom, valid_from=vf, valid_to=vt,
                parents=parents, children=children,
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

DROP MATERIALIZED VIEW IF EXISTS departement_geom;
DROP TABLE IF EXISTS commune_version CASCADE;
-- Table générale des unités administratives temporelles (Step 5) : les
-- communes FR et les régions NUTS partagent le même modèle. Le nom
-- commune_version est historique — renommage en admin_unit_version envisagé
-- au durcissement pré-beta.
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
    geom             geometry(Geometry, 4326),   -- brute (source de vérité, requêtes spatiales)
    geom_simple      geometry(Geometry, 4326)    -- simplifiée ~50 m (servie au web)
);
CREATE INDEX idx_cv_type_country  ON commune_version (unit_type, country);

-- Index temporel : accélère "quelles communes à telle date"
CREATE INDEX idx_cv_validity      ON commune_version (valid_from, valid_to);
-- Index contrat API : "ce code à telle date" (TODO Step 2)
CREATE INDEX idx_cv_code_validity ON commune_version (code, valid_from, valid_to);
-- Index spatiaux : "quelle commune contient ce point"
CREATE INDEX idx_cv_geom          ON commune_version USING gist (geom);
CREATE INDEX idx_cv_geom_simple   ON commune_version USING gist (geom_simple);
"""

# Contours départementaux (couche de navigation de la démo / API) : union des
# communes actuelles par département, matérialisée en fin de chargement.
DEPT_GEOM_SQL = """
DROP MATERIALIZED VIEW IF EXISTS departement_geom;
CREATE MATERIALIZED VIEW departement_geom AS
SELECT CASE WHEN code LIKE '97%' THEN left(code, 3) ELSE left(code, 2) END AS dept,
       ST_Multi(ST_Union(geom_simple)) AS geom
FROM commune_version
WHERE valid_to = '9999-01-01' AND geom_simple IS NOT NULL
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
        print("  [!] psycopg2 non installé -> impossible d'écrire dans PostGIS.")
        return False
    try:
        conn = psycopg2.connect(dsn)
    except Exception as e:
        print(f"  [!] Connexion PostGIS impossible ({e}).")
        return False

    with conn, conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        execute_batch(cur, INSERT_SQL, [version_row(v) for v in versions], page_size=200)
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
    # 2. comptages vs chiffres publiés INSEE (France métro + DROM, au 1er janvier)
    published = {"2015-01-02": 36658, "2020-01-02": 34968, "2025-01-02": 34875}
    for d, expected in published.items():
        active = [v for v in versions if v.valid_from <= d < v.valid_to]
        print(f"  actives au {d} : {len(active)} (INSEE publié : {expected})")


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
