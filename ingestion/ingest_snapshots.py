#!/usr/bin/env python3
"""
Moteur temporel générique par diff de snapshots annuels (DE, NL…).

Contrairement à la France (fichier de mouvements INSEE complet depuis 1943,
transitions à la DATE_EFF exacte), la v1 des autres pays reconstruit les
périodes à partir d'éditions annuelles au 1er janvier :

  - un (code, nom) présent dans des éditions consécutives = une période
    [date_première_édition, date_suivant_la_dernière) ;
  - renommage (même code, autre nom) ou disparition = fin de période ;
  - réapparition = nouvelle période (le multi-périodes est natif).

**Approximation documentée** : les transitions tombent aux dates d'édition
(1er janvier). Aux Pays-Bas, les herindelingen prennent effet au 1er janvier —
quasi exact. En Allemagne, les Gebietsänderungen en cours d'année sont
rabattues sur l'édition suivante ; les fichiers Destatis affineront plus tard.

parents/children : vides en v1 (pas de généalogie dans les snapshots).
"""
from __future__ import annotations

FAR_FUTURE = "9999-01-01"

SNAPSHOT_INSERT = """
    INSERT INTO commune_version
      (code, nom, unit_type, country, valid_from, valid_to, parents, children,
       geometry_vintage, geometry_approx, geom, geom_simple)
    VALUES (%s,%s,%s,%s,%s,%s,'{}','{}',%s,%s,
        CASE WHEN %s IS NULL THEN NULL ELSE ST_SetSRID(ST_GeomFromGeoJSON(%s),4326) END,
        CASE WHEN %s IS NULL THEN NULL ELSE ST_SetSRID(ST_GeomFromGeoJSON(%s),4326) END)
"""


def build_periods(dates: list[str], snapshots: dict[str, dict]) -> list[dict]:
    """snapshots : {date: {code: (nom, shapely_geom)}} -> périodes temporelles.

    Retourne des dicts {code, nom, valid_from, valid_to, geom, vintage}, la
    géométrie étant celle de la dernière édition de la période.
    """
    codes = sorted({c for s in snapshots.values() for c in s})
    periods = []
    for code in codes:
        run: list[str] = []
        for d in dates:
            here = snapshots[d].get(code)
            prev = snapshots[run[-1]][code] if run else None
            if here and (not run or here[0] == prev[0]):     # même nom -> continuité
                run.append(d)
            else:
                if run:
                    periods.append(_period(code, run, dates, snapshots))
                run = [d] if here else []
        if run:
            periods.append(_period(code, run, dates, snapshots))
    return periods


def _period(code: str, run: list[str], dates: list[str], snapshots: dict) -> dict:
    nom, geom = snapshots[run[-1]][code]
    nxt = dates.index(run[-1]) + 1
    return {
        "code": code, "nom": nom,
        "valid_from": run[0],
        "valid_to": dates[nxt] if nxt < len(dates) else FAR_FUTURE,
        "geom": geom, "vintage": run[-1],
    }


def load_postgis(periods: list[dict], unit_type: str, country: str,
                 dsn: str, simplify_tol: float = 0.0005) -> None:
    import json
    import psycopg2
    from psycopg2.extras import execute_batch
    from shapely.geometry import mapping

    conn = psycopg2.connect(dsn)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM commune_version WHERE unit_type = %s AND country = %s",
                    (unit_type, country))
        batch, total = [], 0
        for p in periods:
            raw = simple = None
            if p["geom"] is not None:
                raw = json.dumps(mapping(p["geom"]))
                simple = json.dumps(mapping(
                    p["geom"].simplify(simplify_tol, preserve_topology=True)))
            batch.append((p["code"], p["nom"], unit_type, country,
                          p["valid_from"], p["valid_to"], p["vintage"], False,
                          raw, raw, simple, simple))
            if len(batch) >= 200:
                execute_batch(cur, SNAPSHOT_INSERT, batch, page_size=50)
                total += len(batch)
                batch = []
                if total % 2000 < 200:
                    print(f"  ... {total} périodes chargées")
        if batch:
            execute_batch(cur, SNAPSHOT_INSERT, batch, page_size=50)
            total += len(batch)
    conn.close()
    print(f"  [ok] {total} périodes {unit_type}/{country} écrites dans PostGIS.")


def sanity(periods: list[dict], dates: list[str], label: str) -> None:
    print(f"\nContrôles {label} :")
    for d in dates:
        probe = d[:4] + "-06-01"
        n = sum(1 for p in periods if p["valid_from"] <= probe < p["valid_to"])
        print(f"  actives au {probe} : {n}")
