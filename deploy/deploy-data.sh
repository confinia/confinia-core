#!/bin/bash
# Bleu/vert pour les DONNÉES : ingérer et valider dans un schéma staging de
# Postgres, puis bascule atomique vers l'actif. À lancer SUR LA VM.
#
#   ./deploy/deploy-data.sh stage
#       Clone l'actif (commune_version + departement_geom) dans le schéma
#       `staging` et démarre l'API de validation (8002). Les vhosts
#       staging.confinia.io / staging.api.confinia.io la servent en priorité :
#       ils montrent alors les données CANDIDATES pendant que la prod sert
#       l'actif. Ensuite, lancer les ingestions SUR LE STAGING, p.ex. :
#           source deploy/secrets.env
#           podman-compose --profile tools run --rm --no-deps \
#             -e PG_DSN="${PG_DSN}?options=-csearch_path%3Dstaging,public" \
#             ingest /app/ingest_trf.py
#   ./deploy/deploy-data.sh promote
#       Bascule atomique : l'actif devient `previous` (cible du rollback),
#       le staging devient l'actif. Coupe l'API 8002.
#   ./deploy/deploy-data.sh rollback
#       Re-bascule previous <-> actif (annule le dernier promote).
#   ./deploy/deploy-data.sh abort
#       Jette le staging sans bascule et coupe l'API 8002.
#
# Notes : les tables opérationnelles (api_key, api_usage, visitor_daily,
# data_source) restent dans `public`, partagées ; psycopg2 n'utilise pas de
# prepared statements nommés, donc les instances en marche résolvent le
# nouveau schéma à la requête suivante, sans redémarrage.
set -eu
cd "$(dirname "$0")/.."
# Pas de `source secrets.env` : les hash bcrypt ($2a$…) seraient interprétés
# par le shell. On extrait uniquement le DSN.
PG_DSN=$(grep '^PG_DSN=' deploy/secrets.env | cut -d= -f2-)
case "$PG_DSN" in *\?*) SEP="&" ;; *) SEP="?" ;; esac
STG_DSN="${PG_DSN}${SEP}options=-csearch_path%3Dstaging,public"

sql() { podman exec -i confinia_db_1 psql -U confinia -d confinia -v ON_ERROR_STOP=1; }

start_api_c() {
	podman rm -f confinia_api-c_1 >/dev/null 2>&1 || true
	podman run -d --name confinia_api-c_1 \
		--network confinia_default \
		--env-file deploy/secrets.env \
		-e PG_DSN="$STG_DSN" \
		-e OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 \
		-v "$(pwd)/data/geoip:/geoip:ro" \
		-p "127.0.0.1:8002:8000" \
		--restart unless-stopped \
		localhost/confinia-api:latest >/dev/null
	for _ in $(seq 1 30); do
		curl -sf http://127.0.0.1:8002/healthz >/dev/null && return 0
		sleep 2
	done
	echo "ECHEC : API staging (8002) ne répond pas" >&2
	return 1
}

stop_api_c() { podman rm -f confinia_api-c_1 >/dev/null 2>&1 || true; }

case "${1:-}" in
stage)
	echo "== clonage de l'actif vers le schéma staging (~1 Go, patience)"
	sql <<'SQL'
DROP SCHEMA IF EXISTS staging CASCADE;
CREATE SCHEMA staging;
CREATE TABLE staging.commune_version (LIKE public.commune_version INCLUDING ALL);
INSERT INTO staging.commune_version SELECT * FROM public.commune_version;
CREATE MATERIALIZED VIEW staging.departement_geom AS
SELECT CASE WHEN code LIKE '97%' THEN left(code, 3) ELSE left(code, 2) END AS dept,
       ST_Multi(ST_SimplifyPreserveTopology(ST_Union(geom), 0.002)) AS geom
FROM staging.commune_version
WHERE unit_type = 'commune' AND valid_to = '9999-01-01' AND geom IS NOT NULL
GROUP BY 1;
SQL
	echo "== API de validation données (8002)"
	start_api_c
	MASKED=$(printf '%s' "$STG_DSN" | sed -E 's#://([^:]+):[^@]+@#://\1:••••@#')
	echo "OK : ingérer avec PG_DSN=\"$MASKED\" (mot de passe : voir deploy/secrets.env), valider sur https://staging.confinia.io puis promote."
	;;
promote)
	echo "== bascule atomique staging -> actif (l'actif devient previous)"
	sql <<'SQL'
BEGIN;
DROP SCHEMA IF EXISTS previous CASCADE;
CREATE SCHEMA previous;
ALTER TABLE public.commune_version SET SCHEMA previous;
ALTER MATERIALIZED VIEW public.departement_geom SET SCHEMA previous;
ALTER TABLE staging.commune_version SET SCHEMA public;
ALTER MATERIALIZED VIEW staging.departement_geom SET SCHEMA public;
-- CASCADE : l'API de validation a pu laisser des tables opérationnelles
-- vides dans staging (défense en profondeur ; elles sont désormais créées
-- qualifiées public. côté API). Les métriques d'usage de la fenêtre de
-- validation sont sacrifiées, c'est assumé.
DROP SCHEMA staging CASCADE;
COMMIT;
SQL
	stop_api_c
	echo "OK : les données candidates sont actives ; rollback possible."
	;;
rollback)
	echo "== re-bascule previous <-> actif"
	sql <<'SQL'
BEGIN;
CREATE SCHEMA swaptmp;
ALTER TABLE public.commune_version SET SCHEMA swaptmp;
ALTER MATERIALIZED VIEW public.departement_geom SET SCHEMA swaptmp;
ALTER TABLE previous.commune_version SET SCHEMA public;
ALTER MATERIALIZED VIEW previous.departement_geom SET SCHEMA public;
ALTER TABLE swaptmp.commune_version SET SCHEMA previous;
ALTER MATERIALIZED VIEW swaptmp.departement_geom SET SCHEMA previous;
DROP SCHEMA swaptmp;
COMMIT;
SQL
	echo "OK : retour aux données précédentes."
	;;
abort)
	sql <<<'DROP SCHEMA IF EXISTS staging CASCADE;'
	stop_api_c
	echo "OK : staging abandonné, rien n'a changé."
	;;
*)
	echo "usage: $0 [stage|promote|rollback|abort]" >&2
	exit 2
	;;
esac
