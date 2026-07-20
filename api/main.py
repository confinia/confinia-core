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

import hashlib
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
DSN = os.environ["PG_DSN"]     # fourni par deploy/secrets.env (gitignoré) via compose

pool: psycopg2.pool.SimpleConnectionPool | None = None


KEYS_SQL = """
CREATE TABLE IF NOT EXISTS api_key (
    key        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email      text NOT NULL,
    note       text,
    created_at timestamptz NOT NULL DEFAULT now(),
    active     boolean NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS api_usage (
    key      uuid NOT NULL REFERENCES api_key(key),
    day      date NOT NULL,
    requests bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (key, day)
);
-- Visiteurs uniques par jour/pays. Jamais d'IP : client_hash est un condensé
-- salé (secret d'env + jour UTC), irréversible et illisible d'un jour à l'autre.
-- UNLOGGED : donnée d'observabilité, perdable sans regret. Purge à 45 jours.
CREATE UNLOGGED TABLE IF NOT EXISTS visitor_daily (
    day         date  NOT NULL,
    country     text  NOT NULL,
    client_hash bytea NOT NULL,
    PRIMARY KEY (day, client_hash)
);
DELETE FROM visitor_daily WHERE day < CURRENT_DATE - 45;
"""

# Clés facultatives pendant le développement ; passer REQUIRE_API_KEY=true
# à l'ouverture de la beta (plan 2.3 : metering dès le premier jour).
REQUIRE_KEY = os.environ.get("REQUIRE_API_KEY", "false").lower() == "true"
OPEN_PATHS = ("/", "/docs", "/openapi.json", "/redoc", "/healthz", "/v1/keys")


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
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(KEYS_SQL)
    finally:
        pool.putconn(conn)
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

# ---------------------------------------------------------------------------
#  Observabilité (Step 5b) : métriques OpenTelemetry -> collector -> Prometheus
#  -> Grafana. Pays d'appel via GeoIP (DB-IP Country Lite, CC BY 4.0) sur IP
#  anonymisée — on ne stocke jamais l'IP, seulement le code pays.
# ---------------------------------------------------------------------------
REQ_COUNTER = None
FE_COUNTER = None            # événements UI de la démo (frontend), via /beacon
OTLP = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")   # ex: http://otel-collector:4318
if OTLP:
    try:
        from opentelemetry import metrics as otel_metrics
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.resources import Resource
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{OTLP}/v1/metrics"),
            export_interval_millis=15000)
        otel_metrics.set_meter_provider(MeterProvider(
            resource=Resource.create({"service.name": "confinia-api"}),
            metric_readers=[reader]))
        REQ_COUNTER = otel_metrics.get_meter("confinia").create_counter(
            "confinia.requests", description="Requêtes API par route/statut/pays")
        FE_COUNTER = otel_metrics.get_meter("confinia").create_counter(
            "confinia.frontend.events", description="Événements UI de la démo (frontend)")
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
        FastAPIInstrumentor.instrument_app(app)
        Psycopg2Instrumentor().instrument()
    except Exception as e:                      # l'observabilité ne casse jamais l'API
        print(f"[obs] OpenTelemetry non initialisé : {e}")

GEOIP = None
try:
    import maxminddb
    GEOIP = maxminddb.open_database("/geoip/dbip-country-lite.mmdb")
except Exception:
    pass


def client_country(request: Request) -> str:
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "")
    if not GEOIP or not ip:
        return "??"
    try:
        rec = GEOIP.get(ip)
        return (rec or {}).get("country", {}).get("iso_code", "??")
    except Exception:
        return "??"


def client_kind(request: Request) -> str:
    """D'où vient l'appel : démo (time-slider), site vitrine, ou API directe.
    Déduit de Origin/Referer — cardinalité bornée, aucune donnée personnelle."""
    ref = request.headers.get("origin") or request.headers.get("referer") or ""
    if "confinia.github.io" in ref or "time-slider.confinia.io" in ref:
        return "demo"
    if "confinia.io" in ref:            # landing/blog (après le test démo ci-dessus)
        return "site"
    return "direct"


# ---------------------------------------------------------------------------
#  Visiteurs uniques par jour et par pays. La posture GDPR tient : on ne
#  stocke jamais l'IP. Elle est réduite à un condensé salé (secret d'env +
#  jour UTC), donc irréversible sans le secret et non corrélable entre jours.
#  Le cache mémoire par worker évite un INSERT par requête ; la table fait
#  l'exactitude inter-workers (comme api_usage pour le metering).
# ---------------------------------------------------------------------------
VISITOR_SECRET = os.environ.get("VISITOR_SALT_SECRET", "")
_seen_today: set[bytes] = set()
_seen_day = ""


def note_visitor(ip: str, country: str) -> None:
    global _seen_day
    if not ip or not VISITOR_SECRET or pool is None:
        return
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if day != _seen_day:
        _seen_day = day
        _seen_today.clear()
    h = hashlib.sha256(f"{VISITOR_SECRET}|{day}|{ip}".encode()).digest()[:16]
    if h in _seen_today:
        return
    if len(_seen_today) < 200_000:              # borne mémoire par worker
        _seen_today.add(h)
    try:
        conn = pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO visitor_daily (day, country, client_hash) "
                    "VALUES (CURRENT_DATE, %s, %s) ON CONFLICT DO NOTHING",
                    (country, h))
        finally:
            pool.putconn(conn)
    except Exception:
        pass                                    # fail-open : jamais bloquant


# ---------------------------------------------------------------------------
#  Observatoire 404 : les chemins sondés par les scanners deviennent des labels
#  Prometheus (panneau « Sécurité » Grafana), qu'on verse ensuite dans le filtre
#  Caddy. Garde-fou de cardinalité : au-delà de 300 chemins distincts par jour,
#  tout part dans « (flood) » pour ne pas noyer Prometheus.
# ---------------------------------------------------------------------------
_paths_404: set[str] = set()
_paths_404_day = ""


def label_404(path: str) -> str:
    global _paths_404_day
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if day != _paths_404_day:
        _paths_404_day = day
        _paths_404.clear()
    if path in _paths_404 or len(_paths_404) < 300:
        _paths_404.add(path)
        return path
    return "(flood)"


# ---------------------------------------------------------------------------
#  Limitation de débit (Step 6) : par IP, en mémoire, deux fenêtres fixes.
#  Généreuse pour un usage normal, bloque les rafales de scraping — par worker
#  uvicorn (2 workers => limites effectives ~doublées, assumé).
# ---------------------------------------------------------------------------
RATE_PER_SEC, RATE_PER_MIN = 20, 400
_rate: dict[str, list] = {}          # ip -> [sec_window, sec_n, min_window, min_n]


def rate_limited(ip: str) -> bool:
    now = int(time.time())
    if len(_rate) > 20000:            # borne mémoire : purge les fenêtres mortes
        for k in [k for k, v in _rate.items() if v[2] < now - 60]:
            del _rate[k]
    w = _rate.setdefault(ip, [now, 0, now - now % 60, 0])
    if w[0] != now:
        w[0], w[1] = now, 0
    m = now - now % 60
    if w[2] != m:
        w[2], w[3] = m, 0
    w[1] += 1
    w[3] += 1
    return w[1] > RATE_PER_SEC or w[3] > RATE_PER_MIN


def meter_key(request: Request) -> str | None:
    """Valide la clé API éventuelle et compte l'usage du jour. Fail-open."""
    key = request.headers.get("x-api-key") or request.query_params.get("api_key")
    if not key:
        return None
    try:
        conn = pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("SELECT active FROM api_key WHERE key = %s::uuid", (key,))
                row = cur.fetchone()
                if not row or not row[0]:
                    return None
                cur.execute(
                    "INSERT INTO api_usage (key, day, requests) VALUES (%s::uuid, CURRENT_DATE, 1) "
                    "ON CONFLICT (key, day) DO UPDATE SET requests = api_usage.requests + 1", (key,))
                return key
        finally:
            pool.putconn(conn)
    except Exception:
        return None


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "")
    # Trafic interne (VM, réseau compose) non limité — le public passe par caddy
    # et arrive avec son IP réelle en X-Forwarded-For.
    internal = ip.startswith(("10.", "127.", "192.168.")) or not ip
    if not internal and request.url.path.startswith("/v1/") and rate_limited(ip):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            {"detail": f"Trop de requêtes (limites : {RATE_PER_SEC}/s, {RATE_PER_MIN}/min). "
                       "Besoin de plus ? contact@confinia.io"},
            status_code=429, headers={"Retry-After": "10"})
    valid_key = meter_key(request) if request.url.path.startswith("/v1/") else None
    if (REQUIRE_KEY and valid_key is None
            and request.url.path.startswith("/v1/")
            and not request.url.path.startswith("/v1/keys")):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Clé API requise : POST /v1/keys {email} "
                                       "puis en-tête X-API-Key."}, status_code=401)
    response = await call_next(request)
    response.headers["X-Response-Time-Ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
    country = client_country(request)
    if not internal:
        note_visitor(ip, country)
    if REQ_COUNTER is not None:
        route = request.scope.get("route")
        REQ_COUNTER.add(1, {
            "route": getattr(route, "path", None) or label_404(request.url.path),
            "method": request.method,
            "status": str(response.status_code),
            "country": country,
            "client": client_kind(request),
            "keyed": valid_key is not None,
        })
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
    (code, nom, unit_type, country, valid_from, valid_to, parents, children,
     vintage, approx, geom) = row
    return {
        "type": "Feature",
        "geometry": json.loads(geom) if geom else None,
        "properties": {
            "code": code, "nom": nom,
            "unit_type": unit_type, "country": country,
            "valid_from": valid_from.isoformat(),
            "valid_to": None if valid_to == FAR_FUTURE else valid_to.isoformat(),
            "parents": parents, "children": children,
            "geometry_vintage": vintage.isoformat() if vintage else None,
            "geometry_approx": approx,
        },
    }


COLS = ("code, nom, unit_type, country, valid_from, valid_to, parents, children, "
        "geometry_vintage, geometry_approx, ST_AsGeoJSON(geom_simple, 6)")


def hist_cols(geometry: bool) -> str:
    """Colonnes des endpoints /history — même contrat que COLS, géométrie optionnelle."""
    return COLS if geometry else COLS.replace("ST_AsGeoJSON(geom_simple, 6)", "NULL")


def derive_events(versions: list[dict]) -> list[dict]:
    """Chronologie des ÉVÉNEMENTS d'une unité, dérivée de ses versions :
    renommages (avec les deux noms), fusions/absorptions, scissions, création,
    disparition — chacun daté quand la date est connue."""
    vs = [v["properties"] for v in versions]
    if not vs:
        return []
    code = vs[0]["code"]
    events: list[dict] = []
    for i, p in enumerate(vs):
        prev = vs[i - 1] if i > 0 else None
        contiguous = prev is not None and prev["valid_to"] == p["valid_from"]
        other_parents = sorted({c for c in (p["parents"] or []) if c != code})
        if contiguous and prev["nom"] != p["nom"]:
            events.append({"date": p["valid_from"], "type": "renamed",
                           "detail": f"{prev['nom']} → {p['nom']}"})
            if other_parents:
                events.append({"date": None, "type": "absorbed",
                               "detail": f"absorbed {', '.join(other_parents)} between "
                                         f"{p['valid_from']} and {p['valid_to'] or 'today'}"})
        elif other_parents and p["valid_from"] != "1943-01-01":
            events.append({"date": p["valid_from"],
                           "type": "merger" if code in (p["parents"] or []) or len(other_parents) > 1
                                   else "created",
                           "detail": f"formed from {', '.join(sorted(set(p['parents'])))}"})
        elif other_parents:
            events.append({"date": None, "type": "absorbed",
                           "detail": f"absorbed {', '.join(other_parents)} between "
                                     f"{p['valid_from']} and {p['valid_to'] or 'today'}"})
        elif not contiguous and i > 0:
            events.append({"date": p["valid_from"], "type": "reestablished",
                           "detail": f"re-established as {p['nom']}"})
        if p["valid_to"]:
            nxt = vs[i + 1] if i + 1 < len(vs) else None
            internal = nxt is not None and nxt["valid_from"] == p["valid_to"]
            children = sorted(set(p["children"] or []))
            others = [c for c in children if c != code]
            if internal:
                pass                                   # transition couverte au tour suivant
            elif len(children) > 1:
                events.append({"date": p["valid_to"], "type": "split",
                               "detail": f"split into {', '.join(children)}"})
            elif others:
                events.append({"date": p["valid_to"], "type": "merged_into",
                               "detail": f"merged into {others[0]}"})
            else:
                events.append({"date": p["valid_to"], "type": "ended",
                               "detail": "no longer listed (no successor recorded)"})
    return events

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
<pre>« Which commune was here on 2018-06-01? » — codes get reused, names change, municipalities merge:

GET <a href="/v1/communes?code=01033&amp;at=2018-06-01">/v1/communes?code=01033&amp;at=2018-06-01</a>   → Bellegarde-sur-Valserine
GET <a href="/v1/communes?code=01033&amp;at=2020-06-01">/v1/communes?code=01033&amp;at=2020-06-01</a>   → Valserhône (merged 2019)
GET <a href="/v1/communes/01033/history">/v1/communes/01033/history</a>            → every version since 1943
GET <a href="/v1/communes?dept=01&amp;at=2019-06-01">/v1/communes?dept=01&amp;at=2019-06-01</a>   → a whole département (FeatureCollection)

Any European municipality — 36 countries (EU-27 + EFTA + UK + candidates):

GET <a href="/v1/units?lat=52.52&amp;lon=13.405&amp;at=2020-06-01">/v1/units?lat=52.52&amp;lon=13.405&amp;at=2020-06-01</a>  → Berlin (point, any country)
GET <a href="/v1/units?nuts=ITC4C&amp;at=2020-06-01">/v1/units?nuts=ITC4C&amp;at=2020-06-01</a>    → all comuni of the Milano province
GET <a href="/v1/units?nuts=DE2&amp;at=2019-06-01">/v1/units?nuts=DE2&amp;at=2019-06-01</a>      → all Gemeinden of Bavaria
GET <a href="/v1/units/GM0363/history">/v1/units/GM0363/history</a>              → Amsterdam through time

NUTS regions, 7 versions (2003→2024) — e.g. the 2016 French région reform
(the 13 new régions became NUTS 1; the 22 old ones survive as NUTS 2 — the
kind of divergence that silently breaks time series):

GET <a href="/v1/nuts?level=1&amp;country=FR&amp;at=2015-06-01">/v1/nuts?level=1&amp;country=FR&amp;at=2015-06-01</a>  → 9 ZEAT super-regions (pre-reform NUTS 1)
GET <a href="/v1/nuts?level=1&amp;country=FR&amp;at=2018-06-01">/v1/nuts?level=1&amp;country=FR&amp;at=2018-06-01</a>  → the 13 post-2016 régions (+ DROM)
GET <a href="/v1/nuts?lat=45.46&amp;lon=9.19&amp;level=3&amp;at=2020-06-01">/v1/nuts?lat=45.46&amp;lon=9.19&amp;level=3&amp;at=2020-06-01</a>  → which province am I in?</pre>
<ul>
<li><a href="https://time-slider.confinia.io">Live demo — boundaries through time (MapLibre)</a></li>
<li><a href="/docs">Interactive documentation (OpenAPI)</a></li>
<li><a href="/healthz">Service health</a></li>
</ul>
<footer>168k historical versions · France at exact INSEE event dates since 1943,
Germany &amp; Netherlands from yearly national editions, the rest of Europe via
Eurostat LAU/NUTS. Free during development — no key required yet
(<code>POST /v1/keys {"email": …}</code> to get one for the beta).
Attribution: INSEE · IGN Licence Ouverte 2.0 · © EuroGeographics ·
© GeoBasis-DE / BKG dl-de/by-2-0 · CBS/Kadaster CC BY 4.0 —
details at <a href="https://confinia.io">confinia.io</a>.</footer>
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


# Événements UI de la démo. Hors /v1/ (jamais soumis à clé), fire-and-forget
# côté navigateur (fetch keepalive). Liste blanche => cardinalité bornée ;
# on ne stocke que le nom d'événement + le code pays (GeoIP), jamais d'IP.
FE_EVENTS = {"load", "play", "commune_history", "dept_switch",
             "region_switch", "country_switch", "timetravel", "share", "diff"}


@app.get("/beacon", include_in_schema=False)
def beacon(request: Request, e: str = ""):
    if FE_COUNTER is not None and e in FE_EVENTS:
        FE_COUNTER.add(1, {"event": e, "country": client_country(request)})
    return Response(status_code=204)


from pydantic import BaseModel, EmailStr  # noqa: E402


class KeyRequest(BaseModel):
    email: EmailStr
    note: str | None = None


@app.post("/v1/keys", status_code=201)
def create_key(req: KeyRequest):
    """Crée une clé API (gratuite — beta). À passer en en-tête X-API-Key."""
    with cursor() as cur:
        cur.execute("INSERT INTO api_key (email, note) VALUES (%s, %s) RETURNING key, created_at",
                    (req.email, req.note))
        key, created = cur.fetchone()
        cur.connection.commit()
    return {"key": str(key), "created_at": created.isoformat(),
            "usage": f"/v1/keys/{key}/usage"}


@app.get("/v1/keys/{key}/usage")
def key_usage(key: str):
    """Consommation des 30 derniers jours pour une clé."""
    with cursor() as cur:
        cur.execute(
            "SELECT day, requests FROM api_usage "
            "WHERE key = %s::uuid AND day > CURRENT_DATE - 30 ORDER BY day", (key,))
        rows = cur.fetchall()
    return {"key": key, "days": [{"day": d.isoformat(), "requests": n} for d, n in rows],
            "total_30d": sum(n for _, n in rows)}


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
    with cursor() as cur:
        cur.execute(
            f"SELECT {hist_cols(geometry)} FROM commune_version "
            "WHERE unit_type = 'commune' AND code = %s "
            "ORDER BY valid_from", (code,))
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"Code INSEE inconnu : {code}")
    versions = [feature(r) for r in rows]
    return {"code": code, "versions": versions, "events": derive_events(versions)}


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
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
):
    """Région(s) NUTS valide(s) à la date donnée : par code, par niveau (+ pays),
    ou par point (lat/lon + level) — « dans quelle province/canton suis-je ? »."""
    point = lat is not None and lon is not None
    if (code is None) == (level is None and not point):
        raise HTTPException(422, "Fournir code=, level= (+country=), ou lat=&lon=&level=.")
    with cursor() as cur:
        if point:
            if level is None:
                raise HTTPException(422, "lat/lon nécessite level=.")
            cur.execute(
                f"SELECT {COLS} FROM commune_version "
                "WHERE unit_type = %s AND valid_from <= %s AND valid_to > %s "
                "AND geom_simple IS NOT NULL "
                "AND ST_Intersects(geom_simple, ST_SetSRID(ST_Point(%s, %s), 4326)) "
                "LIMIT 1", (f"nuts{level}", at, at, lon, lat))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Aucune région NUTS ici à cette date.")
            return feature(row)
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


# Types « communaux » par pays : adaptateurs natifs + LAU Eurostat (largeur EU).
MUNICIPAL_TYPES = ("commune", "gemeinde", "gemeente", "lau")


@app.get("/v1/units")
def unit_at(
    response: Response,
    at: date = Query(..., description="Date de validité (YYYY-MM-DD)"),
    code: str | None = Query(None, max_length=16),
    country: str | None = Query(None, min_length=2, max_length=2),
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    bbox: str | None = Query(None, pattern=r"^-?[0-9.]+,-?[0-9.]+,-?[0-9.]+,-?[0-9.]+$",
                             description="minLon,minLat,maxLon,maxLat (≤ 6°×6°) → FeatureCollection"),
    region: str | None = Query(None, min_length=1, max_length=5, pattern=r"^[0-9A-Za-z]{1,5}$",
                               description="Préfixe de code avec country= (ex: region=09&country=DE "
                                           "→ toutes les Gemeinden de Bavière) → FeatureCollection"),
    nuts: str | None = Query(None, min_length=3, max_length=5, pattern=r"^[0-9A-Za-z]{3,5}$",
                             description="Code NUTS : toutes les unités communales dont le point "
                                         "représentatif est dans cette région (ex: nuts=ITC4C → "
                                         "province de Milan) → FeatureCollection"),
):
    """Unité administrative communale (tous pays) : par code (+country), par
    point (lat/lon), par emprise (bbox), ou par préfixe régional (region= +
    country= — Land allemand, province…)."""
    selectors = ((code is not None) + (lat is not None and lon is not None)
                 + (bbox is not None) + (region is not None) + (nuts is not None))
    if selectors != 1:
        raise HTTPException(422, "Fournir un critère : code=, lat=&lon=, bbox=, "
                                 "region=&country=, ou nuts=.")
    if region and not country:
        raise HTTPException(422, "region= nécessite country=.")
    with cursor() as cur:
        if nuts:
            # Appartenance spatiale : point représentatif de l'unité dans la
            # région NUTS (partition propre, pas de doublons de frontière).
            cur.execute(
                "WITH region AS (SELECT geom_simple AS g FROM commune_version "
                "  WHERE unit_type LIKE 'nuts%%' AND code = %s "
                "  AND valid_from <= %s AND valid_to > %s "
                "  ORDER BY valid_from DESC LIMIT 1) "
                f"SELECT {COLS} FROM commune_version, region "
                "WHERE unit_type = ANY(%s) AND valid_from <= %s AND valid_to > %s "
                "AND geom_simple IS NOT NULL AND geom_simple && region.g "
                "AND ST_Intersects(region.g, ST_PointOnSurface(geom_simple)) "
                "ORDER BY code LIMIT 4000",
                (nuts.upper(), at, at, list(MUNICIPAL_TYPES), at, at))
            rows = cur.fetchall()
            response.headers["Cache-Control"] = "public, max-age=3600"
            return {"type": "FeatureCollection", "features": [feature(r) for r in rows]}
        if region:
            cur.execute(
                f"SELECT {COLS} FROM commune_version "
                "WHERE unit_type = ANY(%s) AND country = %s AND code LIKE %s "
                "AND valid_from <= %s AND valid_to > %s "
                "ORDER BY code LIMIT 4000",
                (list(MUNICIPAL_TYPES), country.upper(), region + "%", at, at))
            rows = cur.fetchall()
            response.headers["Cache-Control"] = "public, max-age=3600"
            return {"type": "FeatureCollection", "features": [feature(r) for r in rows]}
        if bbox:
            w, s, e, n = (float(v) for v in bbox.split(","))
            if not (w < e and s < n) or (e - w) > 6 or (n - s) > 6:
                raise HTTPException(422, "bbox invalide ou trop grande (max 6°×6°).")
            sql = (f"SELECT {COLS} FROM commune_version "
                   "WHERE unit_type = ANY(%s) AND valid_from <= %s AND valid_to > %s "
                   "AND geom_simple && ST_MakeEnvelope(%s,%s,%s,%s,4326) ")
            params = [list(MUNICIPAL_TYPES), at, at, w, s, e, n]
            if country:
                sql += "AND country = %s "
                params.append(country.upper())
            cur.execute(sql + "ORDER BY code LIMIT 3000", params)
            rows = cur.fetchall()
            response.headers["Cache-Control"] = "public, max-age=3600"
            return {"type": "FeatureCollection", "features": [feature(r) for r in rows]}
        if code:
            sql = (f"SELECT {COLS} FROM commune_version "
                   "WHERE unit_type = ANY(%s) AND code = %s "
                   "AND valid_from <= %s AND valid_to > %s ")
            params = [list(MUNICIPAL_TYPES), code, at, at]
            if country:
                sql += "AND country = %s "
                params.append(country.upper())
            cur.execute(sql + "ORDER BY valid_from DESC LIMIT 1", params)
        else:
            cur.execute(
                f"SELECT {COLS} FROM commune_version "
                "WHERE unit_type = ANY(%s) "
                "AND valid_from <= %s AND valid_to > %s AND geom IS NOT NULL "
                "AND ST_Contains(geom, ST_SetSRID(ST_Point(%s, %s), 4326)) "
                "LIMIT 1", (list(MUNICIPAL_TYPES), at, at, lon, lat))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Aucune unité valide à cette date pour cette requête.")
    return feature(row)


@app.get("/v1/units/{code}/history")
def unit_history(code: str, country: str | None = Query(None), geometry: bool = Query(False)):
    """Toutes les versions d'une unité communale (tous pays)."""
    sql = (f"SELECT {hist_cols(geometry)} FROM commune_version "
           "WHERE unit_type = ANY(%s) AND code = %s ")
    params = [list(MUNICIPAL_TYPES), code]
    if country:
        sql += "AND country = %s "
        params.append(country.upper())
    with cursor() as cur:
        cur.execute(sql + "ORDER BY valid_from", params)
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"Code inconnu : {code}")
    versions = [feature(r) for r in rows]
    return {"code": code, "versions": versions, "events": derive_events(versions)}


@app.get("/v1/nuts/{code}/history")
def nuts_history(code: str, geometry: bool = Query(False)):
    """Toutes les versions d'un code NUTS."""
    with cursor() as cur:
        cur.execute(
            f"SELECT {hist_cols(geometry)} FROM commune_version "
            "WHERE unit_type LIKE 'nuts%%' AND code = %s "
            "ORDER BY valid_from", (code.upper(),))
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"Code NUTS inconnu : {code}")
    versions = [feature(r) for r in rows]
    return {"code": code.upper(), "versions": versions, "events": derive_events(versions)}
