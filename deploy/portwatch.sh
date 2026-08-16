#!/bin/bash
# Confinia #123: detect a colour whose port mapping exists but is not implemented,
# record the evidence, and repair it.
#
# THE FAULT, characterised over four days: podman reports `8000/tcp ->
# 127.0.0.1:11220`, the container is `running`, the app answers `/healthz` 200 on
# port 8000 INSIDE its namespace, and no rootlessport process holds 11220 on the
# host. It happens within ~a minute of a deployment made BY THE CI RUNNER, and
# not after the same command run over ssh.
#
# Six hypotheses were tested and disproved: a dying publisher, churn from other
# tenants on the shared account, an orphaned publisher, port squatting, the
# host-network smoke container, and missing cgroup delegation on the runner unit
# (Delegate=yes changed nothing). The cause is still unknown.
#
# So this repairs instead of explaining. It is the honest trade: staging was
# silently dead after most deployments, and a 20-second self-heal is worth more
# than a seventh theory. Every repair is logged, so the frequency stays visible
# rather than becoming invisible — a self-healing loop that nobody counts is how
# a fault stops being fixed.
LOG=$HOME/logs/portwatch.log
EVENTS=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/libpod/tmp/events/events.log
REPO=$HOME/projects/confinia
mkdir -p "$HOME/logs"

declare -A LAST_FIX
COOLDOWN=600          # never repair the same colour more than once per 10 min

log() { echo "$@" >> "$LOG"; }

evidence() {
  log "    $1 : $(podman inspect "$1" --format '{{.State.Status}} pid={{.State.Pid}} since {{.State.StartedAt}}' 2>/dev/null || echo absent)"
  log "    podman claims: $(podman port "$1" 2>/dev/null | tr '\n' ' ')"
  log "    rootlessport holding it: $(ss -ltnp 2>/dev/null | grep -c "127.0.0.1:$2 ")"
  [ -f "$EVENTS" ] && tail -40 "$EVENTS" 2>/dev/null | python3 -c '
import json, sys
rows = []
for line in sys.stdin:
    try: d = json.loads(line)
    except ValueError: continue
    if d.get("Status") == "health_status": continue
    rows.append("      %s %-9s %s" % (str(d.get("Time",""))[:19], d.get("Status",""),
                                      (d.get("Name") or "")[:28]))
print("\n".join(rows[-8:]))' >> "$LOG" 2>/dev/null
}

prev="__init__"
while true; do
  now=$(ss -ltn 2>/dev/null | grep -oE '127\.0\.0\.1:(8091|11220)' | sort | tr '\n' ' ')
  [ "$now" != "$prev" ] && {
    log "=== $(date -u +%FT%TZ)  listeners=[${now:-NONE}]  was=[${prev}]"
    for c in blue:8091 green:11220; do evidence "confinia-${c%%:*}_api_1" "${c##*:}"; done
    prev="$now"
  }

  # The repair: mapping claimed, container running, nothing listening.
  for pair in blue:8091 green:11220; do
    colour=${pair%%:*}; port=${pair##*:}; name="confinia-${colour}_api_1"
    podman ps --format '{{.Names}}' 2>/dev/null | grep -qx "$name" || continue
    ss -ltn 2>/dev/null | grep -q "127.0.0.1:$port " && continue
    podman port "$name" 2>/dev/null | grep -q ":$port$" || continue

    last=${LAST_FIX[$colour]:-0}
    if [ $(( $(date +%s) - last )) -lt $COOLDOWN ]; then
      log "    NOT repairing $colour: repaired less than ${COOLDOWN}s ago. If this"
      log "      repeats, the repair is masking something and #123 needs the fix."
      continue
    fi
    log "!!! $(date -u +%FT%TZ)  REPAIRING $colour: podman claims :$port, nothing listens"
    LAST_FIX[$colour]=$(date +%s)
    ( cd "$REPO" && SKIP_BUILD=1 ./deploy/deploy-api.sh stage >>"$LOG" 2>&1 ) || \
      log "    repair FAILED — see above"
    sleep 10
    log "    after repair: $(ss -ltn 2>/dev/null | grep -c "127.0.0.1:$port ") listener on $port"
  done
  sleep 20
done
