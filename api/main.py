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
-- Daily premium counters for PAID tiers (issue #19 phase 3): paid plans get
-- a per-day allowance instead of unlimited; free callers keep the lifetime
-- 9-report allowance above.
CREATE TABLE IF NOT EXISTS public.premium_usage_daily (
    caller   text NOT NULL,
    day      date NOT NULL,
    requests bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (caller, day)
);
-- Distinct billable artifacts per caller per period (issue #83): a "report" is
-- a town record (or a specific area-change query), counted ONCE — re-fetching
-- the same artifact is free. `period` = EPOCH for the free lifetime bucket, the
-- 1st of the month for a Pro caller.
CREATE TABLE IF NOT EXISTS public.premium_seen (
    caller  text NOT NULL,
    period  date NOT NULL,
    unit    text NOT NULL,
    seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (caller, period, unit)
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
-- Polar customer id (issue #81): lets us mint a customer-portal session so the
-- buyer can self-serve their invoices; backfilled from the subscription webhook.
ALTER TABLE public.polar_subscription ADD COLUMN IF NOT EXISTS customer_id text;
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


# Keycloak Bearer JWT (issue #36): accept a realm-issued token as an
# alternative to X-API-Key. Verified against the realm JWKS; the token email
# resolves to (or creates) the caller's key, and the organization claim rides
# along as the tenant dimension. Optional: absent config = feature off.
KC_ISSUER = os.environ.get("KC_ISSUER", "")   # e.g. https://www.confinia.io/auth/realms/confinia
# WHERE to fetch discovery/JWKS from. On a basic-auth host (sandbox/staging) the
# public issuer URL is gated by the edge, so the API's server-side fetch is
# blocked; point KC_DISCOVERY at the INTERNAL Keycloak realm base instead. The
# token's `iss` is still validated against KC_ISSUER. Defaults to KC_ISSUER.
KC_DISCOVERY = os.environ.get("KC_DISCOVERY", "") or KC_ISSUER
_JWKS: dict = {}


def _jwks():
    global _JWKS
    if _JWKS or not KC_DISCOVERY:
        return _JWKS
    try:
        import urllib.request
        conf = json.loads(urllib.request.urlopen(
            f"{KC_DISCOVERY}/.well-known/openid-configuration", timeout=5).read())
        keys = json.loads(urllib.request.urlopen(conf["jwks_uri"], timeout=5).read())
        _JWKS = {k["kid"]: k for k in keys["keys"]}
    except Exception:
        _JWKS = {}
    return _JWKS


def bearer_identity(request: Request) -> dict | None:
    """Validate a Keycloak JWT and return {email, organization} or None.
    The token may arrive as `Authorization: Bearer …` (API clients) OR as an
    `X-Access-Token` header. The latter lets a browser call the API on a
    basic-auth-protected host (sandbox/staging) without its JWT colliding with
    the `Authorization: Basic` header the edge requires (issue #81)."""
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else request.headers.get("x-access-token", "")
    if not token or not KC_ISSUER:
        return None
    try:
        import jwt  # PyJWT
        from jwt import PyJWK
        kid = jwt.get_unverified_header(token).get("kid")
        jwk = _jwks().get(kid)
        if jwk is None:
            _JWKS.clear()               # rotation: refetch once
            jwk = _jwks().get(kid)
        if jwk is None:
            return None
        claims = jwt.decode(token, PyJWK.from_dict(jwk).key,
                            algorithms=["RS256"], issuer=KC_ISSUER,
                            options={"verify_aud": False})
        email = (claims.get("email") or "").strip().lower()
        return {"email": email, "organization": claims.get("organization")} if email else None
    except Exception:
        return None


def key_for_email(email: str) -> str | None:
    """The (active) API key of an email, creating one if none exists — so a
    Bearer-authenticated caller is metered exactly like an X-API-Key caller."""
    try:
        with ops_cursor() as cur:
            cur.execute("SELECT key::text FROM public.api_key "
                        "WHERE email=%s AND active ORDER BY created_at LIMIT 1", (email,))
            row = cur.fetchone()
            if row:
                return row[0]
            cur.execute("SELECT tier FROM public.polar_subscription "
                        "WHERE email=%s AND status=ANY(%s)", (email, list(POLAR_ACTIVE)))
            tiers = {t for (t,) in cur.fetchall()}
            tier = "enterprise" if "enterprise" in tiers else "pro" if "pro" in tiers else "free"
            cur.execute("INSERT INTO public.api_key (email, note, tier) "
                        "VALUES (%s, 'via keycloak', %s) RETURNING key::text", (email, tier))
            return cur.fetchone()[0]
    except Exception:
        return None


def meter_key(request: Request) -> str | None:
    """Validate the API key or Bearer JWT and count today's usage. Fail-open."""
    key = request.headers.get("x-api-key") or request.query_params.get("api_key")
    if not key:
        ident = bearer_identity(request)
        if ident:
            key = key_for_email(ident["email"])
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


# --- Internationalization (issue #79) --------------------------------------
# Supported end-user languages. French is the default for France (country FR),
# English is the fallback everywhere else; an explicit choice always wins.
REPORT_LANGS = ("en", "fr")


def resolve_lang(lang: str | None, country: str) -> str:
    """Resolve the response language: an explicit supported `lang` wins, else
    French for France and English as the fallback for every other country."""
    if lang:
        code = lang.lower()[:2]
        if code in REPORT_LANGS:
            return code
    return "fr" if country == "FR" else "en"


# Event-detail phrases. The renamed event is language-neutral ("A → B"); every
# other detail is built from these so the chronology reads in the chosen tongue.
EVENT_PHRASES = {
    "en": {
        "today": "today",
        "absorbed": lambda who, a, b: f"absorbed {who} between {a} and {b}",
        "formed_from": lambda who: f"formed from {who}",
        "reestablished": lambda nom: f"re-established as {nom}",
        "split": lambda who: f"split into {who}",
        "merged_into": lambda who: f"merged into {who}",
        "ended": "no longer listed (no successor recorded)",
    },
    "fr": {
        "today": "aujourd'hui",
        "absorbed": lambda who, a, b: f"a absorbé {who} entre {a} et {b}",
        "formed_from": lambda who: f"issu de {who}",
        "reestablished": lambda nom: f"rétabli sous le nom de {nom}",
        "split": lambda who: f"scindé en {who}",
        "merged_into": lambda who: f"fusionné dans {who}",
        "ended": "n'apparaît plus (aucun successeur enregistré)",
    },
}


# --- What actually changed between two versions (issues #167, #169) ----------
# A version pair is not automatically an event. Two failures were measured on
# real data before this was written:
#
#   Labastida (01028, ES) records five versions and four "changes". All four are
#   the Spanish/Basque name alternating; the boundary never moves. Its area
#   varies by 0.115 % across the five INE vintages, on 38.6 km2, as the outline
#   is re-digitised from 32 vertices to 30.
#
#   Bad Berneck i.Fichtelgebirge (09472116, DE) records a whole version whose
#   entire difference is ONE DELETED SPACE: "i. Fichtelgebirge" became
#   "i.Fichtelgebirge" between two BKG vintages.
#
# So: normalise before deciding a name changed, and measure before claiming a
# boundary did. Both were previously decided by `prev["nom"] != p["nom"]`.

# Below this, two vintages of the same boundary are the same boundary. Chosen
# from the measurements above: re-digitisation moved Labastida by 0.115 % and
# Bad Berneck by 0.022 %. A real change is orders of magnitude larger -- a
# commune losing a hamlet loses percent, not fractions of one. A change BELOW
# this that is real would be a few hectares, which these sources do not resolve
# anyway; claiming it from vintage noise would be inventing it.
BOUNDARY_NOISE_PCT = 0.5


def _norm_name(nom: str | None) -> str:
    """Collapse the differences that are typography, not decisions."""
    import re as _re
    import unicodedata
    if not nom:
        return ""
    n = unicodedata.normalize("NFC", nom)
    n = n.replace("\u2019", "'").replace("\u2010", "-").replace("\u2011", "-")
    n = _re.sub(r"\s+", " ", n)
    n = _re.sub(r"\s*([.\-/'])\s*", r"\1", n)   # "i. Fichtel" == "i.Fichtel"
    return n.strip().casefold()


def name_delta(before: str | None, after: str | None) -> dict | None:
    """What changed in the name, at character level, or None if nothing did.

    `kind` is "renamed" when the difference survives normalisation and
    "respelled" when it does not -- a respelling is the source's typography
    changing between vintages, not an authority renaming anything.
    """
    import difflib
    b, a = before or "", after or ""
    if b == a:
        return None
    kind = "renamed" if _norm_name(b) != _norm_name(a) else "respelled"
    added, removed = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, b, a).get_opcodes():
        if tag in ("replace", "delete"):
            removed.append(b[i1:i2])
        if tag in ("replace", "insert"):
            added.append(a[j1:j2])
    return {"kind": kind, "before": b, "after": a,
            "added": [x for x in added if x], "removed": [x for x in removed if x]}


def _ring_area_km2(geom: dict | None) -> float | None:
    """Planar area of a GeoJSON polygon, good enough to answer 'did it move'.

    Deliberately not PostGIS: this is a comparison between two versions of the
    SAME outline at the same latitude, so the projection error cancels and a
    round trip to the database would buy nothing.
    """
    import math
    if not geom or not geom.get("coordinates"):
        return None
    t = geom.get("type")
    if t == "Polygon":
        rings = geom["coordinates"]
    elif t == "MultiPolygon":
        rings = [r for poly in geom["coordinates"] for r in poly]
    else:
        return None
    if not rings or len(rings[0]) < 4:
        return None
    total = 0.0
    for idx, ring in enumerate(rings):
        shoelace = abs(sum(ring[j][0] * ring[j + 1][1] - ring[j + 1][0] * ring[j][1]
                           for j in range(len(ring) - 1)) / 2)
        total += shoelace if idx == 0 else -shoelace
    lat = sum(pt[1] for pt in rings[0]) / len(rings[0])
    return abs(total) * (111.32 * 111.32 * math.cos(math.radians(lat)))


def _rings_area_km2(rings: list | None) -> float | None:
    """Same measurement as _ring_area_km2, over the report's `rings` shape.

    The report builds its own version list -- plain coordinate rings, not
    GeoJSON -- so it needs its own entry point. It is the same arithmetic on the
    same numbers; only the container differs.
    """
    import math
    if not rings or len(rings[0]) < 4:
        return None
    total = 0.0
    for idx, ring in enumerate(rings):
        shoelace = abs(sum(ring[j][0] * ring[j + 1][1] - ring[j + 1][0] * ring[j][1]
                           for j in range(len(ring) - 1)) / 2)
        total += shoelace if idx == 0 else -shoelace
    lat = sum(pt[1] for pt in rings[0]) / len(rings[0])
    return abs(total) * (111.32 * 111.32 * math.cos(math.radians(lat)))


def boundary_groups(versions: list[dict]) -> list[list[dict]]:
    """Consecutive versions that share a boundary, grouped into one panel.

    The founder's rule (issue #167): a change that is only a name shows NO
    boundary panel. The web page had this; the PDF did not, and drew Bad
    Berneck's single deleted space as two identical maps on a page a customer
    pays for.

    A version whose area cannot be measured starts its own group -- unknown is
    not unchanged.
    """
    groups: list[list[dict]] = []
    for v in versions:
        a_prev = _rings_area_km2(groups[-1][0].get("rings")) if groups else None
        a_here = _rings_area_km2(v.get("rings"))
        if groups and a_prev and a_here:
            pct = abs(a_here - a_prev) / a_prev * 100
            if pct <= BOUNDARY_NOISE_PCT:
                groups[-1].append(v)
                continue
        groups.append([v])
    return groups


def boundary_delta(before: dict | None, after: dict | None) -> dict | None:
    """Whether the outline moved, or only its digitisation did.

    Returns None when it cannot be told -- one of the versions has no geometry.
    "Unknown" and "unchanged" must not collapse: the first is a gap in what we
    hold, and reporting it as the second would be a claim we cannot support.
    """
    a, b = _ring_area_km2(before), _ring_area_km2(after)
    if a is None or b is None or a == 0:
        return None
    pct = (b - a) / a * 100
    return {"changed": abs(pct) > BOUNDARY_NOISE_PCT,
            "area_before_km2": round(a, 4), "area_after_km2": round(b, 4),
            "area_delta_pct": round(pct, 4)}


def annotate_changes(versions: list[dict]) -> list[dict]:
    """Tag each version with what changed since the previous one.

    The founder's rule (issue #167): a change that is only a name shows NO
    boundary panel. Rendering five near-identical outlines tells the reader the
    boundary moved five times; it moved none. So the decision is made here,
    once, and both the page and the PDF read it -- rather than each deciding
    for itself and drifting apart.

    `boundary` is None when it cannot be told. A caller must treat that as
    "unknown", never as "unchanged".
    """
    for i, v in enumerate(versions):
        props = v.setdefault("properties", {})
        if i == 0:
            props["change"] = None
            continue
        prev = versions[i - 1]
        nd = name_delta(prev.get("properties", {}).get("nom"), props.get("nom"))
        bd = boundary_delta(prev.get("geometry"), v.get("geometry"))
        props["change"] = {
            "name": nd,
            "boundary": bd,
            # What the renderer acts on. draw_boundary is False only when we
            # have measured that the outline did not move -- an unknown
            # boundary is still drawn, because hiding it would assert something
            # we did not check.
            "draw_boundary": True if bd is None else bool(bd["changed"]),
        }
    return versions


def _boundary_phrase(pct: float, fr: bool) -> str:
    """"limites agrandies de 12,9 %" -- agreement and preposition included.

    Written once because it appears in three branches, and a report that says
    "limites agrandie 12.9 %" to a notaire has lost the argument before the
    number is read.
    """
    if fr:
        verb = "agrandies" if pct > 0 else "réduites"
        return f"limites {verb} de {abs(pct):.1f} %".replace(".", ",")
    verb = "grew" if pct > 0 else "shrank"
    return f"boundary {verb} by {abs(pct):.1f}%"


def change_note(nd: dict | None, lang: str = "en", bd: dict | None = None) -> str | None:
    """One plain sentence saying what changed in a name.

    The chronology printed "A → B" and stopped. When the difference is one
    space -- Bad Berneck's entire 2023 event -- the reader sees two identical
    strings and cannot tell what happened. The founder asked "quel changement
    en 2023 ?" of a report that had just shown them that line.

    Plain text only, no substitution glyphs: this goes into a PDF drawn with
    Helvetica, where U+2423 OPEN BOX renders as a blank or a tofu box, which is
    the same failure one level down.
    """
    if not nd:
        return None
    fr = lang == "fr"

    # How much of the name survived. difflib on "Hotonnes" -> "Haut Valromey"
    # matches stray letters and reports `retiré tonn, s · ajouté aut_Valr, m, y`
    # -- true, useless, and it makes the document look broken. A character
    # delta earns its place only when the change is small enough that a reader
    # cannot see it unaided, which is the case it was built for: one space.
    # What decides whether a character delta helps is not its SIZE but how
    # FRAGMENTED it is. "Hotonnes" -> "Haut Valromey" makes difflib match stray
    # letters and report `retiré tonn, s · ajouté aut_Valr, m, y` -- true,
    # useless, and it makes the document look broken. "Labastida" ->
    # "Labastida / Bastida" is one clean insertion and worth showing, even
    # though it is longer.
    pieces = len(nd["added"]) + len(nd["removed"])
    readable = pieces <= 2

    def _describe(chunks: list[str]) -> str:
        parts = []
        for c in chunks:
            if c.strip() == "":
                n = len(c)
                if fr:
                    parts.append("une espace" if n == 1 else f"{n} espaces")
                else:
                    parts.append("one space" if n == 1 else f"{n} spaces")
            else:
                parts.append(f'"{c}"')
        return ", ".join(parts)

    bits = []
    if not readable:
        # A wholesale rename: name the two, do not dissect them.
        head_only = ("renommée" if fr else "renamed")
        if bd and bd.get("changed"):
            pct = bd["area_delta_pct"]
            return f"{head_only} · " + _boundary_phrase(pct, fr)
        if bd is None:
            return head_only + (" · limites non comparables" if fr
                                else " · boundary not comparable")
        return head_only + (" · limites inchangées" if fr else " · boundary unchanged")
    if nd["removed"]:
        bits.append((("retiré : " if fr else "removed: ")) + _describe(nd["removed"]))
    if nd["added"]:
        bits.append((("ajouté : " if fr else "added: ")) + _describe(nd["added"]))
    if nd["kind"] == "respelled":
        head = "orthographe de la source" if fr else "spelling in the source"
    elif bd and bd.get("changed"):
        # THE correction: the head used to assert "boundary unchanged" from the
        # name alone. On Haut Valromey -- four communes absorbed, 107.9 -> 121.8
        # km2 -- the report stated the opposite of what happened, in a document
        # sold on per-fact provenance.
        head = ("nom et limites · " if fr else "name and boundary · ") + \
            _boundary_phrase(bd["area_delta_pct"], fr)
    elif bd is None:
        head = ("nom modifié, limites non comparables" if fr
                else "name changed, boundary not comparable")
    else:
        head = "nom seul, limites inchangées" if fr else "name only, boundary unchanged"
    return head + (" · " + " · ".join(bits) if bits else "")


def derive_events(versions: list[dict], lang: str = "en") -> list[dict]:
    """Chronologie des ÉVÉNEMENTS d'une unité, dérivée de ses versions :
    renommages (avec les deux noms), fusions/absorptions, scissions, création,
    disparition — chacun daté quand la date est connue. `lang` localise les
    libellés d'événements (issue #79)."""
    ph = EVENT_PHRASES.get(lang, EVENT_PHRASES["en"])
    vs = [v["properties"] for v in versions]
    if not vs:
        return []
    code = vs[0]["code"]
    events: list[dict] = []
    # The boundary comparison needs the geometry, which lives on the feature
    # rather than on properties; derive_events only ever saw properties before.
    geoms = [v.get("geometry") for v in versions]
    for i, p in enumerate(vs):
        prev = vs[i - 1] if i > 0 else None
        contiguous = prev is not None and prev["valid_to"] == p["valid_from"]
        other_parents = sorted({c for c in (p["parents"] or []) if c != code})
        nd = name_delta(prev["nom"], p["nom"]) if contiguous else None
        if nd:
            # A respelling is the source's typography changing between vintages,
            # not an authority renaming anything -- see name_delta.
            bd = boundary_delta(geoms[i - 1], geoms[i]) if i else None
            events.append({"date": p["valid_from"], "type": nd["kind"],
                           "detail": f"{prev['nom']} → {p['nom']}",
                           "name_delta": nd, "boundary_delta": bd,
                           "change_note": change_note(nd, lang, bd)})
            if other_parents:
                events.append({"date": None, "type": "absorbed",
                               "detail": ph["absorbed"](', '.join(other_parents),
                                                        p['valid_from'],
                                                        p['valid_to'] or ph["today"])})
        elif other_parents and p["valid_from"] != "1943-01-01":
            events.append({"date": p["valid_from"],
                           "type": "merger" if code in (p["parents"] or []) or len(other_parents) > 1
                                   else "created",
                           "detail": ph["formed_from"](', '.join(sorted(set(p['parents']))))})
        elif other_parents:
            events.append({"date": None, "type": "absorbed",
                           "detail": ph["absorbed"](', '.join(other_parents),
                                                    p['valid_from'],
                                                    p['valid_to'] or ph["today"])})
        elif not contiguous and i > 0:
            events.append({"date": p["valid_from"], "type": "reestablished",
                           "detail": ph["reestablished"](p['nom'])})
        if p["valid_to"]:
            nxt = vs[i + 1] if i + 1 < len(vs) else None
            internal = nxt is not None and nxt["valid_from"] == p["valid_to"]
            children = sorted(set(p["children"] or []))
            others = [c for c in children if c != code]
            if internal:
                pass                                   # transition couverte au tour suivant
            elif len(children) > 1:
                events.append({"date": p["valid_to"], "type": "split",
                               "detail": ph["split"](', '.join(children))})
            elif others:
                events.append({"date": p["valid_to"], "type": "merged_into",
                               "detail": ph["merged_into"](others[0])})
            else:
                events.append({"date": p["valid_to"], "type": "ended",
                               "detail": ph["ended"]})
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

Any European municipality (EU + EFTA + UK + candidates), and New Zealand:

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
<footer>France at exact event dates back to 1870, Germany &amp; Netherlands from
yearly national editions, the rest of Europe via Eurostat LAU/NUTS, the UK at
legal dates (ONS), New Zealand from Stats NZ editions. Free during development — no key required yet
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
BULK_MAX = 200000


@app.get("/v1/export/ohm")
def export_ohm(
    request: Request,
    response: Response,
    country: str = Query(..., min_length=2, max_length=2),
    unit_type: str = Query(..., description="commune, departement, canton…"),
    date_from: date | None = Query(None, alias="from",
                                   description="Keep only versions active after this date"),
    date_to: date | None = Query(None, alias="to",
                                 description="Keep only versions active before this date"),
    full_geometry: bool = Query(False, description="Raw geometry (default: simplified)"),
    bulk: bool = Query(False, description="One-shot full export, no pagination (Enterprise tier)"),
    limit: int = Query(1000, ge=1, le=EXPORT_MAX),
    offset: int = Query(0, ge=0),
):
    """OHM-ready export: every unit VERSION as a GeoJSON Feature with
    `start_date`/`end_date` (OpenHistoricalMap conventions), the official
    reference (`ref:INSEE`…), a suggested `admin_level` and the source
    attribution. Meant to prepare OHM imports (issue #3): community consensus
    and the upload tooling stay on OHM's side.

    The small paginated export is free (for OHM contributors working in chunks);
    the one-shot `bulk=true` full dump is reserved to the Enterprise tier (#45)."""
    if bulk:
        require_tier(request, ("enterprise",))
        limit = BULK_MAX
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
# Report-range model (issue #45): Free = a lifetime trial of PREMIUM_FREE
# reports; Pro = PRO_MONTHLY reports PER MONTH; Enterprise = unlimited.
PREMIUM_FREE = int(os.environ.get("FREE_REPORTS", "10"))
PRO_MONTHLY = int(os.environ.get("PRO_REPORTS_PER_MONTH", "100"))
PRICING_URL = "https://www.confinia.io/pricing"

# --- Metered billing (pay-as-you-go), OFF unless the environment carries all
# three amounts. The amounts are configuration in the same sense secrets are:
# they never appear in this repository, only in the deployment environment
# (RULES 19). With them absent, every tier behaves exactly as before, which is
# what production runs while the model is rehearsed on the sandbox.
BILLING_FLOOR_CENTS = int(os.environ.get("BILLING_FLOOR_CENTS", "0"))
BILLING_PER_REPORT_CENTS = int(os.environ.get("BILLING_PER_REPORT_CENTS", "0"))
BILLING_CAP_CENTS = int(os.environ.get("BILLING_CAP_CENTS", "0"))
METERED = min(BILLING_FLOOR_CENTS, BILLING_PER_REPORT_CENTS, BILLING_CAP_CENTS) > 0


def monthly_charge_cents(used: int) -> int:
    """The whole tariff, in one place: floor, per-report, hard cap.

    One function so the page, the invoice and the API cannot disagree. The cap
    is a price CONTROL, not a tier boundary: past it every further report is
    free and must keep working -- metered billing changes what a bug costs, and
    a meter that keeps counting past the ceiling takes the wrong money from a
    professional customer exactly once, which is how many chances we get.
    """
    if not METERED:
        return 0
    return min(BILLING_CAP_CENTS,
               max(BILLING_FLOOR_CENTS, max(used, 0) * BILLING_PER_REPORT_CENTS))


EPOCH = date(1970, 1, 1)   # the free tier's lifetime bucket (period sentinel)


def _premium_caller(request: Request) -> tuple:
    """Resolve (tier, limit, period, caller) for the premium quota. Caller = a
    valid API key (enterprise unlimited → limit None; pro monthly) else a STABLE
    irreversible IP hash (never the IP). Free = lifetime bucket."""
    key = request.headers.get("x-api-key") or request.query_params.get("api_key")
    if key:
        with ops_cursor() as cur:
            cur.execute("SELECT active, tier FROM public.api_key WHERE key = %s::uuid", (key,))
            row = cur.fetchone()
        if row and row[0]:
            if row[1] == "enterprise":
                return ("enterprise", None, None, f"key:{key}")
            if row[1] == "pro":
                # Metered: no monthly ceiling on USE -- the ceiling is on the
                # CHARGE. limit=None with a period means "record, never refuse".
                return ("pro", None if METERED else PRO_MONTHLY,
                        date.today().replace(day=1), f"key:{key}")
            return ("free", PREMIUM_FREE, EPOCH, f"key:{key}")
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "anon")
    caller = "ip:" + hashlib.sha256(f"{VISITOR_SECRET}|premium|{ip}".encode()).hexdigest()[:32]
    return ("free", PREMIUM_FREE, EPOCH, caller)


def _premium_402(tier: str) -> None:
    if tier == "pro":
        raise HTTPException(402, {
            "detail": f"Monthly allowance of the Pro tier reached "
                      f"({PRO_MONTHLY} town reports per month).",
            "pricing": PRICING_URL,
            "note": "Resets on the 1st; re-downloading a town you already opened is free."})
    raise HTTPException(402, {
        "detail": f"The first {PREMIUM_FREE} town reports are free; beyond that, the Pro tier.",
        "pricing": PRICING_URL,
        "note": "Re-downloading a town you already opened is free."})


def premium_status(request: Request, unit: str | None = None) -> dict:
    """Read-only quota snapshot (NO consumption): {tier, used, limit, remaining,
    unlocked}. `unlocked` = this `unit` already counts for the caller this period,
    so re-fetching it is free (issue #83)."""
    tier, limit, period, caller = _premium_caller(request)
    if period is None:
        # Enterprise: unmetered as well as unlimited -- nothing is recorded.
        return {"tier": tier, "used": None, "limit": None,
                "remaining": "unlimited", "unlocked": True}
    with ops_cursor() as cur:
        cur.execute("SELECT count(*) FROM public.premium_seen WHERE caller=%s AND period=%s",
                    (caller, period))
        used = cur.fetchone()[0]
        unlocked = False
        if unit is not None:
            cur.execute("SELECT 1 FROM public.premium_seen "
                        "WHERE caller=%s AND period=%s AND unit=%s", (caller, period, unit))
            unlocked = cur.fetchone() is not None
    out = {"tier": tier, "used": used, "limit": limit,
           "remaining": "unlimited" if limit is None else max(limit - used, 0),
           "unlocked": unlocked}
    if tier == "pro" and METERED:
        charge = monthly_charge_cents(used)
        out["billing"] = {"reports": used, "charge_cents": charge,
                          "floor_cents": BILLING_FLOOR_CENTS,
                          "per_report_cents": BILLING_PER_REPORT_CENTS,
                          "cap_cents": BILLING_CAP_CENTS,
                          "capped": charge >= BILLING_CAP_CENTS}
    return out


def premium_gate(request: Request, unit: str) -> dict:
    """Consume one DISTINCT-artifact unit of premium quota (issue #83). A "report"
    is a town record (or a specific area-change query); re-using the same `unit`
    this period is FREE. Raises 402 when a NEW unit would exceed the allowance.
    Returns {tier, used, limit, remaining}."""
    tier, limit, period, caller = _premium_caller(request)
    if period is None:
        # Enterprise: unmetered as well as unlimited.
        return {"tier": tier, "used": None, "limit": None, "remaining": "unlimited"}
    with ops_cursor() as cur:
        cur.execute("SELECT 1 FROM public.premium_seen "
                    "WHERE caller=%s AND period=%s AND unit=%s", (caller, period, unit))
        seen = cur.fetchone() is not None
        cur.execute("SELECT count(*) FROM public.premium_seen WHERE caller=%s AND period=%s",
                    (caller, period))
        used = cur.fetchone()[0]
        if not seen:
            # A limit refuses; a meter records. Metered pro has NO limit -- past
            # the cap the charge stops growing but the report must keep working,
            # so there is deliberately no refusal path here for it.
            if limit is not None and used >= limit:
                _premium_402(tier)
            cur.execute("INSERT INTO public.premium_seen (caller, period, unit) "
                        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (caller, period, unit))
            used += 1
    out = {"tier": tier, "used": used, "limit": limit,
           "remaining": "unlimited" if limit is None else limit - used}
    if tier == "pro" and METERED:
        charge = monthly_charge_cents(used)
        out["billing"] = {"reports": used, "charge_cents": charge,
                          "cap_cents": BILLING_CAP_CENTS,
                          "capped": charge >= BILLING_CAP_CENTS}
    return out


def require_tier(request: Request, allowed: tuple) -> str:
    """Feature lock: raise 403 unless the caller's key is in an allowed tier.
    Used for capabilities reserved to paid tiers (e.g. one-shot bulk export)."""
    key = request.headers.get("x-api-key") or request.query_params.get("api_key")
    tier = "free"
    if key:
        with ops_cursor() as cur:
            cur.execute("SELECT tier FROM public.api_key WHERE key=%s::uuid AND active",
                        (key,))
            row = cur.fetchone()
            if row:
                tier = row[0]
    if tier not in allowed:
        raise HTTPException(403, {
            "detail": f"This feature requires the {' or '.join(allowed)} tier.",
            "pricing": PRICING_URL})
    return tier


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
    # Same area-change query (bbox + window) = same report → counts once (issue #83).
    quota = premium_gate(request, f"changes:{bbox}:{date_from}:{date_to}")
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


# Report chrome (issue #79): one localized label set per language. Reports drop
# the former "English / French" dual strings for a single label in the chosen tongue.
REPORT_LABELS = {
    "en": {
        "record": "Confinia · commune record",
        "versions_svg": lambda n: f"{n} recorded version(s) · generated by Confinia API v{APP_VERSION} · ",
        "versions_pdf": lambda n: f"{n} recorded version(s) · full lineage with per-fact provenance",
        "chronology": "Chronology",
        "boundaries": "Boundaries by period (same scale)",
        "sources": "Sources:",
        "annex": "Annex — provenance",
        "annex_lead": "Every fact in this report comes from one of these, in the edition named.",
        "annex_cols": "Source · licence · edition read · where to check it",
        "annex_gap": "gap: ",
        "annex_nov": "edition not recorded",
        "gained": "absorbed (light blue)",
        "gained_partial": lambda who: f"no polygon held for {who} — absorbed, not drawn",
        "no_geometry": "no geometry",
        "no_geometry_period": "no geometry for this period "
                              "(pre-1943 nomenclature without communal polygons)",
        "vintage": lambda d: f"geometry vintage {d}",
        "vintage_na": "geometry vintage n/a",
        "approx": " (approx.)",
        "today": "today",
        "population": "Population through time",
        "pop_inhabitants": "inhabitants",
        "pop_harmonised": lambda dt: f"census figures harmonised on the geography of {dt}",
        "pop_via": lambda c: f"series of {c}, this code's successor",
        "pop_events": "vertical marks = dated boundary events",
    },
    "fr": {
        "record": "Confinia · fiche communale",
        "versions_svg": lambda n: f"{n} version(s) enregistrée(s) · générée par l'API Confinia v{APP_VERSION} · ",
        "versions_pdf": lambda n: f"{n} version(s) enregistrée(s) · filiation complète avec provenance par fait",
        "chronology": "Chronologie",
        "boundaries": "Contours par période (même échelle)",
        "sources": "Sources :",
        "annex": "Annexe — provenance",
        "annex_lead": "Chaque fait de ce rapport provient de l'une d'elles, dans l'édition indiquée.",
        "annex_cols": "Source · licence · édition lue · où la vérifier",
        "annex_gap": "manque : ",
        "annex_nov": "édition non enregistrée",
        "gained": "absorbé (bleu clair)",
        "gained_partial": lambda who: f"aucun polygone pour {who} — absorbée(s), non dessinée(s)",
        "no_geometry": "aucune géométrie",
        "no_geometry_period": "aucune géométrie pour cette période "
                              "(nomenclature antérieure à 1943, sans polygone communal)",
        "vintage": lambda d: f"géométrie de {d}",
        "vintage_na": "géométrie n/d",
        "approx": " (approx.)",
        "today": "aujourd'hui",
        "population": "Population dans le temps",
        "pop_inhabitants": "habitants",
        "pop_harmonised": lambda dt: f"chiffres harmonisés sur la géographie du {dt}",
        "pop_via": lambda c: f"série de {c}, successeur de ce code",
        "pop_events": "repères verticaux = événements de frontière datés",
    },
}


# Neighbours drawn behind each boundary card (issue #96): a boundary means
# nothing against an empty background. Capped, because a dense urban commune can
# touch dozens of others and a card crowded with outlines helps nobody.
REPORT_MAX_NEIGHBOURS = int(os.environ.get("REPORT_MAX_NEIGHBOURS", "40"))


def _feature_rings(feature: dict) -> list:
    """Rings of a GeoJSON feature, Polygon or MultiPolygon, or [] without geometry."""
    g = feature.get("geometry")
    if not g:
        return []
    if g["type"] == "MultiPolygon":
        return [ring for poly in g["coordinates"] for ring in poly]
    if g["type"] == "Polygon":
        return list(g["coordinates"])
    return []


def _gained_rings(cur, parents: list, code: str, country: str, at, bbox) -> dict:
    """What a version ABSORBED, drawn from each predecessor's own polygon.

    Semantics from the LINEAGE, geometry only for drawing (issue #127). A naive
    ST_Difference between two versions of the same commune returns slivers: on
    Haut Valromey, 97 pieces each way, of which exactly one exceeds 0.1 km2 and
    the other 96 total 0.178 km2. They come from mismatched IGN vintages along a
    border that never moved. Painted, they would ring the whole commune in
    colour and tell the reader the boundary shifted everywhere.

    So we do not difference. `parents` says which communes were absorbed -- that
    is the fact -- and each one's own last polygon is what gets coloured.

    Returns the rings AND the predecessors we could not draw. Three of Haut
    Valromey's four parents have no geometry at all, so colouring only what we
    hold would show "gained Ruffieu" and imply "and nothing else", which is a
    false statement in a document sold on per-fact provenance. The caller must
    name them instead.
    """
    others = [c for c in (parents or []) if c != code]
    if not others or not bbox:
        return {"rings": [], "undrawable": others}
    w, s_, e, n = bbox
    cur.execute(
        "SELECT code, ST_AsGeoJSON(ST_Intersection(geom_simple, "
        "         ST_MakeEnvelope(%s,%s,%s,%s,4326)), 5) "
        "FROM commune_version "
        "WHERE country = %s AND code = ANY(%s) AND geom_simple IS NOT NULL "
        # The parent's perimeter AT ABSORPTION, not at the end of its own
        # record. Ruffieu was absorbed by Haut Valromey in 2016 and its own row
        # still runs to 2025 -- the registries disagree about when it stopped
        # existing. `valid_to <= at` silently dropped it, so the one parent we
        # could actually draw was reported as undrawable.
        "  AND valid_from <= %s "
        "ORDER BY code, valid_from DESC",
        (w, s_, e, n, country, others, at))
    rings, drawn = [], set()
    for c, gj in cur.fetchall():
        if c in drawn or not gj:          # the LAST version of each parent only
            continue
        drawn.add(c)
        rings.extend(_rings_of_geojson(gj))
    return {"rings": rings, "undrawable": sorted(set(others) - drawn)}


def _rings_of_geojson(gj: str) -> list:
    g = json.loads(gj)
    if g.get("type") == "Polygon":
        return g["coordinates"]
    if g.get("type") == "MultiPolygon":
        return [r for poly in g["coordinates"] for r in poly]
    return []


def _neighbour_rings(cur, code: str, country: str, at, bbox) -> list:
    """Rings of the units touching `code` AT THAT PERIOD's date, clipped to the
    frame. Taking today's neighbours for a 1950 outline would be a silent
    anachronism, so the validity window is the version's own, not now."""
    if not bbox:
        return []
    w, s_, e, n = bbox
    mx, my = (e - w) * 0.08 or 0.01, (n - s_) * 0.08 or 0.01
    cur.execute(
        "WITH target AS ("
        "  SELECT geom_simple g FROM commune_version "
        "  WHERE unit_type = ANY(%s) AND country = %s AND code = %s "
        "    AND valid_from <= %s AND valid_to > %s AND geom_simple IS NOT NULL "
        "  LIMIT 1) "
        "SELECT ST_AsGeoJSON(ST_Intersection(cv.geom_simple, "
        "         ST_MakeEnvelope(%s,%s,%s,%s,4326)), 5) "
        "FROM commune_version cv, target t "
        "WHERE cv.unit_type = ANY(%s) AND cv.country = %s AND cv.code <> %s "
        "  AND cv.valid_from <= %s AND cv.valid_to > %s "
        "  AND cv.geom_simple IS NOT NULL "
        "  AND cv.geom_simple && ST_MakeEnvelope(%s,%s,%s,%s,4326) "
        "  AND ST_Intersects(cv.geom_simple, t.g) "
        "LIMIT %s",
        (list(MUNICIPAL_TYPES), country, code, at, at,
         w - mx, s_ - my, e + mx, n + my,
         list(MUNICIPAL_TYPES), country, code, at, at,
         w - mx, s_ - my, e + mx, n + my, REPORT_MAX_NEIGHBOURS))
    rings = []
    for (g,) in cur.fetchall():
        if not g:
            continue
        gj = json.loads(g)
        t = gj.get("type")
        if t == "MultiPolygon":
            for poly in gj["coordinates"]:
                rings.extend(poly)
        elif t == "Polygon":
            rings.extend(gj["coordinates"])
        elif t == "GeometryCollection":
            for sub in gj.get("geometries", []):
                if sub["type"] == "Polygon":
                    rings.extend(sub["coordinates"])
                elif sub["type"] == "MultiPolygon":
                    for poly in sub["coordinates"]:
                        rings.extend(poly)
    return rings


def _report_data(code: str, country: str, lang: str = "en") -> dict:
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
        cur.execute("SELECT source, attribution, license, source_url "
                    "FROM public.data_source")
        registry = {r[0]: {"attribution": r[1], "license": r[2], "url": r[3]}
                    for r in cur.fetchall()}
        src_info = {k: (v["attribution"], v["license"]) for k, v in registry.items()}
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
    bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else None
    # One extra spatial query per period: reports are premium and metered per
    # town, so this stays bounded by REPORT_MAX_VERSIONS.
    with cursor() as cur:
        for v in versions:
            v["neighbours"] = (_neighbour_rings(cur, code, country,
                                                v["valid_from"], bbox)
                               if v["rings"] else [])
            # What this version absorbed, from the lineage (issue #127).
            gained = _gained_rings(cur, v.get("parents"), code, country,
                                   v["valid_from"], bbox) if v["rings"] else {}
            v["gained"] = gained.get("rings", [])
            v["gained_undrawable"] = gained.get("undrawable", [])
    attributions = sorted({src_info[v["source"]] for v in versions
                           if v["source"] in src_info})
    pop = population_series(code, country, lang)
    if pop and pop["source"] in src_info:
        # The census source must appear in the report's attribution block too.
        attributions = sorted(set(attributions) | {src_info[pop["source"]]})
    annotate_changes(feats)
    return {"code": code, "country": country, "lang": lang, "versions": versions,
            "source_annex": build_source_annex(versions, pop, registry, lang),
            "events": derive_events(feats, lang),
            "bbox": bbox,
            "population": pop,
            "attributions": attributions}


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


def _group_period_str(g: list[dict], lang: str = "en") -> str:
    """The span a single panel covers, when it stands for several versions."""
    today = REPORT_LABELS.get(lang, REPORT_LABELS["en"])["today"]
    last = g[-1]
    vt = today if last["valid_to"] == FAR_FUTURE else last["valid_to"].isoformat()
    return f"{g[0]['valid_from'].isoformat()} → {vt}"


def _group_name(g: list[dict]) -> str:
    """Every distinct name the panel covers -- the names DID change."""
    seen, out = set(), []
    for v in g:
        if v["nom"] not in seen:
            seen.add(v["nom"])
            out.append(v["nom"])
    return " · ".join(out)


GAP_PHRASES = {
    "en": {"unregistered": "not in the source registry",
           "no_url": "no published reference"},
    "fr": {"unregistered": "absente du registre des sources",
           "no_url": "aucune référence publiée"},
}


def build_source_annex(versions: list[dict], pop: dict | None,
                       registry: dict, lang: str = "en") -> list[dict]:
    """One row per source this report actually used (issue #90).

    Provenance per fact is what this product sells, and the report is where a
    customer meets that claim. A flat footer -- "INSEE, Licence Ouverte 2.0" --
    says WHO the sources are and nothing about which fact came from which, nor
    which edition of it.

    Three things each row must carry, and the third is what makes the other two
    checkable:

      the licence, so the reader knows what they may do with it;
      the VINTAGE WE READ, never "latest" -- these files get republished, and a
        reader verifying next year must land on what we read, not what replaced
        it;
      a URL that resolves.

    Where any of those is missing, the row SAYS SO. A blank reads as an
    oversight; "no published reference for this source" is information, and it
    is the honest half of a provenance claim. Same doctrine as #167: state the
    gap rather than imply completeness.
    """
    ph = GAP_PHRASES.get(lang, GAP_PHRASES["en"])
    used: dict[str, set] = {}
    for v in versions:
        if v.get("source"):
            used.setdefault(v["source"], set())
            if v.get("vintage"):
                used[v["source"]].add(v["vintage"].isoformat())
    if pop and pop.get("source"):
        used.setdefault(pop["source"], set())

    annex = []
    for key in sorted(used):
        meta = registry.get(key) or {}
        gaps = []
        if not meta:
            gaps.append(ph["unregistered"])
        if not meta.get("url"):
            gaps.append(ph["no_url"])
        # A missing edition is NOT repeated here: the vintage line already says
        # it, in the reader's language, and printing it twice reads as two
        # separate problems.
        annex.append({
            "source": key,
            "attribution": meta.get("attribution") or key,
            "license": meta.get("license"),
            "url": meta.get("url"),
            "vintages": sorted(used[key]),
            "gap": " · ".join(gaps) or None,
        })
    return annex


def _period_str(v, lang: str = "en") -> str:
    today = REPORT_LABELS.get(lang, REPORT_LABELS["en"])["today"]
    vt = today if v["valid_to"] == FAR_FUTURE else v["valid_to"].isoformat()
    return f"{v['valid_from'].isoformat()} → {vt}"


def _pop_layout(pop: dict, events: list, w: float, h: float) -> dict | None:
    """Geometry of the population curve, shared by the SVG and PDF renderers
    (issue #88). Returns points in a top-left origin box of (w, h), plus the
    dated events placed on the same x axis — the breaks the curve cannot
    explain on its own. y starts at 0 so amplitudes are not exaggerated."""
    series = (pop or {}).get("series") or []
    if len(series) < 2:
        return None
    years = [s["year"] for s in series]
    values = [s["population"] for s in series]
    y0, y1 = min(years), max(years)
    vmax = max(values) or 1
    span = (y1 - y0) or 1
    px = lambda yr: (yr - y0) / span * w
    py = lambda v: h - (v / vmax) * h
    pts = [(px(s["year"]), py(s["population"])) for s in series]
    marks = []
    for ev in events:
        dt = ev.get("date")
        if not dt:
            continue
        yr = int(str(dt)[:4])
        if y0 <= yr <= y1:
            marks.append({"x": px(yr), "year": yr, "type": ev.get("type", "")})
    return {"points": pts, "marks": marks, "vmax": vmax,
            "first": (y0, values[0]), "last": (y1, values[-1])}


def _report_svg(d: dict) -> str:
    import html as html_mod
    esc = html_mod.escape
    lab = REPORT_LABELS.get(d.get("lang", "en"), REPORT_LABELS["en"])
    W, PAD = 840, 28
    cur_name = d["versions"][-1]["nom"]
    parts, y = [], PAD + 8
    def text(x, yy, s, size=13, weight="normal", fill="#1a2333", anchor="start"):
        parts.append(f'<text x="{x}" y="{yy}" font-size="{size}" font-family="system-ui,sans-serif" '
                     f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{esc(s)}</text>')
    text(PAD, y + 10, lab["record"], 13, fill="#5b6b85"); y += 26
    text(PAD, y + 12, f"{cur_name} ({d['code']}, {d['country']})", 26, "bold"); y += 40
    text(PAD, y, lab["versions_svg"](len(d['versions']))
                 + f"https://www.confinia.io/commune/{d['code']}", 11, fill="#5b6b85"); y += 24
    text(PAD, y, lab["chronology"], 15, "bold"); y += 8
    for ev in d["events"][:60]:
        y += 17
        pre = f"{ev['date']} · " if ev.get("date") else ""
        text(PAD + 8, y, f"• {pre}{ev['detail']}"[:130], 12)
        # Issue #169: "A → B" is unreadable when the difference is one space.
        if ev.get("change_note"):
            y += 13
            text(PAD + 20, y, ev["change_note"][:120], 10, fill="#7d8ba3")
    y += 26
    # Population curve (issue #88): the one chart that shows both the series and
    # the boundary events that break it. Drawn only when we actually have data.
    CH_W, CH_H = W - 2 * PAD - 60, 150
    lay = _pop_layout(d.get("population"), d["events"], CH_W, CH_H)
    if lay:
        pop = d["population"]
        text(PAD, y, lab["population"], 15, "bold"); y += 10
        ox, oy = PAD + 46, y + 6
        # dated events first, so the curve reads above them
        for m in lay["marks"]:
            parts.append(f'<line x1="{ox + m["x"]:.1f}" y1="{oy}" x2="{ox + m["x"]:.1f}" '
                         f'y2="{oy + CH_H}" stroke="#e6b800" stroke-width="1" '
                         'stroke-dasharray="3 3"/>')
            text(ox + m["x"], oy - 3, str(m["year"]), 8, fill="#a8862a", anchor="middle")
        parts.append(f'<line x1="{ox}" y1="{oy + CH_H}" x2="{ox + CH_W}" y2="{oy + CH_H}" '
                     'stroke="#d5dbe6" stroke-width="1"/>')
        path = " ".join(("M" if i == 0 else "L") + f" {ox + x:.1f} {oy + yy:.1f}"
                        for i, (x, yy) in enumerate(lay["points"]))
        parts.append(f'<path d="{path}" fill="none" stroke="#3a5f95" stroke-width="2"/>')
        for x, yy in (lay["points"][0], lay["points"][-1]):
            parts.append(f'<circle cx="{ox + x:.1f}" cy="{oy + yy:.1f}" r="3" fill="#3a5f95"/>')
        # direct labels only at the ends (no number on every point)
        fy, fv = lay["first"]; ly_, lv = lay["last"]
        text(ox - 6, oy + lay["points"][0][1] + 4, f"{fv:,}".replace(",", " "), 9,
             fill="#5b6b85", anchor="end")
        text(ox + CH_W + 6, oy + lay["points"][-1][1] + 4, f"{lv:,}".replace(",", " "), 9,
             fill="#5b6b85", anchor="start")
        text(ox, oy + CH_H + 12, str(fy), 9, fill="#5b6b85")
        text(ox + CH_W, oy + CH_H + 12, str(ly_), 9, fill="#5b6b85", anchor="end")
        text(ox, oy + CH_H + 24, lab["pop_events"], 8, fill="#8a94a6")
        y = oy + CH_H + 38
        # Provenance, never hidden: harmonisation date and successor substitution.
        if pop.get("harmonised_on"):
            text(PAD, y, "· " + lab["pop_harmonised"](pop["harmonised_on"]), 9, fill="#8a94a6")
            y += 12
        if pop.get("via_successor"):
            text(PAD, y, "· " + lab["pop_via"](pop["code"]), 9, fill="#8a94a6")
            y += 12
        y += 14
    text(PAD, y, lab["boundaries"], 15, "bold"); y += 12
    CELL_W, CELL_H, DRAW_H = (W - 2 * PAD) // 2, 320, 250
    # Issue #167: one panel per DISTINCT boundary, not per version. Drawing a
    # panel per version told the reader the outline moved every time a name did.
    groups = boundary_groups(d["versions"])
    for i, g in enumerate(groups):
        v = g[0]
        col, row_y = i % 2, y + (i // 2) * CELL_H
        ox = PAD + col * CELL_W
        parts.append(f'<rect x="{ox + 6}" y="{row_y + 6}" width="{CELL_W - 12}" height="{CELL_H - 12}" '
                     'fill="none" stroke="#d5dbe6" rx="8"/>')
        if v["rings"] and d["bbox"]:
            box = (ox + 20, row_y + 18, CELL_W - 40, DRAW_H - 24)
            def draw(rings):
                return " ".join(
                    "M " + " L ".join(f"{px:.1f} {py:.1f}" for px, py in
                                      _ring_points(ring, d["bbox"], *box)) + " Z"
                    for ring in rings)
            # Neighbours first, subdued, so the target reads as the subject.
            # Clipped to the cell: a neighbour extends past the frame by design.
            if v.get("neighbours"):
                cid = f"cell{i}"
                parts.append(f'<clipPath id="{cid}"><rect x="{box[0]}" y="{box[1]}" '
                             f'width="{box[2]}" height="{box[3]}"/></clipPath>')
                parts.append(f'<g clip-path="url(#{cid})"><path d="{draw(v["neighbours"])}" '
                             'fill="#eef1f6" stroke="#c9d2e0" stroke-width="0.6" '
                             'fill-rule="evenodd"/></g>')
            # Absorbed predecessors, LIGHT BLUE, under the outline (issue #127).
            # Drawn from each parent's own polygon, never from a difference:
            # differencing two vintages of the same commune returns slivers --
            # 97 of them on this very example -- and colour turns noise into an
            # assertion.
            if v.get("gained"):
                parts.append(f'<path d="{draw(v["gained"])}" fill="#a8d5f2" '
                             'stroke="#5a9fd4" stroke-width="0.8" fill-rule="evenodd"/>')
            parts.append(f'<path d="{draw(v["rings"])}" fill="#dbe7fb" stroke="#3a5f95" '
                         'stroke-width="1.2" fill-rule="evenodd" fill-opacity="0.55"/>')
        else:
            text(ox + CELL_W / 2, row_y + DRAW_H / 2, lab["no_geometry"], 12, fill="#8a94a6", anchor="middle")
        text(ox + CELL_W / 2, row_y + DRAW_H + 16, _group_name(g), 14, "bold", anchor="middle")
        text(ox + CELL_W / 2, row_y + DRAW_H + 34, _group_period_str(g, d.get("lang", "en")), 12,
             fill="#5b6b85", anchor="middle")
        vin = lab["vintage"](v['vintage'].isoformat()) if v["vintage"] else lab["vintage_na"]
        if v["approx"]:
            vin += lab["approx"]
        text(ox + CELL_W / 2, row_y + DRAW_H + 50, vin, 10, fill="#8a94a6", anchor="middle")
        if v.get("gained"):
            text(ox + CELL_W / 2, row_y + DRAW_H + 64, lab["gained"], 9,
                 fill="#5a9fd4", anchor="middle")
        if v.get("gained_undrawable"):
            # Naming them is the point: colouring only what we hold would show
            # "gained Ruffieu" and imply "and nothing else".
            text(ox + CELL_W / 2, row_y + DRAW_H + 76,
                 lab["gained_partial"](", ".join(v["gained_undrawable"]))[:80], 9,
                 fill="#a05a2c", anchor="middle")
    # Groups, not versions: sizing on the version count leaves a blank half-page
    # for every panel the grouping removed.
    y += ((len(groups) + 1) // 2) * CELL_H + 20
    # Annex (issue #90): the flat footer named WHO the sources are and nothing
    # about which edition was read or where to check it. Provenance per fact is
    # what this product sells; a reader must be able to take any statement here
    # and go verify it upstream without taking our word for anything.
    y += 10
    text(PAD, y, lab["annex"], 13, "bold"); y += 16
    text(PAD, y, lab["annex_lead"], 10, fill="#5b6b85"); y += 14
    text(PAD, y, lab["annex_cols"], 9, fill="#8a94a6"); y += 6
    for row in d.get("source_annex", []):
        y += 16
        head = row["attribution"]
        if row.get("license"):
            head += f" · {row['license']}"
        text(PAD + 8, y, head[:120], 10)
        y += 13
        vint = ", ".join(row["vintages"]) if row["vintages"] else lab["annex_nov"]
        text(PAD + 18, y, vint, 9, fill="#5b6b85")
        if row.get("url"):
            y += 12
            text(PAD + 18, y, row["url"][:110], 9, fill="#3a5f95")
        if row.get("gap"):
            # An explicit gap beats a blank: a blank reads as an oversight.
            y += 12
            text(PAD + 18, y, lab["annex_gap"] + row["gap"], 9, fill="#a05a2c")
    y += 26
    text(PAD, y, lab["sources"], 11, "bold", "#5b6b85"); y += 4
    for attr, lic in d["attributions"]:
        y += 15
        text(PAD + 8, y, f"{attr} · {lic}", 10, fill="#5b6b85")
    y += 30
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{y}" '
            f'viewBox="0 0 {W} {y}"><rect width="{W}" height="{y}" fill="#ffffff"/>'
            + "".join(parts) + "</svg>")


def _report_pdf(d: dict) -> bytes:
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas
    lab = REPORT_LABELS.get(d.get("lang", "en"), REPORT_LABELS["en"])
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
            c.drawString(PAD, yy, f"{attr} · {lic}"[:110])
            yy -= 9
        c.drawRightString(W - PAD, 26, f"Confinia API v{APP_VERSION} · www.confinia.io")
    cur_name = d["versions"][-1]["nom"]
    c.setFillColorRGB(.36, .42, .52); c.setFont("Helvetica", 10)
    c.drawString(PAD, H - 60, lab["record"])
    c.setFillColorRGB(.1, .14, .2); c.setFont("Helvetica-Bold", 22)
    c.drawString(PAD, H - 88, f"{cur_name} ({d['code']}, {d['country']})")
    c.setFont("Helvetica", 9); c.setFillColorRGB(.36, .42, .52)
    c.drawString(PAD, H - 104, lab["versions_pdf"](len(d['versions'])))
    y = H - 136
    c.setFont("Helvetica-Bold", 13); c.setFillColorRGB(.1, .14, .2)
    c.drawString(PAD, y, lab["chronology"]); y -= 18
    c.setFont("Helvetica", 9.5)
    for ev in d["events"]:
        if y < 70:
            footer(); c.showPage(); y = H - 70; c.setFont("Helvetica", 9.5)
        pre = f"{ev['date']} · " if ev.get("date") else ""
        c.setFillColorRGB(.1, .14, .2)
        c.drawString(PAD + 6, y, f"• {pre}{ev['detail']}"[:118]); y -= 14
        # Issue #169: without this the reader sees two identical-looking names
        # and has to ask what changed -- which the founder did, of this report.
        if ev.get("change_note"):
            c.setFont("Helvetica", 8); c.setFillColorRGB(.45, .5, .58)
            c.drawString(PAD + 18, y, ev["change_note"][:110]); y -= 12
            c.setFont("Helvetica", 9.5)
    # Population curve (issue #88). PDF is y-up: Y = top - py.
    CH_W, CH_H = W - 2 * PAD - 60, 130
    lay = _pop_layout(d.get("population"), d["events"], CH_W, CH_H)
    if lay:
        pop = d["population"]
        if y < 220:
            footer(); c.showPage(); y = H - 70
        c.setFont("Helvetica-Bold", 13); c.setFillColorRGB(.1, .14, .2)
        c.drawString(PAD, y, lab["population"]); y -= 16
        ox, top = PAD + 46, y
        c.setStrokeColorRGB(.90, .72, .0); c.setLineWidth(.6); c.setFont("Helvetica", 6.5)
        for m in lay["marks"]:
            c.setDash(2, 2)
            c.line(ox + m["x"], top - CH_H, ox + m["x"], top)
            c.setDash()
            c.setFillColorRGB(.66, .53, .16)
            c.drawCentredString(ox + m["x"], top + 3, str(m["year"]))
        c.setStrokeColorRGB(.84, .86, .90); c.setLineWidth(.8)
        c.line(ox, top - CH_H, ox + CH_W, top - CH_H)
        c.setStrokeColorRGB(.23, .37, .58); c.setLineWidth(1.6)
        path = c.beginPath()
        px0, py0 = lay["points"][0]
        path.moveTo(ox + px0, top - py0)
        for px_, py_ in lay["points"][1:]:
            path.lineTo(ox + px_, top - py_)
        c.drawPath(path)
        c.setFillColorRGB(.23, .37, .58)
        for px_, py_ in (lay["points"][0], lay["points"][-1]):
            c.circle(ox + px_, top - py_, 2, stroke=0, fill=1)
        fy, fv = lay["first"]; ly_, lv = lay["last"]
        c.setFont("Helvetica", 7); c.setFillColorRGB(.36, .42, .52)
        c.drawRightString(ox - 5, top - lay["points"][0][1] - 2, f"{fv:,}".replace(",", " "))
        c.drawString(ox + CH_W + 5, top - lay["points"][-1][1] - 2, f"{lv:,}".replace(",", " "))
        c.drawString(ox, top - CH_H - 10, str(fy))
        c.drawRightString(ox + CH_W, top - CH_H - 10, str(ly_))
        c.setFont("Helvetica", 6.5); c.setFillColorRGB(.54, .58, .65)
        c.drawString(ox, top - CH_H - 20, lab["pop_events"])
        y = top - CH_H - 32
        if pop.get("harmonised_on"):
            c.drawString(PAD, y, "· " + lab["pop_harmonised"](pop["harmonised_on"])); y -= 10
        if pop.get("via_successor"):
            c.drawString(PAD, y, "· " + lab["pop_via"](pop["code"])); y -= 10
    footer(); c.showPage()
    per_page, slot_h = 2, (H - 140) / 2
    for i, g in enumerate(boundary_groups(d["versions"])):
        v = g[0]
        slot = i % per_page
        if slot == 0 and i:
            footer(); c.showPage()
        top = H - 60 - slot * slot_h
        c.setFont("Helvetica-Bold", 12); c.setFillColorRGB(.1, .14, .2)
        c.drawString(PAD, top, f"{_group_name(g)} · {_group_period_str(g, d.get('lang', 'en'))}")
        vin = lab["vintage"](v['vintage'].isoformat()) if v["vintage"] else lab["vintage_na"]
        if v["approx"]:
            vin += lab["approx"]
        c.setFont("Helvetica", 8.5); c.setFillColorRGB(.45, .5, .58)
        c.drawString(PAD, top - 13, vin)
        if v["rings"] and d["bbox"]:
            draw_w, draw_h, top_draw = W - 2 * PAD, slot_h - 70, top - 30

            def ring_path(ring):
                # _ring_points renvoie du y-vers-le-bas (convention SVG) dans
                # [0..w]x[0..h] ; PDF est y-vers-le-haut : Y = haut - py.
                pts = _ring_points(ring, d["bbox"], 0, 0, draw_w, draw_h)
                p = c.beginPath()
                p.moveTo(PAD + pts[0][0], top_draw - pts[0][1])
                for px, py in pts[1:]:
                    p.lineTo(PAD + px, top_draw - py)
                p.close()
                return p

            # Neighbours first, subdued, clipped to the slot so a neighbour
            # extending past the frame does not bleed into the next card
            # (issue #96). The target is drawn last, so it stays the subject.
            if v.get("neighbours"):
                c.saveState()
                clip = c.beginPath()
                clip.rect(PAD, top_draw - draw_h, draw_w, draw_h)
                c.clipPath(clip, stroke=0, fill=0)
                c.setFillColorRGB(.93, .95, .97); c.setStrokeColorRGB(.79, .82, .88)
                c.setLineWidth(.4)
                for ring in v["neighbours"]:
                    c.drawPath(ring_path(ring), stroke=1, fill=1)
                c.restoreState()
            # Absorbed predecessors, LIGHT BLUE, under the outline (issue #127),
            # drawn from each parent's own polygon rather than a difference: two
            # vintages of the same commune differ by slivers, 97 of them here,
            # and colour would turn that noise into an assertion.
            if v.get("gained"):
                c.setFillColorRGB(.66, .84, .95); c.setStrokeColorRGB(.35, .62, .83)
                c.setLineWidth(.6)
                for ring in v["gained"]:
                    c.drawPath(ring_path(ring), stroke=1, fill=1)
            c.setFillColorRGB(.86, .91, .98); c.setStrokeColorRGB(.23, .37, .58)
            c.setLineWidth(1)
            c.setFillAlpha(0.55)
            for ring in v["rings"]:
                c.drawPath(ring_path(ring), stroke=1, fill=1)
            c.setFillAlpha(1)
            legend = []
            if v.get("gained"):
                legend.append(lab["gained"])
            if v.get("gained_undrawable"):
                # Naming them is the point: colouring only what we hold would
                # show "gained Ruffieu" and imply "and nothing else".
                legend.append(lab["gained_partial"](", ".join(v["gained_undrawable"])))
            if legend:
                c.setFont("Helvetica", 7.5); c.setFillColorRGB(.35, .45, .58)
                c.drawString(PAD, top - 26, " · ".join(legend)[:150])
        else:
            c.setFont("Helvetica", 10); c.setFillColorRGB(.54, .58, .65)
            c.drawCentredString(W / 2, top - slot_h / 2, lab["no_geometry_period"])
    # Annex (issue #90), its own page: the per-page footer names WHO the sources
    # are, which is not a provenance claim. A reader must be able to take any
    # statement in this document and go check it upstream -- which needs the
    # EDITION we read (these files get republished) and a reference that
    # resolves. Where either is missing the row says so: a blank reads as an
    # oversight, an explicit gap is information.
    annex = d.get("source_annex") or []
    if annex:
        footer(); c.showPage()
        y = H - 70
        c.setFont("Helvetica-Bold", 14); c.setFillColorRGB(.1, .14, .2)
        c.drawString(PAD, y, lab["annex"]); y -= 18
        c.setFont("Helvetica", 9); c.setFillColorRGB(.36, .42, .52)
        c.drawString(PAD, y, lab["annex_lead"][:120]); y -= 12
        c.setFont("Helvetica", 8); c.setFillColorRGB(.55, .6, .68)
        c.drawString(PAD, y, lab["annex_cols"][:120]); y -= 18
        for row in annex:
            if y < 90:
                footer(); c.showPage(); y = H - 70
            head = row["attribution"] + (f" · {row['license']}" if row.get("license") else "")
            c.setFont("Helvetica-Bold", 9.5); c.setFillColorRGB(.1, .14, .2)
            c.drawString(PAD, y, head[:105]); y -= 12
            c.setFont("Helvetica", 8.5); c.setFillColorRGB(.36, .42, .52)
            vint = ", ".join(row["vintages"]) if row["vintages"] else lab["annex_nov"]
            c.drawString(PAD + 12, y, vint[:100]); y -= 11
            if row.get("url"):
                c.setFillColorRGB(.23, .37, .58)
                c.drawString(PAD + 12, y, row["url"][:100]); y -= 11
            if row.get("gap"):
                c.setFillColorRGB(.63, .35, .17)
                c.drawString(PAD + 12, y, (lab["annex_gap"] + row["gap"])[:100]); y -= 11
            y -= 6
    footer(); c.showPage(); c.save()
    return buf.getvalue()


@app.get("/v1/communes/{code}/report.svg")
def commune_report_svg(request: Request, code: str, country: str = "FR",
                       lang: str | None = Query(None,
                           description="Report language (fr/en); default: French for FR, English otherwise")):
    """PREMIUM — rapport SVG : traçabilité complète + contours par période.
    Même quota gratuit que /v1/changes (9 rapports), palier Pro ensuite."""
    country = country.upper()
    quota = premium_gate(request, f"{country}/{code}")   # distinct-town (issue #83)
    svg = _report_svg(_report_data(code, country, resolve_lang(lang, country)))
    return Response(svg, media_type="image/svg+xml", headers={
        "Content-Disposition": f'inline; filename="confinia-{country}-{code}.svg"',
        "Cache-Control": "no-store",
        "X-Premium-Remaining": str(quota.get("remaining"))})


@app.get("/v1/communes/{code}/report.pdf")
def commune_report_pdf(request: Request, code: str, country: str = "FR",
                       lang: str | None = Query(None,
                           description="Report language (fr/en); default: French for FR, English otherwise")):
    """PREMIUM — le même rapport en PDF (document citable)."""
    country = country.upper()
    quota = premium_gate(request, f"{country}/{code}")   # distinct-town (issue #83)
    pdf = _report_pdf(_report_data(code, country, resolve_lang(lang, country)))
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="confinia-{country}-{code}.pdf"',
        "Cache-Control": "no-store",
        "X-Premium-Remaining": str(quota.get("remaining"))})


@app.get("/v1/pricing")
def pricing_config():
    """The offer, as the deployment is actually configured (RULES 19).

    Pages must not hardcode an amount: the founder found the account page
    advertising the OLD flat price while the environment metered the new
    model -- the page and the eventual invoice disagreed. Amounts live in the
    environment; this endpoint is how a page learns them. Flat deployments
    return only the mode, and the page then names no number at all -- the
    checkout is the single source of the flat price."""
    if not METERED:
        return {"mode": "flat"}
    return {"mode": "metered",
            "floor_cents": BILLING_FLOOR_CENTS,
            "per_report_cents": BILLING_PER_REPORT_CENTS,
            "cap_cents": BILLING_CAP_CENTS}


@app.get("/v1/reports/quota")
def reports_quota(request: Request, country: str = "FR", code: str | None = None):
    """Read-only report allowance for the caller (issue #83): {tier, used, limit,
    remaining, unlocked}. `unlocked` tells whether the given town already counts
    (free re-download). Powers the "N of 10 towns left" counter on the commune page."""
    unit = f"{country.upper()}/{code}" if code else None
    return premium_status(request, unit)


# Feature entitlements per tier, for the account page (issue #73). Static,
# public wording — mirrors /pricing; a "report" is a TOWN, counted once (issue #83).
PLAN_FEATURES = {
    "free": ["All point-in-time lookups & unit history",
             "Interactive demo & commune pages",
             f"{PREMIUM_FREE} town reports to try (a town counts once — re-downloads free)",
             "Attribution of sources required", "Community support"],
    "pro": ["Everything in Free",
            f"Up to {PRO_MONTHLY} town reports per month (re-downloads free)",
            "Area-change reports & commune SVG/PDF records", "Passage tables",
            "R & Python clients", "Versioned endpoints", "Email support"],
    "enterprise": ["Everything in Pro", "Unlimited town reports (fair use)",
                   "One-shot bulk exports (full-country dumps)",
                   "Priority country coverage", "Premium datasets as they land",
                   "SLA 99.9%", "Invoice billing"],
}


@app.get("/v1/usage")
def account_usage(request: Request):
    """Read-only usage summary for the caller's key (issue #73): tier, plan
    features and the current town-report consumption (this month for Pro,
    lifetime trial for Free, unlimited for Enterprise). Does NOT consume quota."""
    key = request.headers.get("x-api-key") or request.query_params.get("api_key")
    if not key:
        raise HTTPException(401, "API key required (X-API-Key or ?api_key=).")
    with ops_cursor() as cur:
        cur.execute("SELECT active, tier FROM public.api_key WHERE key = %s::uuid", (key,))
        row = cur.fetchone()
    if not row or not row[0]:
        raise HTTPException(404, "Unknown or inactive key.")
    tier = row[1]
    q = premium_status(request)            # distinct-town counters (issue #83)
    q.pop("unlocked", None); q.pop("tier", None)
    q["window"] = {"free": "lifetime", "pro": "month"}.get(tier, "none")
    with ops_cursor() as cur:
        cur.execute("SELECT coalesce(sum(requests),0) FROM public.api_usage "
                    "WHERE key = %s::uuid AND day >= date_trunc('month', now())::date", (key,))
        api_calls_month = int(cur.fetchone()[0])
    return {"tier": tier, "premium_reports": q,
            "api_requests_this_month": api_calls_month,
            "features": PLAN_FEATURES.get(tier, []),
            "all_plans": PLAN_FEATURES}


def _population_weights(codes: list, country: str, since_year: int):
    """Weights from the census, following COGugaison (Kim Antunez, confirmed by
    email 2026-08-01): the municipal populations of the communes RESULTING from
    the split, taken at the FIRST census that follows it — never the population
    before the split, which no harmonised source provides (issue #88).

    Returns (weights, census_year) or (None, reason) when the census cannot
    answer for every target: we fall back to area rather than mixing two methods
    silently inside one response."""
    with cursor() as cur:
        cur.execute(
            "SELECT min(census_year) FROM commune_population "
            "WHERE country=%s AND code = ANY(%s) AND census_year >= %s",
            (country, codes, since_year))
        row = cur.fetchone()
        year = row[0] if row else None
        if not year:
            return None, "no census on or after the split"
        cur.execute(
            "SELECT code, population FROM commune_population "
            "WHERE country=%s AND code = ANY(%s) AND census_year=%s",
            (country, codes, year))
        pops = {c: p for c, p in cur.fetchall()}
    missing = [c for c in codes if c not in pops or not pops[c]]
    if missing:
        return None, f"no census figure for {', '.join(sorted(missing))}"
    total = sum(pops.values())
    if not total:
        return None, "census populations sum to zero"
    return {c: pops[c] / total for c in codes}, year


@app.get("/v1/passage")
def passage(
    code: str = Query(..., description="Source unit code"),
    date_from: date = Query(..., alias="from",
                            description="Vintage the value is expressed in"),
    date_to: date = Query(..., alias="to", description="Target date"),
    country: str = Query("FR"),
    weighting: str = Query("area", pattern="^(area|population)$",
                           description="area (geometric) or population "
                                       "(municipale, COGugaison method)"),
):
    """Correspondence table from a source unit (as it existed at `from`) to the
    unit(s) covering the same territory at `to`, with weights.

    Two weightings (issue #94): `area` = share of the source area falling in each
    target; `population` = municipal populations of the resulting communes at the
    first census following the split. This is the COGugaison operation as an API.
    Credit: methodology follows COGugaison (Kim Antunez)."""
    with cursor() as cur:
        cur.execute(
            "SELECT geom FROM commune_version WHERE code=%s AND country=%s "
            " AND unit_type = ANY(%s) AND valid_from <= %s AND valid_to > %s "
            "LIMIT 1",
            (code, country.upper(), list(MUNICIPAL_TYPES), date_from, date_from))
        src = cur.fetchone()
        if not src or not src[0]:
            raise HTTPException(404, f"No source unit {country}/{code} at {date_from}")
        cur.execute(
            "SELECT code, nom, valid_from, "
            " ST_Area(ST_Intersection(geom, %s)::geography) AS inter, "
            " ST_Area(%s::geography) AS total "
            "FROM commune_version "
            "WHERE country=%s AND unit_type = ANY(%s) "
            " AND valid_from <= %s AND valid_to > %s "
            " AND ST_Intersects(geom, %s) "
            "ORDER BY inter DESC",
            (src[0], src[0], country.upper(), list(MUNICIPAL_TYPES),
             date_to, date_to, src[0]))
        rows = cur.fetchall()
    targets = [{"code": c, "nom": n, "weight": round(inter / total, 6), "_vf": vf}
               for c, n, vf, inter, total in rows if total and inter / total > 0.005]
    ssum = sum(t["weight"] for t in targets) or 1.0
    for t in targets:
        t["weight"] = round(t["weight"] / ssum, 6)   # normalize to 1

    applied, census_year, fallback = "area", None, None
    if weighting == "population" and targets:
        if len(targets) == 1:
            # A merger needs no apportionment: the successor takes everything.
            applied = "population"
        else:
            # The split is the moment the current targets came into being.
            split_year = max(t["_vf"] for t in targets).year
            weights, info = _population_weights(
                [t["code"] for t in targets], country.upper(), split_year)
            if weights:
                applied, census_year = "population", info
                for t in targets:
                    t["weight"] = round(weights[t["code"]], 6)
            else:
                fallback = info          # stated, never silently substituted
    for t in targets:
        t.pop("_vf", None)

    if applied == "population":
        note = ("Population weighting: municipal populations of the resulting "
                "communes at the first census following the split"
                + (f" ({census_year})" if census_year else "")
                + ". Figures are harmonised by INSEE on a single reference "
                  "geography (see /v1/communes/{code}/history?population=true), "
                  "so they describe each target's current territory.")
    else:
        note = "Area weighting: share of the source area falling in each target."
        if fallback:
            note += (f" Population weighting was requested but not applied: "
                     f"{fallback}.")
    return {"source": {"code": code, "at": date_from.isoformat()},
            "target_date": date_to.isoformat(),
            "weighting": applied,
            "weighting_requested": weighting,
            "census_year": census_year,
            "targets": targets,
            "note": note + " Method follows COGugaison (Kim Antunez)."}


@app.get("/healthz")
def healthz():
    with cursor() as cur:
        cur.execute("SELECT count(*) FROM commune_version")
        return {"status": "ok", "version": APP_VERSION, "versions": cur.fetchone()[0],
                # Reported by the API, never inferred from the hostname. A badge
                # driven by the host would say nothing if PRODUCTION were ever
                # pointed at Polar sandbox -- and that is the dangerous
                # direction: customers "paying" while no money is collected,
                # with every page looking normal. Here it is also an ops signal.
                "payment_mode": payment_mode()}


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
# Polar API for minting customer-portal sessions (issue #81). Base differs
# between prod (api.polar.sh) and sandbox (sandbox-api.polar.sh). The access
# token needs `customer_sessions:write`; absent = the billing button degrades
# to the receipt-email note. Never committed — provided via backend secrets.
POLAR_API_BASE = os.environ.get("POLAR_API_BASE", "https://api.polar.sh").rstrip("/")


def payment_mode() -> str:
    """"sandbox" when no real money can move, "production" otherwise.

    Derived from the Polar host actually configured, not from an environment
    name or a hostname: those describe intent, and this must describe fact.
    """
    return "sandbox" if "sandbox" in POLAR_API_BASE else "production"
POLAR_ACCESS_TOKEN = os.environ.get("POLAR_ACCESS_TOKEN", "")


def _polar_get(path: str):
    """GET the Polar API with the backend token; return parsed JSON or None."""
    import urllib.request
    req = urllib.request.Request(
        f"{POLAR_API_BASE}{path}",
        headers={"Authorization": f"Bearer {POLAR_ACCESS_TOKEN}",
                 # Cloudflare 403s the default Python-urllib agent.
                 "User-Agent": f"confinia-api/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _polar_customer_id(email: str) -> str | None:
    """The Polar customer id for `email`: the one captured by the subscription
    webhook if present, else looked up by email via the Polar API (so existing
    subscriptions whose customer_id predates the column still resolve)."""
    with ops_cursor() as cur:
        cur.execute("SELECT customer_id FROM public.polar_subscription "
                    "WHERE email=%s AND customer_id IS NOT NULL "
                    "ORDER BY updated_at DESC LIMIT 1", (email,))
        row = cur.fetchone()
    if row:
        return row[0]
    import urllib.parse
    data = _polar_get(f"/v1/customers/?email={urllib.parse.quote(email)}&limit=1")
    items = (data or {}).get("items") or []
    return items[0]["id"] if items else None


def polar_portal_url(email: str) -> str | None:
    """Mint a Polar customer-portal session for `email` and return its URL, or
    None when billing self-service is not available (no token, no customer, or
    the Polar API refused). The portal is where the buyer downloads invoices."""
    if not (POLAR_ACCESS_TOKEN and email):
        return None
    customer_id = _polar_customer_id(email)
    if not customer_id:
        return None
    import urllib.request
    req = urllib.request.Request(
        f"{POLAR_API_BASE}/v1/customer-sessions/",   # trailing slash: no-slash gets a 307 that urllib won't re-POST
        data=json.dumps({"customer_id": customer_id}).encode(),
        headers={"Authorization": f"Bearer {POLAR_ACCESS_TOKEN}",
                 "Content-Type": "application/json",
                 # Cloudflare 403s the default Python-urllib agent.
                 "User-Agent": f"confinia-api/{APP_VERSION}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read()).get("customer_portal_url")
    except Exception:
        return None


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
    customer_id = str(data.get("customer_id")
                      or (data.get("customer") or {}).get("id") or "") or None
    product_id = str(data.get("product_id")
                     or (data.get("product") or {}).get("id") or "")
    tier = POLAR_TIER_BY_PRODUCT.get(product_id)
    if not (sub_id and email and tier):
        return {"status": "ignored", "reason": "souscription incomplète ou produit inconnu"}
    with ops_cursor() as cur:
        cur.execute(
            "INSERT INTO public.polar_subscription (subscription_id, email, tier, status, customer_id) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (subscription_id) DO UPDATE SET "
            " email = EXCLUDED.email, tier = EXCLUDED.tier, status = EXCLUDED.status, "
            " customer_id = COALESCE(EXCLUDED.customer_id, polar_subscription.customer_id), "
            " updated_at = now()",
            (sub_id, email, tier, status, customer_id))
    effective = polar_apply_tier(email)
    return {"status": "ok", "email_tier": effective}


@app.get("/v1/billing/portal")
def billing_portal(request: Request):
    """Return the signed-in caller's Polar customer-portal URL (invoices +
    billing self-service). Authenticated by the Keycloak Bearer JWT (issue #36),
    so a caller only ever gets a session for their OWN email (issue #81)."""
    ident = bearer_identity(request)
    if not ident:
        raise HTTPException(401, "Bearer token required (sign in first).")
    url = polar_portal_url(ident["email"])
    if not url:
        raise HTTPException(503, {
            "detail": "Self-service billing portal is not available yet; your "
                      "invoice is in the receipt Polar emailed you.",
            "reason": "no active Polar customer for this account, or portal not configured."})
    return {"portal_url": url}


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


# --- Historical population (issue #88) --------------------------------------
# INSEE back-projects every census onto ONE reference geography, so the figures
# describe a CURRENT commune's territory, not the historical commune. Two
# consequences we must never hide: `harmonised_on` travels with the series, and
# a code that has died is ABSENT from the source — we then follow our own
# lineage to its living successor and say so (`via_successor`).
POP_NOTE = {
    "en": ("Figures are harmonised by INSEE on the geography of {d}: they describe "
           "the territory of the commune as it exists at that date, summed back "
           "through time — not the population of the historical commune. "
           "Censuses from 2006 on should only be compared at 5-year intervals."),
    "fr": ("Chiffres harmonisés par l'INSEE sur la géographie du {d} : ils décrivent "
           "le territoire de la commune telle qu'elle existe à cette date, sommé "
           "rétrospectivement — et non la population de la commune historique. "
           "Les recensements depuis 2006 ne se comparent qu'à 5 ans d'intervalle."),
}


def _live_successor(code: str, country: str = "FR") -> str | None:
    """Follow the lineage of a dead code to a still-living one (issue #88)."""
    seen, queue = {code}, [code]
    while queue:
        cur_code = queue.pop(0)
        with cursor() as cur:
            cur.execute(
                "SELECT valid_to, children FROM commune_version "
                "WHERE unit_type = ANY(%s) AND country = %s AND code = %s "
                "ORDER BY valid_from DESC LIMIT 1",
                (list(MUNICIPAL_TYPES), country, cur_code))
            row = cur.fetchone()
        if not row:
            continue
        valid_to, children = row
        if valid_to == FAR_FUTURE:
            return cur_code
        for child in (children or []):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return None


def population_series(code: str, country: str = "FR", lang: str = "en") -> dict | None:
    """Harmonised census series for `code`, or for its living successor when the
    code itself has disappeared (issue #88). None when nothing is available."""
    def fetch(c):
        with cursor() as cur:
            cur.execute(
                "SELECT census_year, population, harmonised_on, source "
                "FROM commune_population WHERE country = %s AND code = %s "
                "ORDER BY census_year", (country, c))
            return cur.fetchall()
    served_for, rows = code, fetch(code)
    via_successor = False
    if not rows:
        succ = _live_successor(code, country)
        if succ and succ != code:
            rows = fetch(succ)
            if rows:
                served_for, via_successor = succ, True
    if not rows:
        return None
    harmonised_on = rows[0][2]
    out = {
        "code": served_for,
        "harmonised_on": harmonised_on.isoformat() if harmonised_on else None,
        "source": rows[0][3],
        "series": [{"year": y, "population": p} for y, p, _, _ in rows],
        "note": POP_NOTE.get(lang, POP_NOTE["en"]).format(
            d=harmonised_on.isoformat() if harmonised_on else "?"),
    }
    if via_successor:
        # The requested commune no longer exists: INSEE has no row for it, so we
        # serve its successor's series and make the substitution explicit.
        out["via_successor"] = True
        out["requested_code"] = code
    return out


@app.get("/v1/communes/{code}/history")
def commune_history(code: str, geometry: bool = Query(False),
                    lang: str | None = Query(None,
                        description="Chronology language (fr/en); default French for these FR units"),
                    population: bool = Query(False,
                        description="Add the harmonised INSEE census series (issue #88)"),
                    neighbours: bool = Query(False,
                        description="Add, per version, the units bordering it AT THAT "
                                    "period's date, to situate the outline (issue #96)")):
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
    L = resolve_lang(lang, "FR")
    annotate_changes(versions)
    out = {"code": code, "versions": versions, "events": derive_events(versions, L)}
    if population:
        out["population"] = population_series(code, "FR", L)
    if neighbours and geometry:
        # Same dated rule as the report: the neighbours of THAT period, never
        # today's around an old outline (issue #96).
        xs = [x for f in versions for ring in _feature_rings(f) for x, _ in ring]
        ys = [y for f in versions for ring in _feature_rings(f) for _, y in ring]
        bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else None
        with cursor() as cur:
            for f in versions:
                vf = f["properties"]["valid_from"]
                f["properties"]["neighbours"] = (
                    _neighbour_rings(cur, code, "FR", vf, bbox)
                    if bbox and _feature_rings(f) else [])
    return out


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
def unit_history(code: str, country: str | None = Query(None), geometry: bool = Query(False),
                 lang: str | None = Query(None,
                     description="Chronology language (fr/en); default French for FR, English otherwise")):
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
    annotate_changes(versions)
    return {"code": code, "versions": versions,
            "events": derive_events(versions, resolve_lang(lang, (country or "").upper()))}


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
    annotate_changes(versions)
    return {"code": code.upper(), "versions": versions, "events": derive_events(versions)}
