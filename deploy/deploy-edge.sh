#!/bin/bash
# Mise à jour de l'edge caddy SANS coupure. À lancer SUR LA VM, après rsync :
#   ./deploy/deploy-edge.sh
# 1) Valide la NOUVELLE config dans un conteneur éphémère (jamais dans le
#    conteneur en marche : après un rsync il peut voir d'anciens inodes).
# 2) Reload gracieux dans le conteneur : la config est montée en répertoire
#    (deploy/caddy -> /etc/caddy/conf), donc le nouveau fichier est visible.
set -eu
cd "$(dirname "$0")/.."

echo "== validation (conteneur éphémère, vrais fichiers, même env que la prod)"
podman run --rm \
	--env-file deploy/secrets.env \
	-v ./deploy/caddy:/etc/caddy:ro \
	-v ./deploy/sites:/etc/caddy/sites:ro \
	-v "$HOME/confinia-edge-state:/etc/caddy/active:ro" \
	docker.io/library/caddy:2 caddy validate --config /etc/caddy/Caddyfile

echo "== reload gracieux (chemin STANDARD /etc/caddy/Caddyfile : contrat partagé)"
podman exec confinia_caddy_1 caddy reload --config /etc/caddy/Caddyfile
echo "OK : edge rechargé sans coupure."
