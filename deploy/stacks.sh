#!/bin/bash
# Orchestrateur des stacks COULEUR (bleu/vert complets, doctrine : la base géo
# est un artefact reconstruit par double ingestion, jamais copié). VM.
#   ./deploy/stacks.sh up-db <blue|green>   # démarre la db géo de la couleur
#   ./deploy/stacks.sh build <blue|green>   # double ingestion EN FOND
#                                           # (log : ~/logs/build-geo-<c>.log)
#   ./deploy/stacks.sh status               # état des deux couleurs
# Le cutover des API de couleur (remplacement des conteneurs api/api-b
# historiques par les services api des stacks) se fait quand un build est
# validé : voir TODO build « chantier stacks ».
set -eu
cd "$(dirname "$0")/.."

pc() {	# $1 = couleur, reste = commande podman-compose
	local c="$1"; shift
	# Chemins ABSOLUS : podman-compose résout -f relativement au répertoire
	# de l'--env-file (piège découvert au premier lancement).
	podman-compose -p "confinia-$c" --env-file "$PWD/deploy/stack/$c.env" \
		-f "$PWD/deploy/stack/docker-compose.yml" "$@"
}

case "${1:-}" in
up-db)
	c="${2:?couleur}"
	pc "$c" up -d db
	;;
build)
	c="${2:?couleur}"
	mkdir -p ~/logs
	nohup ./deploy/build-geo.sh "$c" > ~/logs/"build-geo-$c.log" 2>&1 &
	echo "build $c lancé en fond : tail -f ~/logs/build-geo-$c.log"
	;;
# Écrit le fichier d'état des upstreams caddy : couleur active en tête pour
# le public, passif (+8002 données) pour le staging. Puis reload gracieux.
write-upstreams|promote)
	c="${2:?couleur active (blue|green)}"
	case "$c" in
		blue)  ACT=8000; PAS=8001 ;;
		green) ACT=8001; PAS=8000 ;;
		*) echo "couleur inconnue : $c" >&2; exit 2 ;;
	esac
	mkdir -p ~/confinia-edge-state
	# Auth du staging en LITTÉRAL dans l'état (le reload admin de caddy ne
	# résout pas les placeholders {env.*}, contrairement au démarrage).
	SU=$(grep '^STAGING_USER=' deploy/secrets.env | cut -d= -f2-)
	SH=$(grep '^STAGING_HASH=' deploy/secrets.env | cut -d= -f2-)
	printf '(staging_auth) {\n\tbasic_auth {\n\t\t%s %s\n\t}\n}\n' "$SU" "$SH" \
		> ~/confinia-edge-state/auth.caddy
	cat > ~/confinia-edge-state/upstreams.caddy <<CADDY
# GÉNÉRÉ par deploy/stacks.sh promote : couleur active = $c ($(date -u +%FT%TZ))
(api_upstreams) {
	reverse_proxy 127.0.0.1:$ACT 127.0.0.1:$PAS {
		lb_policy first
		lb_try_duration 5s
		lb_try_interval 250ms
		fail_duration 15s
		unhealthy_status 5xx
		health_uri /healthz
		health_interval 10s
		health_timeout 3s
	}
}
(staging_upstreams) {
	reverse_proxy 127.0.0.1:8002 127.0.0.1:$PAS {
		lb_policy first
		lb_try_duration 5s
		lb_try_interval 250ms
		fail_duration 15s
		health_uri /healthz
		health_interval 10s
		health_timeout 3s
	}
}
CADDY
	echo "$c" > ~/confinia-edge-state/ACTIVE_COLOR
	podman run --rm --env-file deploy/secrets.env 		-v "$PWD/deploy/caddy:/etc/caddy:ro" 		-v "$HOME/confinia-edge-state:/etc/caddy/active:ro" 		docker.io/library/caddy:2 caddy validate --config /etc/caddy/Caddyfile >/dev/null
	podman exec confinia_caddy_1 caddy reload --config /etc/caddy/Caddyfile
	echo "OK : couleur active = $c (public sur $ACT, staging sur 8002 puis $PAS)"
	;;
status)
	echo "couleur active : $(cat ~/confinia-edge-state/ACTIVE_COLOR 2>/dev/null || echo 'non définie (legacy)')"
	for c in blue green; do
		db="confinia-${c}_db_1"
		if podman exec "$db" pg_isready -U confinia >/dev/null 2>&1; then
			n=$(podman exec "$db" psql -U confinia -d confinia -t -A -c \
				"SELECT count(*) FROM commune_version" 2>/dev/null || echo "schéma absent")
			echo "$c : db up, commune_version = $n"
		else
			echo "$c : db absente"
		fi
	done
	;;
*)
	echo "usage: $0 [up-db <c> | build <c> | status]" >&2
	exit 2
	;;
esac
