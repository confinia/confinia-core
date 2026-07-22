#!/bin/bash
# ZERO-downtime update of the caddy edge. Run ON THE VM, after rsync:
#   ./deploy/deploy-edge.sh
# 1) Validates the NEW config in an ephemeral container (never in the
#    running container: after an rsync it may see old inodes).
# 2) Graceful reload inside the container: the config is mounted as a
#    directory (deploy/caddy -> /etc/caddy/conf), so the new file is visible.
set -eu
cd "$(dirname "$0")/.."

echo "== validation (ephemeral container, real files, same env as prod)"
podman run --rm \
	--env-file deploy/secrets.env \
	-v ./deploy/caddy:/etc/caddy:ro \
	-v ./deploy/sites:/etc/caddy/sites:ro \
	-v "$HOME/confinia-edge-state:/etc/caddy/active:ro" \
	docker.io/library/caddy:2 caddy validate --config /etc/caddy/Caddyfile

echo "== graceful reload (STANDARD path /etc/caddy/Caddyfile: shared contract)"
podman exec confinia_caddy_1 caddy reload --config /etc/caddy/Caddyfile
echo "OK: edge reloaded with no downtime."
