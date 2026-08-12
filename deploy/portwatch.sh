#!/bin/bash
# Confinia #123 watchdog. Records WHEN a colour's port publisher appears or
# disappears — and now WHY, by dumping the podman events around that instant.
#
# The events only became available on 2026-08-12: the logger was journald, which
# recorded nothing at all in rootless mode and failed silently, so every earlier
# occurrence was diagnosed blind. Switching to the file backend was impossible
# until the stack moved to its own user (#99), because containers.conf was
# shared with five other products.
#
# Ports: 8091 = blue, 8402 = green. Read-only: it observes, it never acts.
LOG=$HOME/logs/portwatch.log
EVENTS=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/libpod/tmp/events/events.log
mkdir -p "$HOME/logs"
prev="__init__"
while true; do
  now=$(ss -ltn 2>/dev/null | grep -oE '127\.0\.0\.1:(8091|8402)' | sort | tr '\n' ' ')
  if [ "$now" != "$prev" ]; then
    {
      echo "=== $(date -u +%FT%TZ)  listeners=[${now:-NONE}]  was=[${prev}]"
      for c in confinia-blue_api_1 confinia-green_api_1; do
        echo "    $c : $(podman inspect "$c" --format '{{.State.Status}} pid={{.State.Pid}} since {{.State.StartedAt}}' 2>/dev/null || echo absent)"
      done
      echo "    rootlessport processes: $(pgrep -c -f rootlessport 2>/dev/null)"
      # The point of this rewrite: what podman itself did, around the change.
      if [ -f "$EVENTS" ]; then
        echo "    --- last 12 podman events:"
        # The key is "Time", capitalised. Health-check chatter is dropped: it
        # is most of the volume and none of the signal.
        tail -60 "$EVENTS" 2>/dev/null | python3 -c '
import json, sys
rows = []
for line in sys.stdin:
    try:
        d = json.loads(line)
    except ValueError:
        continue
    if d.get("Status") == "health_status":
        continue
    rows.append("      %s %-9s %-24s %s" % (
        str(d.get("Time", ""))[:19], d.get("Status", ""),
        (d.get("Name") or d.get("Image") or "")[:24],
        (d.get("Attributes") or {}).get("containerExitCode", "")))
print("\n".join(rows[-10:]) or "      (no non-health events)")
' 2>/dev/null
      else
        echo "    --- no events file at $EVENTS"
      fi
    } >> "$LOG"
    prev="$now"
  fi
  sleep 20
done
