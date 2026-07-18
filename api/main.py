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
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

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


# API publique en lecture seule : CORS ouvert (la démo tourne sur GitHub Pages).
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["GET"], allow_headers=["*"])


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

LANDING = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Confinia API</title><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: dark; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#10151d; color:#e8eaed; font:16px/1.6 system-ui,-apple-system,sans-serif; }
  main { max-width:44rem; padding:2rem; }
  h1 { font-size:1.6rem; margin:0 0 .3rem; } h1 span { color:#7ab8ff; }
  p.tag { margin:0 0 1.4rem; opacity:.85; }
  pre { background:#0b0f16; border:1px solid #26314a; border-radius:8px;
        padding:.9rem 1rem; overflow-x:auto; font-size:.85rem; }
  a { color:#7ab8ff; text-decoration:none; } a:hover { text-decoration:underline; }
  ul { padding-left:1.2rem; } footer { margin-top:1.6rem; font-size:.8rem; opacity:.7; }
</style></head><body><main>
<h1><span>Confinia</span> API</h1>
<p class="tag">EU administrative boundaries with full historical versioning —
any commune, as it existed at any date, as GeoJSON.</p>
<p class="tag">Typical uses: joining a 2015 dataset (health, tax, elections…) to today's map
without losing the ~1,800 communes that merged since; resolving which commune an address
or GPS point belonged to <em>at the time of the event</em> (insurance claims, property
history, epidemiology); keeping INSEE-coded time series consistent across COG vintages
when codes get reused or renamed.</p>
<pre>« Which commune was here on 2018-06-01? » — codes get reused, names change, communes merge:

GET <a href="/v1/communes?code=01033&amp;at=2018-06-01">/v1/communes?code=01033&amp;at=2018-06-01</a>   → Bellegarde-sur-Valserine
GET <a href="/v1/communes?code=01033&amp;at=2020-06-01">/v1/communes?code=01033&amp;at=2020-06-01</a>   → Valserhône (merged 2019)
GET <a href="/v1/communes/01033/history">/v1/communes/01033/history</a>            → every version since 1943
GET <a href="/v1/communes?lat=46.11&amp;lon=5.83&amp;at=2015-06-01">/v1/communes?lat=46.11&amp;lon=5.83&amp;at=2015-06-01</a>  → point-in-polygon
GET <a href="/v1/communes?dept=01&amp;at=2019-06-01">/v1/communes?dept=01&amp;at=2019-06-01</a>   → a whole département (FeatureCollection)</pre>
<ul>
<li><a href="/docs">Interactive documentation (OpenAPI)</a></li>
<li><a href="/healthz">Service health</a></li>
</ul>
<footer>France first (INSEE COG + IGN Admin Express, Licence Ouverte 2.0 —
attribution « IGN — Admin Express »), DE/NL and Eurostat NUTS next.
Early development — no authentication yet, be gentle.</footer>
</main></body></html>"""


@app.get("/", include_in_schema=False)
def landing():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(LANDING)


@app.get("/healthz")
def healthz():
    with cursor() as cur:
        cur.execute("SELECT count(*) FROM commune_version")
        return {"status": "ok", "versions": cur.fetchone()[0]}


@app.get("/v1/communes")
def commune_at(
    response: Response,
    at: date = Query(..., description="Date de validité (YYYY-MM-DD)"),
    code: str | None = Query(None, min_length=5, max_length=5,
                             description="Code INSEE (ex: 01033)"),
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    dept: str | None = Query(None, min_length=2, max_length=3,
                             pattern=r"^[0-9][0-9AB][0-9]?$",
                             description="Département (ex: 01) → FeatureCollection"),
):
    """Commune(s) valide(s) à la date donnée : par code INSEE, par point (lat/lon),
    ou toutes celles d'un département (FeatureCollection, géométrie simplifiée)."""
    selectors = (code is not None) + (lat is not None and lon is not None) + (dept is not None)
    if selectors != 1:
        raise HTTPException(422, "Fournir exactement un critère : code=, lat=&lon=, ou dept=.")
    with cursor() as cur:
        if dept:
            cur.execute(
                f"SELECT {COLS} FROM commune_version "
                "WHERE unit_type = 'commune' AND code LIKE %s "
                "AND valid_from <= %s AND valid_to > %s "
                "ORDER BY code", (dept + "%", at, at))
            rows = cur.fetchall()
            # L'état d'un département à une date passée ne change plus : cacheable.
            response.headers["Cache-Control"] = "public, max-age=3600"
            return {"type": "FeatureCollection", "features": [feature(r) for r in rows]}
        if code:
            cur.execute(
                f"SELECT {COLS} FROM commune_version "
                "WHERE unit_type = 'commune' AND code = %s "
                "AND valid_from <= %s AND valid_to > %s "
                "ORDER BY valid_from DESC LIMIT 1", (code, at, at))
        else:
            cur.execute(
                f"SELECT {COLS} FROM commune_version "
                "WHERE unit_type = 'commune' "
                "AND valid_from <= %s AND valid_to > %s AND geom IS NOT NULL "
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
            "FROM commune_version WHERE unit_type = 'commune' AND code = %s "
            "ORDER BY valid_from", (code,))
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"Code INSEE inconnu : {code}")
    return {"code": code, "versions": [feature(r) for r in rows]}


@app.get("/v1/departements")
def departements(response: Response):
    """Contours départementaux actuels (couche de navigation, union des communes)."""
    with cursor() as cur:
        cur.execute("SELECT dept, ST_AsGeoJSON(geom, 5) FROM departement_geom ORDER BY dept")
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(503, "Contours non matérialisés — relancer le chargement.")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": json.loads(g), "properties": {"dept": d}}
        for d, g in rows]}


@app.get("/v1/nuts")
def nuts_at(
    response: Response,
    at: date = Query(..., description="Date de validité (YYYY-MM-DD)"),
    code: str | None = Query(None, min_length=2, max_length=5,
                             description="Code NUTS (ex: FR101)"),
    level: int | None = Query(None, ge=0, le=3),
    country: str | None = Query(None, min_length=2, max_length=2,
                                description="Code pays (ex: FR, DE, NL)"),
):
    """Région(s) NUTS valide(s) à la date donnée : par code, ou par niveau (+ pays)."""
    if (code is None) == (level is None):
        raise HTTPException(422, "Fournir soit code=, soit level= (avec country= optionnel).")
    with cursor() as cur:
        if code:
            cur.execute(
                f"SELECT {COLS} FROM commune_version "
                "WHERE unit_type LIKE 'nuts%%' AND code = %s "
                "AND valid_from <= %s AND valid_to > %s "
                "ORDER BY valid_from DESC LIMIT 1", (code.upper(), at, at))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Aucune région NUTS valide à cette date pour ce code.")
            return feature(row)
        sql = (f"SELECT {COLS} FROM commune_version "
               "WHERE unit_type = %s AND valid_from <= %s AND valid_to > %s ")
        params = [f"nuts{level}", at, at]
        if country:
            sql += "AND country = %s "
            params.append(country.upper())
        cur.execute(sql + "ORDER BY code", params)
        rows = cur.fetchall()
    response.headers["Cache-Control"] = "public, max-age=3600"
    return {"type": "FeatureCollection", "features": [feature(r) for r in rows]}


@app.get("/v1/nuts/{code}/history")
def nuts_history(code: str, geometry: bool = Query(False)):
    """Toutes les versions d'un code NUTS."""
    geom_col = "ST_AsGeoJSON(geom_simple, 6)" if geometry else "NULL"
    with cursor() as cur:
        cur.execute(
            "SELECT code, nom, valid_from, valid_to, parents, children, "
            f"geometry_vintage, geometry_approx, {geom_col} "
            "FROM commune_version WHERE unit_type LIKE 'nuts%%' AND code = %s "
            "ORDER BY valid_from", (code.upper(),))
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"Code NUTS inconnu : {code}")
    return {"code": code.upper(), "versions": [feature(r) for r in rows]}
