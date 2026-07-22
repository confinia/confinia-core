#!/bin/bash
# DOUBLE INGESTION: rebuilds a color's GEO database from the raw sources
# (data/raw/) with the versioned pipelines. This is THE artifact builder:
# each color builds its own database itself, nothing is ever copied from
# the other color. Run ON THE VM (long: allow ~1-2 h):
#   ./deploy/stacks.sh up-db green && ./deploy/stacks.sh build green
# The chain follows the Makefile's canonical order + the 2026-07-20
# additions (sources, TRF 1870-1940, ONS UK, UK reconciliation).
set -eu
COLOR="${1:?usage: build-geo.sh blue|green}"
cd "$(dirname "$0")/.."
NET="confinia-${COLOR}_default"
DB="confinia-${COLOR}_db_1"

RUN() {
	echo
	echo "==== [$COLOR] $1"
	podman run --rm --network "$NET" --env-file deploy/secrets.env \
		-v "$(pwd)/data:/data" localhost/confinia-ingest:latest "$@"
}
PSQL() { podman exec -i "$DB" psql -U confinia -d confinia -v ON_ERROR_STOP=1 -q; }

echo "==== [$COLOR] waiting for the database"
until podman exec "$DB" pg_isready -U confinia -d confinia >/dev/null 2>&1; do sleep 2; done

RUN /app/ingest_cog.py --millesimes 2025 --data-dir /data/raw/insee
RUN /app/join_geometry.py --millesimes 2025 --data-dir /data/raw/insee \
	--shp "2018-01-01=/data/raw/ae2018/extract/**/COMMUNE.shp" \
	--shp "2019-01-01=/data/raw/ae2019/extract/**/COMMUNE.shp" \
	--parquet "2026-01-01=/data/raw/ae2026/commune.parquet" \
	--dsn
RUN /app/ingest_nuts.py --data-dir /data/raw/nuts --download --dsn
RUN /app/ingest_de.py --data-dir /data/raw/de --download --dsn
RUN /app/ingest_nl.py --data-dir /data/raw/nl --download --dsn
RUN /app/ingest_lau.py --data-dir /data/raw/lau --download --dsn
echo "==== [$COLOR] source registry + backfill"
PSQL < ingestion/sources.sql
RUN /app/ingest_trf.py --data-dir /data/raw/trf/communes
RUN /app/ingest_trf_dept.py --data-dir /data/raw/trf/departements
RUN /app/ingest_trf_supra.py --data-dir /data/raw/trf
RUN /app/ingest_epci.py --data-dir /data/raw/banatic
RUN /app/ingest_ons.py --data-dir /data/raw/uk/chd
RUN /app/ingest_nz.py --download --data-dir /data/raw/nz
echo "==== [$COLOR] UK reconciliation"
{ echo "SET search_path TO public;"; cat ingestion/reconcile_uk.sql; } | PSQL
echo "==== [$COLOR] re-backfill sources (idempotent)"
PSQL < ingestion/sources.sql

echo "==== [$COLOR] final check"
podman exec "$DB" psql -U confinia -d confinia -c \
	"SELECT source, count(*) FROM commune_version GROUP BY 1 ORDER BY 2 DESC" -c \
	"SELECT count(*) AS total FROM commune_version"
echo "BUILD GEO $COLOR: OK"
