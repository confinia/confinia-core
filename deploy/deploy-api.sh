#!/bin/bash
# API deployment via COLOR STACKS (blue/green, complete and independent).
# Run ON THE VM, after rsync:
#   ./deploy/deploy-api.sh stage      # build + the PASSIVE color's API
#                                     # switches to the new version; validate
#                                     # it on https://staging.api.confinia.io
#                                     # (staging always targets the passive)
#   ./deploy/deploy-api.sh promote    # the passive color becomes ACTIVE
#                                     # (caddy switchover via deploy/stacks.sh)
#   ./deploy/deploy-api.sh rollback   # switch back to the other color
#   ./deploy/deploy-api.sh full       # stage + promote (default)
#   SKIP_BUILD=1 …                    # switch back without rebuilding
# The DATA follows its own cycle: double ingestion on the passive color
# (deploy/stacks.sh build <color>) then promote. Never copied.
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
	echo "FAILURE: /healthz on $1 not responding after 120 s" >&2
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
	echo "== the $P API (passive, $(port_of "$P")) switches to the new version; the public stays on $A"
	podman rm -f "confinia-${P}_api_1" >/dev/null 2>&1 || true
	# --no-deps is MANDATORY: without it, a hash change of secrets.env
	# makes compose recreate the db and tear down its dependents (doctrine, 2 incidents).
	podman-compose -p "confinia-$P" -f "$PWD/deploy/stack/docker-compose-$P.yml" \
		--profile serve up -d --no-deps api >/dev/null 2>&1
	wait_ok "$(port_of "$P")"
	echo "OK: validate on https://staging.api.confinia.io then ./deploy/deploy-api.sh promote"
}

promote() {
	A=$(active); P=$(other "$A")
	./deploy/stacks.sh promote "$P"
}

case "${1:-full}" in
	stage)    stage ;;
	promote)  promote ;;
	rollback) promote ;;      # symmetrical: switches back to the other color
	full)     stage; promote ;;
	*) echo "usage: $0 [stage|promote|rollback|full]" >&2; exit 2 ;;
esac
