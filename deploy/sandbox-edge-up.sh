#!/bin/bash
# Bring up / reload the SANDBOX edge (platform RULES.md §6: the sandbox has its
# own caddy process, never a second listen address on production's).
#   ./deploy/sandbox-edge-up.sh
#
# Same shape as deploy-edge.sh: validate in an ephemeral container FIRST, so a
# broken config is caught before it reaches a running process. The point of the
# split is that a mistake here cannot touch confinia_caddy_1 -- which is only
# true if this script never talks to it. It does not: admin is 11490, not 2085.
set -eu
cd "$(dirname "$0")/.."

echo "== validation (ephemeral container, real files)"
podman run --rm \
	--env-file deploy/secrets.env \
	-v ./deploy/caddy-sandbox:/etc/caddy:ro \
	-v "$HOME/confinia-edge-state:/etc/caddy/active:ro" \
	docker.io/library/caddy:2 caddy validate --config /etc/caddy/Caddyfile

if podman ps --format '{{.Names}}' | grep -qx confinia-sandbox_caddy_1; then
	echo "== graceful reload of the sandbox edge (its OWN admin, 11490)"
	podman exec confinia-sandbox_caddy_1 \
		caddy reload --config /etc/caddy/Caddyfile --address localhost:11490
else
	echo "== first start"
	podman-compose -p confinia-sandbox \
		-f "$PWD/deploy/sandbox-stack/docker-compose.yml" up -d
fi

for _ in $(seq 1 30); do
	curl -so /dev/null --max-time 3 -H 'Host: sandbox.confinia.io' \
		http://127.0.0.1:11400/ && { echo "OK: sandbox edge answers on :11400"; exit 0; }
	sleep 2
done
echo "FAILURE: :11400 not answering" >&2
exit 1
