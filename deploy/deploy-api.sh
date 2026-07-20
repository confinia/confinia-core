#!/bin/bash
# Déploiement API par STACKS COULEUR (bleu/vert complets, indépendants).
# À lancer SUR LA VM, après rsync :
#   ./deploy/deploy-api.sh stage      # build + l'API de la couleur PASSIVE
#                                     # passe sur la nouvelle version ; la
#                                     # valider sur https://staging.api.confinia.io
#                                     # (le staging vise toujours le passif)
#   ./deploy/deploy-api.sh promote    # la couleur passive devient ACTIVE
#                                     # (bascule caddy via deploy/stacks.sh)
#   ./deploy/deploy-api.sh rollback   # re-bascule vers l'autre couleur
#   ./deploy/deploy-api.sh full       # stage + promote (défaut)
#   SKIP_BUILD=1 …                    # re-bascule sans rebuild
# Les DONNÉES suivent leur propre cycle : double ingestion sur la couleur
# passive (deploy/stacks.sh build <couleur>) puis promote. Jamais de copie.
set -eu
cd "$(dirname "$0")/.."

active() { cat ~/confinia-edge-state/ACTIVE_COLOR 2>/dev/null || echo green; }
other()  { if [ "$1" = blue ]; then echo green; else echo blue; fi; }
port_of() { if [ "$1" = blue ]; then echo 8000; else echo 8001; fi; }

wait_ok() {
	for _ in $(seq 1 60); do
		curl -sf "http://127.0.0.1:$1/healthz" >/dev/null && return 0
		sleep 2
	done
	echo "ECHEC : /healthz sur $1 ne répond pas après 120 s" >&2
	return 1
}

stage() {
	A=$(active); P=$(other "$A")
	if [ "${SKIP_BUILD:-0}" != "1" ]; then
		podman tag localhost/confinia-api:latest localhost/confinia-api:previous 2>/dev/null || true
		cp VERSION api/VERSION
		echo "== build (no-cache) $(cat VERSION)"
		podman build --no-cache -q -t localhost/confinia-api:latest ./api >/dev/null
	fi
	echo "== l'API $P (passive, $(port_of "$P")) passe sur la nouvelle version ; le public reste sur $A"
	podman rm -f "confinia-${P}_api_1" >/dev/null 2>&1 || true
	podman-compose -p "confinia-$P" -f "$PWD/deploy/stack/docker-compose-$P.yml" \
		--profile serve up -d api >/dev/null 2>&1
	wait_ok "$(port_of "$P")"
	echo "OK : valider sur https://staging.api.confinia.io puis ./deploy/deploy-api.sh promote"
}

promote() {
	A=$(active); P=$(other "$A")
	./deploy/stacks.sh promote "$P"
}

case "${1:-full}" in
	stage)    stage ;;
	promote)  promote ;;
	rollback) promote ;;      # symétrique : re-bascule vers l'autre couleur
	full)     stage; promote ;;
	*) echo "usage: $0 [stage|promote|rollback|full]" >&2; exit 2 ;;
esac
