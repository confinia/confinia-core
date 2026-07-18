#!/usr/bin/env python3
"""
Confinia API — communes à date (modèle temporel valid_from / valid_to).

Les deux endpoints du contrat (TODO Step 3) :
  GET /v1/communes?at=YYYY-MM-DD&code=XXXXX      -> Feature GeoJSON à cette date
  GET /v1/communes?at=YYYY-MM-DD&lat=..&lon=..   -> variante point-dans-polygone
  GET /v1/communes/{code}/history                -> toutes les versions + liens

Données : table commune_version chargée par ingestion/join_geometry.py --dsn.
La géométrie servie est la simplifiée (~50 m) ; le point-dans-polygone
s'appuie sur la brute (exacte, index GIST).
"""
from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import date

import psycopg2
import psycopg2.pool
from fastapi import FastAPI, HTTPException, Query, Request

FAR_FUTURE = date(9999, 1, 1)
DSN = os.environ.get("PG_DSN", "postgresql://confinia:<dev-password-rotated>@db:5432/confinia")

pool: psycopg2.pool.SimpleConnectionPool | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pool
    last_err = None
    for _attempt in range(30):                     # la base peut démarrer après nous
        try:
            pool = psycopg2.pool.SimpleConnectionPool(1, 8, DSN)
            break
        except psycopg2.OperationalError as e:
            last_err = e
            time.sleep(2)
    if pool is None:
        raise RuntimeError(f"PostGIS injoignable : {last_err}")
    yield
    pool.closeall()


app = FastAPI(
    title="Confinia API",
    version="0.1.0",
    description="EU administrative boundaries with full historical versioning. "
                "Data: INSEE COG + IGN Admin Express (Licence Ouverte 2.0).",
    lifespan=lifespan,
)


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Response-Time-Ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
    return response


@contextmanager
def cursor():
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        pool.putconn(conn)


def feature(row) -> dict:
    (code, nom, valid_from, valid_to, parents, children,
     vintage, approx, geom) = row
    return {
        "type": "Feature",
        "geometry": json.loads(geom) if geom else None,
        "properties": {
            "code": code, "nom": nom,
            "valid_from": valid_from.isoformat(),
            "valid_to": None if valid_to == FAR_FUTURE else valid_to.isoformat(),
            "parents": parents, "children": children,
            "geometry_vintage": vintage.isoformat() if vintage else None,
            "geometry_approx": approx,
        },
    }


COLS = ("code, nom, valid_from, valid_to, parents, children, "
        "geometry_vintage, geometry_approx, ST_AsGeoJSON(geom_simple, 6)")


@app.get("/healthz")
def healthz():
    with cursor() as cur:
        cur.execute("SELECT count(*) FROM commune_version")
        return {"status": "ok", "versions": cur.fetchone()[0]}


@app.get("/v1/communes")
def commune_at(
    at: date = Query(..., description="Date de validité (YYYY-MM-DD)"),
    code: str | None = Query(None, min_length=5, max_length=5,
                             description="Code INSEE (ex: 01033)"),
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
):
    """La commune valide à la date donnée, par code INSEE ou par point (lat/lon)."""
    if (code is None) == (lat is None or lon is None):
        raise HTTPException(422, "Fournir soit code=, soit lat= et lon=.")
    with cursor() as cur:
        if code:
            cur.execute(
                f"SELECT {COLS} FROM commune_version "
                "WHERE code = %s AND valid_from <= %s AND valid_to > %s "
                "ORDER BY valid_from DESC LIMIT 1", (code, at, at))
        else:
            cur.execute(
                f"SELECT {COLS} FROM commune_version "
                "WHERE valid_from <= %s AND valid_to > %s AND geom IS NOT NULL "
                "AND ST_Contains(geom, ST_SetSRID(ST_Point(%s, %s), 4326)) "
                "LIMIT 1", (at, at, lon, lat))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Aucune commune valide à cette date pour cette requête.")
    return feature(row)


@app.get("/v1/communes/{code}/history")
def commune_history(code: str, geometry: bool = Query(False)):
    """Toutes les versions d'un code INSEE, avec liens parents/enfants."""
    geom_col = "ST_AsGeoJSON(geom_simple, 6)" if geometry else "NULL"
    with cursor() as cur:
        cur.execute(
            "SELECT code, nom, valid_from, valid_to, parents, children, "
            f"geometry_vintage, geometry_approx, {geom_col} "
            "FROM commune_version WHERE code = %s ORDER BY valid_from", (code,))
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"Code INSEE inconnu : {code}")
    return {"code": code, "versions": [feature(r) for r in rows]}
