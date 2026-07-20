#!/bin/bash
# Déploiement API sans coupure (bleu/vert). À lancer SUR LA VM, après rsync :
#   ./deploy/deploy-api.sh
# Séquence : rebuild image (--no-cache, doctrine anti-couches-fantômes) ->
# recrée le vert (8001) pendant que le bleu sert -> attend /healthz vert ->
# recrée le bleu (8000) pendant que le vert sert -> attend /healthz bleu.
# Caddy (health checks + lb_try) bascule tout seul : zéro requête perdue.
set -eu
cd "$(dirname "$0")/.."

wait_ok() {
	for _ in $(seq 1 60); do
		curl -sf "http://127.0.0.1:$1/healthz" >/dev/null && return 0
		sleep 2
	done
	echo "ECHEC : /healthz sur $1 ne répond pas après 120 s" >&2
	return 1
}

# Les bascules n'utilisent PAS podman-compose : même avec --no-deps il
# retraite la db (depends_on + hash de config sur secrets.env partagé) et peut
# supprimer les deux instances d'un coup. podman pur, options répliquées du
# compose (réseau confinia_default : la résolution de `db` et `otel-collector`
# vient des alias de leurs conteneurs sur ce réseau).
recreate() {	# $1 = service (api | api-b)   $2 = port hôte
	podman rm -f "confinia_$1_1" >/dev/null 2>&1 || true
	podman run -d --name "confinia_$1_1" \
		--network confinia_default \
		--env-file deploy/secrets.env \
		-e OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 \
		-v "$(pwd)/data/geoip:/geoip:ro" \
		-p "127.0.0.1:$2:8000" \
		--restart unless-stopped \
		localhost/confinia-api:latest >/dev/null
}

echo "== build (no-cache)"
[ "${SKIP_BUILD:-0}" = "1" ] || podman-compose build --no-cache api
echo "== bascule VERT (8001)"
recreate api-b 8001
wait_ok 8001
echo "== bascule BLEU (8000)"
recreate api 8000
wait_ok 8000
echo "OK : les deux instances servent la nouvelle image, aucune coupure."
