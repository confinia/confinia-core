#!/bin/bash
# Déploiement API bleu/vert, sans coupure, avec validation humaine optionnelle.
# À lancer SUR LA VM, après rsync. Usage :
#   ./deploy/deploy-api.sh            # full : stage + promote (roulant direct)
#   ./deploy/deploy-api.sh stage      # build + bascule le VERT seul ; le public
#                                     # reste sur le BLEU (ancienne version).
#                                     # Tester : https://staging.api.confinia.io
#   ./deploy/deploy-api.sh promote    # bascule le BLEU sur la version validée
#   ./deploy/deploy-api.sh rollback   # rebascule VERT puis BLEU sur :previous
#   SKIP_BUILD=1 …                    # saute le build (re-bascule pure)
#
# Caddy (health checks actifs + passifs, lb_policy first) fait la continuité :
# vérifié sonde à 300 ms, 0 requête perdue pendant les bascules.
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
recreate() {	# $1 = service (api | api-b)   $2 = port hôte   $3 = image
	podman rm -f "confinia_$1_1" >/dev/null 2>&1 || true
	podman run -d --name "confinia_$1_1" \
		--network confinia_default \
		--env-file deploy/secrets.env \
		-e OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 \
		-v "$(pwd)/data/geoip:/geoip:ro" \
		-p "127.0.0.1:$2:8000" \
		--restart unless-stopped \
		"$3" >/dev/null
}

stage() {
	if [ "${SKIP_BUILD:-0}" != "1" ]; then
		# L'image courante devient :previous (cible du rollback), puis build.
		podman tag localhost/confinia-api:latest localhost/confinia-api:previous 2>/dev/null || true
		cp VERSION api/VERSION      # la version voyage dans l'image (pas de .git sur la VM)
		echo "== build (no-cache) $(cat VERSION)"
		podman-compose build --no-cache api
	fi
	echo "== VERT (8001) passe sur la nouvelle image ; le public reste sur le BLEU"
	recreate api-b 8001 localhost/confinia-api:latest
	wait_ok 8001
	echo "OK : à valider sur https://staging.api.confinia.io puis ./deploy/deploy-api.sh promote"
}

promote() {
	echo "== BLEU (8000) passe sur l'image courante (le VERT sert pendant la bascule)"
	recreate api 8000 localhost/confinia-api:latest
	wait_ok 8000
	echo "OK : le public est entièrement sur la nouvelle version."
}

rollback() {
	echo "== ROLLBACK sur :previous (VERT puis BLEU)"
	recreate api-b 8001 localhost/confinia-api:previous
	wait_ok 8001
	recreate api 8000 localhost/confinia-api:previous
	wait_ok 8000
	podman tag localhost/confinia-api:previous localhost/confinia-api:latest
	echo "OK : retour à la version précédente."
}

case "${1:-full}" in
	stage)    stage ;;
	promote)  promote ;;
	rollback) rollback ;;
	full)     stage; promote ;;
	*) echo "usage: $0 [stage|promote|rollback|full]" >&2; exit 2 ;;
esac
