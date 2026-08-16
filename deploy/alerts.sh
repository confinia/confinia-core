#!/bin/bash
# What is firing RIGHT NOW (RULES 17). Run ON THE VM:
#   ./deploy/alerts.sh          -> exits 1 if anything is firing
#
# RULES 17 says to watch the mailbox for incidents. This reads the SOURCE those
# mails are generated from instead, which is better in three ways: no inbox
# credentials, no delivery delay, and it says what is firing NOW rather than
# what fired at some point. alert@confinia.io is send-only by design and its
# inbox is deliberately tiny, so it was never the place to look anyway.
#
# Why this exists: on 2026-08-16 a regression put /grafana at 502 in production.
# The alert fired correctly and left in 34 seconds -- and was seen only because
# the founder forwarded it. The detection was never the weak link.
set -eu
cd "$(dirname "$0")/.."
PW=$(grep '^GF_SECURITY_ADMIN_PASSWORD=' deploy/secrets.env | cut -d= -f2-)
[ -n "$PW" ] || { echo "no GF_SECURITY_ADMIN_PASSWORD in deploy/secrets.env" >&2; exit 2; }

body=$(curl -sf --max-time 10 -u "admin:$PW" \
	"http://127.0.0.1:11040/api/alertmanager/grafana/api/v2/alerts") || {
	echo "CANNOT REACH GRAFANA on 11040 -- which is itself worth investigating" >&2
	exit 2; }

printf '%s' "$body" | python3 -c '
import json, sys
alerts = json.load(sys.stdin)
firing = [a for a in alerts if a.get("status", {}).get("state") != "suppressed"]
if not firing:
    print("no alert firing")
    raise SystemExit(0)
for a in firing:
    lb = a.get("labels", {})
    print(f'"'"'[{a["status"]["state"].upper()}] {lb.get("alertname","?")} '"'"'
          f'"'"'severity={lb.get("severity","?")} {lb.get("instance","")}'"'"')
    summary = a.get("annotations", {}).get("summary")
    if summary:
        print(f"    {summary}")
raise SystemExit(1)
'
