#!/bin/bash
# Maintenance holding page (issue #99).
#
#   ./deploy/maintenance.sh up     # stand in on :11000 while the stack is down
#   ./deploy/maintenance.sh down   # step aside so the real caddy can take :11000
#   ./deploy/maintenance.sh status
#
# WHY IT EXISTS: during the cutover the project caddy is stopped, so the
# PLATFORM edge gets a connection refused on 127.0.0.1:11000 and the visitor sees
# a bare 502 from someone else's server -- no explanation, and search engines
# see an error with no Retry-After. This serves a real page with 503 instead.
#
# It binds the same port as the project caddy, so the two can never run at once:
# `up` must come after the real caddy stops, `down` before it starts.
set -eu
cd "$(dirname "$0")/.."

NAME=confinia-maintenance
# The port the PLATFORM edge targets, so this stands in for the project caddy
# exactly. It was 8085 until the 1PESI migration -- and a maintenance page
# bound to a port nobody routes to is worse than none: you reach for it
# precisely when everything else is already broken, and it answers nowhere.
PORT=11000

case "${1:-status}" in
up)
	if ss -ltn 2>/dev/null | grep -q "127.0.0.1:$PORT"; then
		echo "REFUSING: something already listens on $PORT (the real caddy?)." >&2
		echo "Stop it first; two servers cannot share the port." >&2
		exit 1
	fi
	podman rm -f "$NAME" >/dev/null 2>&1 || true
	podman run -d --name "$NAME" --network host \
		-v "$PWD/deploy/maintenance/Caddyfile:/etc/caddy/Caddyfile:ro" \
		-v "$PWD/deploy/maintenance:/srv:ro" \
		docker.io/library/caddy:2 >/dev/null
	for i in $(seq 1 20); do
		code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$PORT/" || true)
		[ "$code" = 503 ] && { echo "maintenance page up on :$PORT (503, Retry-After)"; exit 0; }
		sleep 1
	done
	echo "FAILED: the maintenance page is not answering 503 on $PORT" >&2
	podman logs --tail 10 "$NAME" >&2 || true
	exit 1
	;;
down)
	podman rm -f "$NAME" >/dev/null 2>&1 || true
	for i in $(seq 1 15); do
		ss -ltn 2>/dev/null | grep -q "127.0.0.1:$PORT" || { echo "port $PORT released"; exit 0; }
		sleep 1
	done
	echo "WARNING: $PORT still bound after removing $NAME" >&2
	exit 1
	;;
status)
	if podman ps --format '{{.Names}}' | grep -qx "$NAME"; then
		echo "maintenance page RUNNING ($(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:$PORT/ || echo '?'))"
	else
		echo "maintenance page not running"
	fi
	;;
*) echo "usage: $0 [up|down|status]" >&2; exit 2 ;;
esac
