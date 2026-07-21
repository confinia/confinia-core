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
DSN = os.environ["PG_DSN"]     # base GÉO (artefact de build, par couleur de stack)
# État OPÉRATIONNEL partagé (api_key, api_usage, visitor_daily) : mini-Postgres
# « ops » de la couche services. Fallback sur la base géo si absent (dev).
OPS_DSN = os.environ.get("OPS_DSN", "") or os.environ["PG_DSN"]

# Version applicative : copiée depuis VERSION (racine du repo) dans l'image par
# deploy/deploy-api.sh au build. Affichée par /healthz, /docs et le front.
APP_VERSION = "dev"
try:
    with open(os.path.join(os.path.dirname(__file__), "VERSION")) as _v:
        APP_VERSION = _v.read().strip() or "dev"
except OSError:
    pass

pool: psycopg2.pool.SimpleConnectionPool | None = None
ops_pool: psycopg2.pool.SimpleConnectionPool | None = None


KEYS_SQL = """
CREATE TABLE IF NOT EXISTS public.api_key (
    key        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email      text NOT NULL,
    note       text,
    created_at timestamptz NOT NULL DEFAULT now(),
    active     boolean NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS public.api_usage (
    key      uuid NOT NULL REFERENCES public.api_key(key),
    day      date NOT NULL,
    requests bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (key, day)
);
-- Visiteurs uniques par jour/pays. Jamais d'IP : client_hash est un condensé
-- salé (secret d'env + jour UTC), irréversible et illisible d'un jour à l'autre.
-- UNLOGGED : donnée d'observabilité, perdable sans regret. Purge à 45 jours.
CREATE UNLOGGED TABLE IF NOT EXISTS public.visitor_daily (
    day         date  NOT NULL,
    country     text  NOT NULL,
    client_hash bytea NOT NULL,
    PRIMARY KEY (day, client_hash)
);
DELETE FROM public.visitor_daily WHERE day < CURRENT_DATE - 45;
-- Intentions de paiement (page /pricing) : le pipeline commercial en
-- self-service. Lu à la main (ou par le futur webhook MoR), jamais purgé.
CREATE TABLE IF NOT EXISTS public.upgrade_intent (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    email      text NOT NULL,
    tier       text NOT NULL,
    use_case   text,
    UNIQUE (email, tier)
);
-- Palier par clé ('free' tant que le checkout MoR n'existe pas ; le webhook
-- de l'issue #8 passera 'pro'/'enterprise').
ALTER TABLE public.api_key ADD COLUMN IF NOT EXISTS tier text NOT NULL DEFAULT 'free';
-- Compteur À VIE des requêtes premium par appelant (clé ou condensé d'IP
-- STABLE, jamais l'IP) : les N premières sont offertes, au-delà 402 -> /pricing.
CREATE TABLE IF NOT EXISTS public.premium_usage (
    caller     text PRIMARY KEY,
    requests   bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- Abonnements Polar (Merchant of Record, issue #8) : état par souscription,
-- alimenté par le webhook. Le palier d'un email = sa meilleure souscription
-- active ; appliqué aux clés existantes ET aux clés créées ensuite.
CREATE TABLE IF NOT EXISTS public.polar_subscription (
    subscription_id text PRIMARY KEY,
    email           text NOT NULL,
    tier            text NOT NULL,
    status          text NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_polar_sub_email ON public.polar_subscription (email);
"""

# Clés facultatives pendant le développement ; passer REQUIRE_API_KEY=true
# à l'ouverture de la beta (plan 2.3 : metering dès le premier jour).
REQUIRE_KEY = os.environ.get("REQUIRE_API_KEY", "false").lower() == "true"
OPEN_PATHS = ("/", "/docs", "/openapi.json", "/redoc", "/healthz", "/v1/keys")


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pool, ops_pool
    last_err = None
    for _attempt in range(30):                     # les bases peuvent démarrer après nous
        try:
            pool = pool or psycopg2.pool.SimpleConnectionPool(1, 8, DSN)
            ops_pool = ops_pool or psycopg2.pool.SimpleConnectionPool(1, 4, OPS_DSN)
            break
        except psycopg2.OperationalError as e:
            last_err = e
            time.sleep(2)
    if pool is None or ops_pool is None:
        raise RuntimeError(f"Postgres injoignable : {last_err}")
    conn = ops_pool.getconn()                      # les tables opérationnelles vivent côté ops
    try:
        with conn, conn.cursor() as cur:
            cur.execute(KEYS_SQL)
    finally:
        ops_pool.putconn(conn)
    yield
    pool.closeall()
    ops_pool.closeall()


app = FastAPI(
    title="Confinia API",
    version=APP_VERSION,
    description="EU administrative boundaries with full historical versioning. "
                "Data: INSEE COG + IGN Admin Express (Licence Ouverte 2.0).",
    lifespan=lifespan,
)


# API publique en lecture seule : CORS ouvert (la démo tourne sur GitHub Pages).
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["GET", "POST"], allow_headers=["*"])

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
    if not ip or not VISITOR_SECRET or ops_pool is None:
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
        conn = ops_pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO public.visitor_daily (day, country, client_hash) "
                    "VALUES (CURRENT_DATE, %s, %s) ON CONFLICT DO NOTHING",
                    (country, h))
        finally:
            ops_pool.putconn(conn)
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
        conn = ops_pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("SELECT active FROM public.api_key WHERE key = %s::uuid", (key,))
                row = cur.fetchone()
                if not row or not row[0]:
                    return None
                cur.execute(
                    "INSERT INTO public.api_usage (key, day, requests) VALUES (%s::uuid, CURRENT_DATE, 1) "
                    "ON CONFLICT (key, day) DO UPDATE SET requests = api_usage.requests + 1", (key,))
                return key
        finally:
            ops_pool.putconn(conn)
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
            and not request.url.path.startswith(("/v1/keys", "/v1/upgrade-intent"))):
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


@contextmanager
def ops_cursor():
    """Curseur sur la base OPS partagée (clés, usage, intentions) : commit
    automatique en sortie — ces écritures doivent survivre aux couleurs."""
    conn = ops_pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            yield cur
    finally:
        ops_pool.putconn(conn)


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


@app.get("/v1/countries")
def countries(response: Response):
    """Silhouettes des pays couverts HORS nomenclature NUTS (ex. NZ) :
    union des unités courantes par pays. Complète /v1/nuts?level=0 pour les
    couches de navigation."""
    with cursor() as cur:
        cur.execute(
            "SELECT country, ST_AsGeoJSON(ST_Multi(ST_SimplifyPreserveTopology("
            "  ST_Union(geom_simple), 0.01)), 5) "
            "FROM commune_version "
            "WHERE valid_to = %s AND geom_simple IS NOT NULL "
            "  AND country NOT IN (SELECT DISTINCT country FROM commune_version "
            "                      WHERE unit_type = 'nuts0') "
            "GROUP BY country ORDER BY country", (FAR_FUTURE,))
        rows = cur.fetchall()
    response.headers["Cache-Control"] = "public, max-age=86400"
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": json.loads(g),
         "properties": {"code": c, "country": c}}
        for c, g in rows]}


@app.get("/v1/attributions")
def attributions():
    """Registre des sources : licence, attribution et conditions par source."""
    with cursor() as cur:
        cur.execute("SELECT source, license, attribution, commercial_use, source_url "
                    "FROM public.data_source ORDER BY source")
        return {"sources": [
            {"source": s, "license": li, "attribution": a,
             "commercial_use": c, "url": u}
            for s, li, a, c, u in cur.fetchall()]}


# Suggestion d'admin_level OHM par type d'unité (conventions OSM/OHM France
# et voisins ; les cantons historiques n'ont pas d'équivalent moderne, 9 par
# convention de proposition). C'est une SUGGESTION : la communauté OHM décide.
OHM_ADMIN_LEVEL = {
    "nuts1": 4, "nuts2": 5, "nuts3": 6, "region": 4,
    "departement": 6, "arrondissement": 7, "canton": 9,
    "commune": 8, "gemeinde": 8, "gemeente": 8, "lau": 8, "lad": 8, "ta": 6,
}
REF_KEY = {"FR": "ref:INSEE", "GB": "ref:gss", "DE": "ref:ags", "NL": "ref:cbs"}
EXPORT_MAX = 5000


@app.get("/v1/export/ohm")
def export_ohm(
    response: Response,
    country: str = Query(..., min_length=2, max_length=2),
    unit_type: str = Query(..., description="commune, departement, canton…"),
    date_from: date | None = Query(None, alias="from",
                                   description="Ne garder que les versions actives après cette date"),
    date_to: date | None = Query(None, alias="to",
                                 description="Ne garder que les versions actives avant cette date"),
    full_geometry: bool = Query(False, description="Géométrie brute (défaut : simplifiée)"),
    limit: int = Query(1000, ge=1, le=EXPORT_MAX),
    offset: int = Query(0, ge=0),
):
    """Export « OHM-ready » : chaque VERSION d'unité en Feature GeoJSON avec
    `start_date`/`end_date` (conventions OpenHistoricalMap), la référence
    officielle (`ref:INSEE`…), une suggestion d'`admin_level` et l'attribution
    de la source. Pensé pour préparer des imports OHM (issue #3) : le consensus
    communautaire et l'outillage d'upload restent côté OHM."""
    geom_col = "ST_AsGeoJSON(geom, 6)" if full_geometry else "ST_AsGeoJSON(geom_simple, 6)"
    where = ["country = %s", "unit_type = %s"]
    params: list = [country.upper(), unit_type]
    if date_from is not None:
        where.append("valid_to > %s")
        params.append(date_from)
    if date_to is not None:
        where.append("valid_from < %s")
        params.append(date_to)
    with cursor() as cur:
        cur.execute(
            f"SELECT cv.code, cv.nom, cv.valid_from, cv.valid_to, cv.unit_type, "
            f" cv.geometry_approx, cv.source, ds.attribution, ds.license, {geom_col} "
            "FROM commune_version cv "
            "LEFT JOIN public.data_source ds ON ds.source = cv.source "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY cv.code, cv.valid_from LIMIT %s OFFSET %s",
            params + [limit + 1, offset])
        rows = cur.fetchall()
    truncated = len(rows) > limit
    rows = rows[:limit]
    ref_key = REF_KEY.get(country.upper(), "ref")
    feats = []
    for code, nom, vf, vt, ut, approx, src, attr, lic, g in rows:
        props = {
            "name": nom, ref_key: code,
            "boundary": "administrative",
            "admin_level": OHM_ADMIN_LEVEL.get(ut),
            "start_date": vf.isoformat(),
            "unit_type": ut, "source": attr or src,
            "license": lic, "geometry_approx": approx,
        }
        if vt != FAR_FUTURE:
            props["end_date"] = vt.isoformat()
        feats.append({"type": "Feature", "geometry": json.loads(g) if g else None,
                      "properties": props})
    response.headers["Cache-Control"] = "public, max-age=86400"
    return {"type": "FeatureCollection",
            "count": len(feats), "offset": offset, "truncated": truncated,
            "note": ("admin_level est une suggestion ; start_date/end_date suivent "
                     "les conventions OHM. Attribution obligatoire par source "
                     "(voir /v1/attributions)."),
            "features": feats}


# ---------------------------------------------------------------------------
# Premium : rapport de changements d'une zone, provenance complète.
# Modèle économique (fondateur, 2026-07-21) : les 9 premières requêtes sont
# offertes, la 10e exige un palier payant -> 402 avec pointeur /pricing tant
# que le checkout MoR (issue #8) n'est pas branché.
PREMIUM_FREE = 9
PRICING_URL = "https://www.confinia.io/pricing"


def premium_gate(request: Request) -> dict:
    """Retourne le quota {used, free_limit, remaining} de l'appelant, ou lève
    402. Appelant = clé API valide (tier 'pro'/'enterprise' = illimité), sinon
    condensé STABLE et irréversible de l'IP (jamais l'IP elle-même)."""
    key = request.headers.get("x-api-key") or request.query_params.get("api_key")
    caller = None
    if key:
        with ops_cursor() as cur:
            cur.execute("SELECT active, tier FROM public.api_key WHERE key = %s::uuid", (key,))
            row = cur.fetchone()
        if row and row[0]:
            if row[1] in ("pro", "enterprise"):
                return {"used": None, "free_limit": None, "remaining": "unlimited"}
            caller = f"key:{key}"
    if caller is None:
        ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
            or (request.client.host if request.client else "anon")
        caller = "ip:" + hashlib.sha256(f"{VISITOR_SECRET}|premium|{ip}".encode()).hexdigest()[:32]
    with ops_cursor() as cur:
        cur.execute(
            "INSERT INTO public.premium_usage (caller, requests) VALUES (%s, 1) "
            "ON CONFLICT (caller) DO UPDATE SET requests = premium_usage.requests + 1, "
            " updated_at = now() RETURNING requests", (caller,))
        used = cur.fetchone()[0]
    if used > PREMIUM_FREE:
        raise HTTPException(402, {
            "detail": f"Les {PREMIUM_FREE} premiers rapports de changements sont offerts ; "
                      "au-delà, c'est le palier Pro.",
            "pricing": PRICING_URL,
            "note": "Réserver maintenant fige le tarif de lancement.",
        })
    return {"used": used, "free_limit": PREMIUM_FREE, "remaining": PREMIUM_FREE - used}


CHANGES_MAX_UNITS = 300


@app.get("/v1/changes")
def area_changes(
    request: Request,
    response: Response,
    bbox: str = Query(..., description="w,s,e,n (WGS84)"),
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
):
    """PREMIUM — tous les CHANGEMENTS des unités municipales d'une zone
    (fusions, scissions, renommages, créations, disparitions), datés, avec la
    provenance complète (source, licence, attribution) de chaque unité.
    9 rapports offerts, palier Pro ensuite."""
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError:
        raise HTTPException(422, "bbox attendu : w,s,e,n")
    quota = premium_gate(request)
    with cursor() as cur:
        cur.execute(
            "SELECT country, unit_type, code FROM commune_version "
            "WHERE unit_type = ANY(%s) AND geom && ST_MakeEnvelope(%s,%s,%s,%s,4326) "
            "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3 LIMIT %s",
            (list(MUNICIPAL_TYPES), w, s, e, n, CHANGES_MAX_UNITS + 1))
        units = cur.fetchall()
        truncated = len(units) > CHANGES_MAX_UNITS
        units = units[:CHANGES_MAX_UNITS]
        cur.execute("SELECT source, attribution, license FROM public.data_source")
        src_info = {r[0]: {"attribution": r[1], "license": r[2]} for r in cur.fetchall()}
        events = []
        for country, ut, code in units:
            cur.execute(
                "SELECT nom, valid_from, valid_to, parents, children, source "
                "FROM commune_version WHERE country=%s AND unit_type=%s AND code=%s "
                "ORDER BY valid_from", (country, ut, code))
            rows = cur.fetchall()
            vs = [{"type": "Feature", "properties": {
                       "code": code, "nom": nom,
                       "valid_from": vf.isoformat(),
                       "valid_to": None if vt == FAR_FUTURE else vt.isoformat(),
                       "parents": parents, "children": children}}
                  for nom, vf, vt, parents, children, _src in rows]
            last_src = rows[-1][5] if rows else None
            for ev in derive_events(vs):
                d = ev.get("date")
                if d and date_from and date.fromisoformat(d) < date_from:
                    continue
                if d and date_to and date.fromisoformat(d) >= date_to:
                    continue
                events.append({
                    "country": country, "unit_type": ut, "code": code,
                    "name": vs[-1]["properties"]["nom"] if vs else None,
                    **ev,
                    "source": last_src,
                    **(src_info.get(last_src) or {}),
                })
        events.sort(key=lambda ev: (ev.get("date") or "9999", ev["code"]))
    return {"bbox": [w, s, e, n],
            "from": date_from.isoformat() if date_from else None,
            "to": date_to.isoformat() if date_to else None,
            "units_scanned": len(units), "units_truncated": truncated,
            "events": events, "quota": quota,
            "note": "Attribution des sources obligatoire (champ attribution par "
                    "événement ; registre complet : /v1/attributions)."}


# ---------------------------------------------------------------------------
# Rapport communal téléchargeable (issue #14) : document CITABLE généré côté
# serveur (traçabilité complète + contour à chaque période, provenance par
# fait). SVG et PDF partagent le même quota premium que /v1/changes.
REPORT_MAX_VERSIONS = 60


def _report_data(code: str, country: str) -> dict:
    with cursor() as cur:
        cur.execute(
            "SELECT nom, valid_from, valid_to, parents, children, source, "
            " geometry_vintage, geometry_approx, ST_AsGeoJSON(geom_simple, 5) "
            "FROM commune_version "
            "WHERE unit_type = ANY(%s) AND code = %s AND country = %s "
            "ORDER BY valid_from LIMIT %s",
            (list(MUNICIPAL_TYPES), code, country, REPORT_MAX_VERSIONS))
        rows = cur.fetchall()
        if not rows:
            raise HTTPException(404, f"Unité inconnue : {country}/{code}")
        cur.execute("SELECT source, attribution, license FROM public.data_source")
        src_info = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    versions = []
    for nom, vf, vt, parents, children, src, vintage, approx, g in rows:
        rings = []
        if g:
            gj = json.loads(g)
            polys = gj["coordinates"] if gj["type"] == "MultiPolygon" else [gj["coordinates"]]
            for poly in polys:
                rings.extend(poly)
        versions.append({
            "nom": nom, "valid_from": vf, "valid_to": vt,
            "parents": parents or [], "children": children or [],
            "source": src, "vintage": vintage, "approx": approx, "rings": rings,
        })
    feats = [{"type": "Feature", "properties": {
        "code": code, "nom": v["nom"],
        "valid_from": v["valid_from"].isoformat(),
        "valid_to": None if v["valid_to"] == FAR_FUTURE else v["valid_to"].isoformat(),
        "parents": v["parents"], "children": v["children"]}} for v in versions]
    xs = [x for v in versions for ring in v["rings"] for x, _ in ring]
    ys = [y for v in versions for ring in v["rings"] for _, y in ring]
    return {"code": code, "country": country, "versions": versions,
            "events": derive_events(feats),
            "bbox": (min(xs), min(ys), max(xs), max(ys)) if xs else None,
            "attributions": sorted({src_info[v["source"]]
                                    for v in versions
                                    if v["source"] in src_info})}


def _ring_points(ring, bbox, ox, oy, w, h):
    """Projette un anneau lon/lat dans une cellule (équirectangulaire locale,
    y inversé), échelle uniforme centrée."""
    import math
    w0, s0, e0, n0 = bbox
    kx = math.cos(math.radians((s0 + n0) / 2))
    gw, gh = max((e0 - w0) * kx, 1e-9), max(n0 - s0, 1e-9)
    sc = min(w / gw, h / gh)
    cx, cy = ox + w / 2, oy + h / 2
    return [(cx + ((x - (w0 + e0) / 2) * kx) * sc,
             cy - (y - (s0 + n0) / 2) * sc) for x, y in ring]


def _period_str(v) -> str:
    vt = "today" if v["valid_to"] == FAR_FUTURE else v["valid_to"].isoformat()
    return f"{v['valid_from'].isoformat()} → {vt}"


def _report_svg(d: dict) -> str:
    import html as html_mod
    esc = html_mod.escape
    W, PAD = 840, 28
    cur_name = d["versions"][-1]["nom"]
    parts, y = [], PAD + 8
    def text(x, yy, s, size=13, weight="normal", fill="#1a2333", anchor="start"):
        parts.append(f'<text x="{x}" y="{yy}" font-size="{size}" font-family="system-ui,sans-serif" '
                     f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{esc(s)}</text>')
    text(PAD, y + 10, "Confinia — commune record / fiche communale", 13, fill="#5b6b85"); y += 26
    text(PAD, y + 12, f"{cur_name} ({d['code']}, {d['country']})", 26, "bold"); y += 40
    text(PAD, y, f"{len(d['versions'])} recorded version(s) · generated by Confinia API v{APP_VERSION} · "
                 f"https://www.confinia.io/commune/{d['code']}", 11, fill="#5b6b85"); y += 24
    text(PAD, y, "Chronology / chronologie", 15, "bold"); y += 8
    for ev in d["events"][:60]:
        y += 17
        pre = f"{ev['date']} — " if ev.get("date") else ""
        text(PAD + 8, y, f"• {pre}{ev['detail']}"[:130], 12)
    y += 26
    text(PAD, y, "Boundaries by period / contours par période (same scale)", 15, "bold"); y += 12
    CELL_W, CELL_H, DRAW_H = (W - 2 * PAD) // 2, 320, 250
    for i, v in enumerate(d["versions"]):
        col, row_y = i % 2, y + (i // 2) * CELL_H
        ox = PAD + col * CELL_W
        parts.append(f'<rect x="{ox + 6}" y="{row_y + 6}" width="{CELL_W - 12}" height="{CELL_H - 12}" '
                     'fill="none" stroke="#d5dbe6" rx="8"/>')
        if v["rings"] and d["bbox"]:
            path = " ".join(
                "M " + " L ".join(f"{px:.1f} {py:.1f}" for px, py in
                                  _ring_points(ring, d["bbox"], ox + 20, row_y + 18,
                                               CELL_W - 40, DRAW_H - 24)) + " Z"
                for ring in v["rings"])
            parts.append(f'<path d="{path}" fill="#dbe7fb" stroke="#3a5f95" '
                         'stroke-width="1.2" fill-rule="evenodd"/>')
        else:
            text(ox + CELL_W / 2, row_y + DRAW_H / 2, "no geometry", 12, fill="#8a94a6", anchor="middle")
        text(ox + CELL_W / 2, row_y + DRAW_H + 16, v["nom"], 14, "bold", anchor="middle")
        text(ox + CELL_W / 2, row_y + DRAW_H + 34, _period_str(v), 12, fill="#5b6b85", anchor="middle")
        vin = f"geometry vintage {v['vintage'].isoformat()}" if v["vintage"] else "geometry vintage n/a"
        if v["approx"]:
            vin += " (approx.)"
        text(ox + CELL_W / 2, row_y + DRAW_H + 50, vin, 10, fill="#8a94a6", anchor="middle")
    y += ((len(d["versions"]) + 1) // 2) * CELL_H + 20
    text(PAD, y, "Sources:", 11, "bold", "#5b6b85"); y += 4
    for attr, lic in d["attributions"]:
        y += 15
        text(PAD + 8, y, f"{attr} — {lic}", 10, fill="#5b6b85")
    y += 30
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{y}" '
            f'viewBox="0 0 {W} {y}"><rect width="{W}" height="{y}" fill="#ffffff"/>'
            + "".join(parts) + "</svg>")


def _report_pdf(d: dict) -> bytes:
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas
    W, H = A4
    PAD = 50
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Confinia — {d['versions'][-1]['nom']} ({d['code']})")
    def footer():
        c.setFont("Helvetica", 7)
        c.setFillColorRGB(.45, .5, .58)
        yy = 26
        for attr, lic in d["attributions"]:
            c.drawString(PAD, yy, f"{attr} — {lic}"[:110])
            yy -= 9
        c.drawRightString(W - PAD, 26, f"Confinia API v{APP_VERSION} · www.confinia.io")
    cur_name = d["versions"][-1]["nom"]
    c.setFillColorRGB(.36, .42, .52); c.setFont("Helvetica", 10)
    c.drawString(PAD, H - 60, "Confinia — commune record / fiche communale")
    c.setFillColorRGB(.1, .14, .2); c.setFont("Helvetica-Bold", 22)
    c.drawString(PAD, H - 88, f"{cur_name} ({d['code']}, {d['country']})")
    c.setFont("Helvetica", 9); c.setFillColorRGB(.36, .42, .52)
    c.drawString(PAD, H - 104, f"{len(d['versions'])} recorded version(s) · full lineage with per-fact provenance")
    y = H - 136
    c.setFont("Helvetica-Bold", 13); c.setFillColorRGB(.1, .14, .2)
    c.drawString(PAD, y, "Chronology / chronologie"); y -= 18
    c.setFont("Helvetica", 9.5)
    for ev in d["events"]:
        if y < 70:
            footer(); c.showPage(); y = H - 70; c.setFont("Helvetica", 9.5)
        pre = f"{ev['date']} — " if ev.get("date") else ""
        c.setFillColorRGB(.1, .14, .2)
        c.drawString(PAD + 6, y, f"• {pre}{ev['detail']}"[:118]); y -= 14
    footer(); c.showPage()
    per_page, slot_h = 2, (H - 140) / 2
    for i, v in enumerate(d["versions"]):
        slot = i % per_page
        if slot == 0 and i:
            footer(); c.showPage()
        top = H - 60 - slot * slot_h
        c.setFont("Helvetica-Bold", 12); c.setFillColorRGB(.1, .14, .2)
        c.drawString(PAD, top, f"{v['nom']} — {_period_str(v)}")
        vin = f"geometry vintage {v['vintage'].isoformat()}" if v["vintage"] else "geometry vintage n/a"
        if v["approx"]:
            vin += " (approx.)"
        c.setFont("Helvetica", 8.5); c.setFillColorRGB(.45, .5, .58)
        c.drawString(PAD, top - 13, vin)
        if v["rings"] and d["bbox"]:
            c.setFillColorRGB(.86, .91, .98); c.setStrokeColorRGB(.23, .37, .58)
            draw_w, draw_h, top_draw = W - 2 * PAD, slot_h - 70, top - 30
            for ring in v["rings"]:
                # _ring_points renvoie du y-vers-le-bas (convention SVG) dans
                # [0..w]x[0..h] ; PDF est y-vers-le-haut : Y = haut - py.
                pts = _ring_points(ring, d["bbox"], 0, 0, draw_w, draw_h)
                p = c.beginPath()
                p.moveTo(PAD + pts[0][0], top_draw - pts[0][1])
                for px, py in pts[1:]:
                    p.lineTo(PAD + px, top_draw - py)
                p.close()
                c.drawPath(p, stroke=1, fill=1)
    footer(); c.showPage(); c.save()
    return buf.getvalue()


@app.get("/v1/communes/{code}/report.svg")
def commune_report_svg(request: Request, code: str, country: str = "FR"):
    """PREMIUM — rapport SVG : traçabilité complète + contours par période.
    Même quota gratuit que /v1/changes (9 rapports), palier Pro ensuite."""
    quota = premium_gate(request)
    svg = _report_svg(_report_data(code, country.upper()))
    return Response(svg, media_type="image/svg+xml", headers={
        "Content-Disposition": f'inline; filename="confinia-{country}-{code}.svg"',
        "Cache-Control": "no-store",
        "X-Premium-Remaining": str(quota.get("remaining"))})


@app.get("/v1/communes/{code}/report.pdf")
def commune_report_pdf(request: Request, code: str, country: str = "FR"):
    """PREMIUM — le même rapport en PDF (document citable)."""
    quota = premium_gate(request)
    pdf = _report_pdf(_report_data(code, country.upper()))
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="confinia-{country}-{code}.pdf"',
        "Cache-Control": "no-store",
        "X-Premium-Remaining": str(quota.get("remaining"))})


@app.get("/healthz")
def healthz():
    with cursor() as cur:
        cur.execute("SELECT count(*) FROM commune_version")
        return {"status": "ok", "version": APP_VERSION, "versions": cur.fetchone()[0]}


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
    # Base OPS impérativement : le metering (meter_key) lit public.api_key côté
    # ops ; une clé écrite côté géo serait invisible et donc inutilisable.
    email = req.email.strip().lower()
    with ops_cursor() as cur:
        # Une souscription Polar active sur cet email donne son palier aux
        # clés créées APRÈS l'achat (l'ordre achat/clé est indifférent).
        cur.execute("SELECT tier FROM public.polar_subscription "
                    "WHERE email = %s AND status = ANY(%s)", (email, list(POLAR_ACTIVE)))
        tiers = {t for (t,) in cur.fetchall()}
        tier = "enterprise" if "enterprise" in tiers else "pro" if "pro" in tiers else "free"
        cur.execute("INSERT INTO public.api_key (email, note, tier) VALUES (%s, %s, %s) "
                    "RETURNING key, created_at", (email, req.note, tier))
        key, created = cur.fetchone()
    return {"key": str(key), "created_at": created.isoformat(), "tier": tier,
            "usage": f"/v1/keys/{key}/usage"}


@app.get("/v1/keys/{key}/usage")
def key_usage(key: str):
    """Consommation des 30 derniers jours pour une clé."""
    with ops_cursor() as cur:
        cur.execute(
            "SELECT day, requests FROM public.api_usage "
            "WHERE key = %s::uuid AND day > CURRENT_DATE - 30 ORDER BY day", (key,))
        rows = cur.fetchall()
    return {"key": key, "days": [{"day": d.isoformat(), "requests": n} for d, n in rows],
            "total_30d": sum(n for _, n in rows)}


class IntentRequest(BaseModel):
    email: EmailStr
    tier: str
    use_case: str | None = None


@app.post("/v1/upgrade-intent", status_code=201)
def upgrade_intent(req: IntentRequest):
    """Capture d'intention de paiement depuis /pricing. Idempotent par
    (email, tier) : re-soumettre met à jour le cas d'usage."""
    tier = req.tier.strip().lower()
    if tier not in ("pro", "enterprise"):
        raise HTTPException(422, "tier doit être 'pro' ou 'enterprise'")
    use_case = (req.use_case or "").strip()[:2000] or None
    with ops_cursor() as cur:
        cur.execute(
            "INSERT INTO public.upgrade_intent (email, tier, use_case) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (email, tier) DO UPDATE SET use_case = EXCLUDED.use_case",
            (req.email, tier, use_case))
    return {"status": "recorded", "tier": tier,
            "note": "Merci ! Vous serez prévenu à l'ouverture du palier, "
                    "tarif de lancement garanti."}


# ---------------------------------------------------------------------------
# Polar (Merchant of Record, issue #8) : Polar est le vendeur légal (TVA,
# factures, relances) ; nous ne touchons jamais la carte. Ce webhook applique
# le palier payé aux clés de l'email acheteur : la boucle achat -> clé active
# tourne sans intervention humaine.
POLAR_WEBHOOK_SECRET = os.environ.get("POLAR_WEBHOOK_SECRET", "")
POLAR_TIER_BY_PRODUCT = {
    pid: tier for tier, pid in (
        ("pro", os.environ.get("POLAR_PRODUCT_PRO", "")),
        ("enterprise", os.environ.get("POLAR_PRODUCT_ENTERPRISE", "")),
    ) if pid
}
POLAR_ACTIVE = ("active", "trialing")


def polar_verify(request: Request, body: bytes) -> bool:
    """Signature « Standard Webhooks » : HMAC-SHA256 de `id.timestamp.corps`,
    secret base64 (préfixe whsec_ éventuel), en-tête webhook-signature
    'v1,<b64> ...'. Refus si secret absent : jamais de webhook en aveugle."""
    import base64
    import hmac as hmac_mod
    if not POLAR_WEBHOOK_SECRET:
        return False
    mid = request.headers.get("webhook-id", "")
    ts = request.headers.get("webhook-timestamp", "")
    sigs = request.headers.get("webhook-signature", "")
    if not (mid and ts and sigs):
        return False
    secret = POLAR_WEBHOOK_SECRET.removeprefix("whsec_")
    try:
        key = base64.b64decode(secret + "=" * (-len(secret) % 4))
    except Exception:
        key = secret.encode()
    signed = f"{mid}.{ts}.".encode() + body
    want = base64.b64encode(hmac_mod.new(key, signed, hashlib.sha256).digest()).decode()
    return any(hmac_mod.compare_digest(want, s.split(",", 1)[-1])
               for s in sigs.split() if s)


def polar_apply_tier(email: str) -> str:
    """Palier effectif d'un email = sa meilleure souscription active ;
    répercuté sur TOUTES ses clés existantes. Les clés créées plus tard
    héritent du palier via create_key."""
    with ops_cursor() as cur:
        cur.execute("SELECT tier FROM public.polar_subscription "
                    "WHERE email = %s AND status = ANY(%s)", (email, list(POLAR_ACTIVE)))
        tiers = {t for (t,) in cur.fetchall()}
        tier = "enterprise" if "enterprise" in tiers else "pro" if "pro" in tiers else "free"
        cur.execute("UPDATE public.api_key SET tier = %s WHERE email = %s", (tier, email))
    return tier


@app.post("/polar/webhook", include_in_schema=False)
async def polar_webhook(request: Request):
    body = await request.body()
    if not polar_verify(request, body):
        raise HTTPException(401, "signature webhook invalide")
    try:
        evt = json.loads(body)
    except ValueError:
        raise HTTPException(422, "corps JSON attendu")
    etype = str(evt.get("type", ""))
    data = evt.get("data") or {}
    if not etype.startswith("subscription."):
        return {"status": "ignored", "type": etype}
    sub_id = str(data.get("id") or "")
    status = str(data.get("status") or "")
    email = ((data.get("customer") or {}).get("email")
             or (data.get("user") or {}).get("email") or "").strip().lower()
    product_id = str(data.get("product_id")
                     or (data.get("product") or {}).get("id") or "")
    tier = POLAR_TIER_BY_PRODUCT.get(product_id)
    if not (sub_id and email and tier):
        return {"status": "ignored", "reason": "souscription incomplète ou produit inconnu"}
    with ops_cursor() as cur:
        cur.execute(
            "INSERT INTO public.polar_subscription (subscription_id, email, tier, status) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (subscription_id) DO UPDATE SET "
            " email = EXCLUDED.email, tier = EXCLUDED.tier, "
            " status = EXCLUDED.status, updated_at = now()",
            (sub_id, email, tier, status))
    effective = polar_apply_tier(email)
    return {"status": "ok", "email_tier": effective}


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
def departements(response: Response,
                 at: date | None = Query(None,
                     description="Date de validité : avant 1941, sert le "
                                 "découpage HISTORIQUE (TRF-GIS, 1870-1940)")):
    """Contours départementaux. Sans `at` (ou date moderne) : actuels (union
    des communes). Avec `at` <= 1940 : le découpage de l'époque, année par
    année (l'Alsace-Moselle sort en 1871 et revient en 1919)."""
    with cursor() as cur:
        if at is not None and at < date(1941, 1, 1):
            cur.execute(
                "SELECT code, nom, valid_from, valid_to, ST_AsGeoJSON(geom_simple, 5) "
                "FROM commune_version "
                "WHERE unit_type = 'departement' AND valid_from <= %s AND valid_to > %s "
                "ORDER BY code", (at, at))
            rows = cur.fetchall()
            if rows:
                response.headers["Cache-Control"] = "public, max-age=86400"
                return {"type": "FeatureCollection", "features": [
                    {"type": "Feature", "geometry": json.loads(g),
                     "properties": {"dept": c, "nom": n, "valid_from": str(vf),
                                    "valid_to": str(vt), "historical": True}}
                    for c, n, vf, vt, g in rows]}
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
MUNICIPAL_TYPES = ("commune", "gemeinde", "gemeente", "lau", "lad", "ta")


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
            # Géométrie NUTS : à la date demandée, sinon la DERNIÈRE connue
            # (navigation : le UK sort des éditions NUTS après 2021, mais ses
            # régions restent le bon contenant pour ouvrir les autorités).
            cur.execute(
                "WITH region AS (SELECT geom_simple AS g FROM commune_version "
                "  WHERE unit_type LIKE 'nuts%%' AND code = %s "
                "  ORDER BY (valid_from <= %s AND valid_to > %s) DESC, "
                "           valid_to DESC LIMIT 1) "
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
