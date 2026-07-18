# Toutes les cibles passent par docker compose — identiques sur macOS (socktainer)
# et sur la VM Debian (podman). Jamais de python direct sur l'hôte (voir DEV.md).

COMPOSE ?= docker compose
# Pas de BuildKit/buildx via docker compose (socktainer ne le supporte pas) :
# build legacy via les variables dédiées, mêmes commandes sur la VM podman.
export DOCKER_BUILDKIT = 0
export COMPOSE_DOCKER_CLI_BUILD = 0
export COMPOSE_BAKE = false

.PHONY: build db-up db-down db-shell ingest load-fr join-01 verify-01 demo api-up stack-up clean

build:            ## construit l'image d'ingestion (fallback macOS : container build)
	$(COMPOSE) build ingest || container build -t confinia-ingest:latest ./ingestion

db-up:            ## démarre PostGIS
	$(COMPOSE) up -d db

db-down:
	$(COMPOSE) down

db-shell:         ## psql dans la base
	$(COMPOSE) exec db psql -U confinia -d confinia

demo:             ## sert la démo web MapLibre sur :8080 (aperçu ; prod = GitHub Pages)
	$(COMPOSE) up -d demo

demo-publish:     ## déploie demo/ vers le repo public confinia.github.io (GitHub Pages)
	rm -rf /tmp/confinia-pages && git clone -q https://github.com/confinia/confinia.github.io /tmp/confinia-pages
	cp demo/index.html /tmp/confinia-pages/index.html
	cd /tmp/confinia-pages && git -c user.name=Confinia -c user.email=contact@confinia.io \
	  commit -am "Deploy demo from confinia-core" && git push -q

demo-data:        ## ingestion en mode démo (aucune donnée requise)
	$(COMPOSE) run --rm --no-deps ingest /app/ingest_cog.py --geojson /data/out/demo.geojson

ingest:           ## COG INSEE -> PostGIS (données dans ./data/raw/insee)
	$(COMPOSE) run --rm ingest /app/ingest_cog.py --millesimes 2025 --data-dir /data/raw/insee

load-fr:          ## pleine France, tous millésimes géométrie -> PostGIS (long)
	$(COMPOSE) run --rm ingest /app/join_geometry.py \
	  --millesimes 2025 --data-dir /data/raw/insee \
	  --shp "2018-01-01=/data/raw/ae2018/extract/**/COMMUNE.shp" \
	  --shp "2019-01-01=/data/raw/ae2019/extract/**/COMMUNE.shp" \
	  --parquet "2026-01-01=/data/raw/ae2026/commune.parquet" \
	  --dsn

api-up:           ## API FastAPI + caddy (HTTPS public api.confinia.io)
	$(COMPOSE) up -d --build api caddy

stack-up: db-up api-up  ## stack complète (db + api + caddy)

join-01:          ## géométries Admin Express, département 01 -> data/out/
	$(COMPOSE) run --rm --no-deps ingest /app/join_geometry.py \
	  --millesimes 2025 --data-dir /data/raw/insee \
	  --shp "2018-01-01=/data/raw/ae2018/extract/**/COMMUNE.shp" \
	  --shp "2019-01-01=/data/raw/ae2019/extract/**/COMMUNE.shp" \
	  --parquet "2026-01-01=/data/raw/ae2026/commune.parquet" \
	  --dept 01 --geojson /data/out/communes_01.geojson \
	  --geojson-raw /data/out/communes_01_raw.geojson

verify-01:        ## test Step 1 : fusion Valserhône sur le GeoJSON produit
	$(COMPOSE) run --rm --no-deps ingest /app/verify_ain.py

clean:
	rm -rf data/out/*
