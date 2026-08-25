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

migrate() {
	# Additive, idempotent geo-schema changes, applied to the colour being
	# staged. ingest_cog.py owns the schema but runs at INGESTION, so an index
	# added there reaches a live colour never -- idx_cv_country_type_code_vf was
	# merged and applied to nothing, leaving the active colour on a plan that
	# read 53 076 pages to return 2 rows.
	#
	# Run against the colour's own database, never by the API: PG_DSN is
	# read-only at runtime and the staging isolation guard rests on that.
	local f c
	[ -d deploy/migrations ] || return 0
	# BOTH colours, not only the one being staged.
	#
	# Applying to the staged colour alone leaves the other unable to serve the
	# code that is about to go live -- and caddy keeps that other colour as the
	# health-checked fallback. So a failover would answer with a database that
	# lacks the column the new code selects: 500 on every report, from a
	# fallback everyone believes is there. A broken fallback is worse than no
	# fallback, because it is trusted.
	#
	# Found on 2026-08-21 with geography_basis, applied to the passive colour by
	# the pipeline and to the active one by hand. Doing it by hand every time is
	# not a plan.
	#
	# Safe on the live colour because of what a migration is allowed to be here:
	# additive, idempotent, and indexes built CONCURRENTLY. The 1.28 M-row
	# backfill ran on production in 95 s with /healthz answering throughout.
	for c in blue green; do
		podman container exists "confinia-${c}_db_1" 2>/dev/null || continue
		echo "== geo migrations on $c"
		for f in deploy/migrations/*.sql; do
			[ -r "$f" ] || continue
			# CONCURRENTLY cannot run inside a transaction, hence no single -c wrap.
			if podman exec -i "confinia-${c}_db_1" \
				psql -U confinia -d confinia -v ON_ERROR_STOP=1 -q < "$f" 2>/dev/null; then
				echo "   applied $(basename "$f")"
			else
				# A migration that will not apply must be seen, but it must not
				# strand a colour half-deployed: the smoke below is the real gate.
				echo "   [!] FAILED $(basename "$f") on $c -- see the smoke result" >&2
			fi
		done
	done
}

warm() {
	# A passive colour serves nothing, so PostgreSQL's cache holds none of its
	# pages. Measured 2026-08-20 on identical data (205 370 rows in both):
	# /v1/export/ohm?unit_type=epci&limit=1 took 37 s cold on the passive colour
	# and 0.015 s on the active one -- a factor of ~2400, and not statistics:
	# ANALYZE changed nothing. It is simply that one colour's pages are hot and
	# the other's are on disk.
	#
	# Two consequences, and the second is the one that matters. The smoke test
	# times out at 30 s and failed the deploy. And a promotion would hand real
	# users a cold colour: the first requests after every promotion have been
	# paying that price, unnoticed, because nobody was watching the moment of
	# the switch.
	#
	# So touch the heavy paths before anyone else does. Failures are ignored on
	# purpose: this is a favour to the cache, never a gate.
	local port="$1"
	echo "== warming :$port (a passive colour's pages are all on disk)"
	for path in \
		"/v1/export/ohm?country=FR&unit_type=epci&limit=1" \
		"/v1/export/ohm?country=FR&unit_type=commune&limit=1" \
		"/v1/communes/01187/report.svg?country=FR&lang=fr" \
		"/v1/communes/01187/history?geometry=true&neighbours=true" \
		"/healthz"; do
		t=$(curl -s -o /dev/null -w "%{time_total}" --max-time 120 \
			"http://127.0.0.1:$port$path" 2>/dev/null || echo "-")
		printf "   %6ss  %s\n" "$t" "${path%%\?*}"
	done
}

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
	# The demo of the colour being staged. Copied rather than symlinked: the
	# caddy container mounts these paths, and a symlink resolves at mount time,
	# so flipping one would change nothing until caddy were recreated.
	if [ -d demo ]; then
		mkdir -p "$HOME/demo-$P"
		rsync -a --delete demo/ "$HOME/demo-$P/"
		echo "== demo staged into ~/demo-$P ($(find demo -type f | wc -l | tr -d ' ') files)"
	fi
	migrate
	wait_ok "$(port_of "$P")"
	warm "$(port_of "$P")"
	echo "OK: validate on https://staging.api.confinia.io then ./deploy/deploy-api.sh promote"
}

promote() {
	A=$(active); P=$(other "$A")
	./deploy/stacks.sh promote "$P"
	# Warm the colour we just made live, before anything measures it.
	#
	# 2026-08-25: a promotion switched correctly, proved the new colour was
	# answering, and then FAILED its production smoke -- the report PDF took
	# longer than the 30 s a client waits, because that request was the one
	# paying the cold cost of the geometry and the neighbours. The colour was
	# healthy; the first caller was simply unlucky, and the first caller was
	# the smoke, so a good deployment rolled itself back.
	#
	# The smoke must not be the request that pays for the cold cache. Failures
	# are ignored on purpose: this is a favour to the next caller, never a gate
	# -- the smoke that follows is the gate.
	local port
	port=$(port_of "$P")
	echo "== warming $P on $port before the smoke"
	curl -sf --max-time 90 -o /dev/null \
		"http://127.0.0.1:$port/v1/communes/01033/report.pdf?country=FR" || true
	curl -sf --max-time 30 -o /dev/null \
		"http://127.0.0.1:$port/v1/communes/01033/history" || true
}

case "${1:-full}" in
	stage)    stage ;;
	promote)  promote ;;
	rollback) promote ;;      # symmetrical: switches back to the other color
	full)     stage; promote ;;
	*) echo "usage: $0 [stage|promote|rollback|full]" >&2; exit 2 ;;
esac
