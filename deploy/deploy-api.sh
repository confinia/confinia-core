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
# Both colours are on the 1PESI band. This function is what `stage` waits on,
# so a stale port here does not misroute anything -- it just makes every
# deployment time out after 120 s against a port nobody serves, which is how
# deploy-staging broke for an afternoon when green's 8402 was dropped.
port_of() { if [ "$1" = blue ]; then echo 11120; else echo 11220; fi; }

wait_ok() {
	for _ in $(seq 1 60); do
		curl -sf "http://127.0.0.1:$1/healthz" >/dev/null && return 0
		sleep 2
	done
	echo "FAILURE: /healthz on $1 not responding after 120 s" >&2
	return 1
}

# Refuse to destroy a healthy container for a port we cannot get back.
#
# On 2026-08-11 `stage` removed a working green API, then could not recreate it:
# another tenant (maplibre) held 8092, so podman failed with "rootlessport
# listen tcp 127.0.0.1:8092: bind: address already in use" and staging stayed
# down. The port was gone BEFORE the container was removed -- we just never
# looked. A reserved band is a convention; ss is the only source of truth.
port_is_ours() {	# $1 = port, $2 = the container allowed to hold it
	local port="$1" mine="$2" line owner pid
	line=$(ss -ltnp 2>/dev/null | grep -E "127\.0\.0\.1:$port " | head -1) || true
	[ -z "$line" ] && return 0                      # free: nothing to argue about
	# Ours already? Then this is the container we are about to replace.
	if podman port "$mine" 2>/dev/null | grep -q ":$port\$"; then
		podman ps --format '{{.Names}}' | grep -qx "$mine" && return 0
	fi
	pid=$(printf '%s' "$line" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
	owner=$(ps -o user= -p "${pid:-0}" 2>/dev/null | tr -d ' ')
	echo "REFUSING: 127.0.0.1:$port is held by ${owner:-another process} (pid ${pid:-?})," >&2
	echo "  and it is not $mine. Recreating would destroy a working container and" >&2
	echo "  fail to bind -- see the BURNED table in PORTS.md and issue #123." >&2
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
	port_is_ours "$(port_of "$P")" "confinia-${P}_api_1" || exit 1
	# Install the unit BEFORE restarting it. deploy/quadlet/*.container was
	# committed, edited, reviewed and merged -- and none of that reached the
	# machine, because nothing ever copied it. The units had been installed by
	# hand once, so the repo copies were documentation that silently drifted.
	# Wiring identity landed KC_ISSUER in the file and production still reported
	# `identity: off`: the image was new, the environment was not. Same shape as
	# the Caddyfile that reached the mirror and stopped, and the edge that was
	# never reloaded.
	UNIT_SRC="deploy/quadlet/confinia-${P}-api.container"
	UNIT_DST="$HOME/.config/containers/systemd/confinia-${P}-api.container"
	if [ -r "$UNIT_SRC" ] && ! cmp -s "$UNIT_SRC" "$UNIT_DST"; then
		mkdir -p "$(dirname "$UNIT_DST")"
		cp "$UNIT_SRC" "$UNIT_DST"
		systemctl --user daemon-reload
		echo "   unit confinia-${P}-api updated from the repo (it had drifted)"
	fi
	if systemctl --user cat "confinia-${P}-api" >/dev/null 2>&1; then
		# Quadlet path (issue #123). The container belongs to SYSTEMD, so it
		# outlives the process that restarted it. A container created here
		# directly would be a child of this shell -- and when this shell is a CI
		# job step, the container is killed about two minutes after the job ends
		# (exit=-1, neither a crash nor a clean stop). The same command over ssh
		# survived nine hours; that difference was the whole bug.
		echo "   via systemd unit confinia-${P}-api"
		# `systemctl restart` returns as soon as systemd has ACTED, not when the
		# container is serving. wait_ok alone is not enough either: for a moment
		# the OLD container is still answering /healthz, so the gate passes, the
		# restart completes, and the smoke then hits nothing. That is exactly how
		# the first Quadlet deployment failed -- smoke at 13:16:07, container up
		# at 13:16:11.
		# So: remember when we asked, and wait for a container that started AFTER.
		asked=$(date -u +%s)
		systemctl --user restart "confinia-${P}-api"
		for _ in $(seq 1 60); do
			started=$(podman inspect "confinia-${P}_api_1" \
				--format '{{.State.StartedAt}}' 2>/dev/null || true)
			[ -n "$started" ] && \
				[ "$(date -u -d "$started" +%s 2>/dev/null || echo 0)" -ge "$asked" ] && break
			sleep 2
		done
	else
		echo "   via podman-compose (no systemd unit for $P yet -- see deploy/quadlet/)"
		podman rm -f "confinia-${P}_api_1" >/dev/null 2>&1 || true
		# --no-deps is MANDATORY: without it, a hash change of secrets.env
		# makes compose recreate the db and tear down its dependents (doctrine, 2 incidents).
		podman-compose -p "confinia-$P" -f "$PWD/deploy/stack/docker-compose-$P.yml" \
			--profile serve up -d --no-deps api >/dev/null 2>&1
	fi
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
