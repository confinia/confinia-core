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
status)
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
