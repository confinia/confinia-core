#!/bin/bash
# What is firing RIGHT NOW (RULES 17). Run ON THE VM:
#   ./deploy/alerts.sh          -> exits 1 if anything is firing
#
# Two checks, because they fail differently:
#
#   1. What Grafana says is firing NOW -- the source the notification mails are
#      generated from. No delivery delay, and it answers the present tense.
#   2. What bounced. alert@confinia.io is send-only, so anything in its inbox
#      means mail we sent was never delivered. That is the only place where
#      "the alerting itself is broken" shows up: Grafana calls a send
#      successful as soon as SMTP accepts it, and the bounce arrives later,
#      out of band. On 2026-08-11 that mailbox held a bounce nobody read --
#      contact@confinia.io did not exist yet, so every alert of that day went
#      nowhere while both ends reported success.
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

status=0
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
' || status=$?

# The bounce mailbox. Same OVH login as the SMTP sender -- IMAP needs no extra
# secret, which is why this check costs nothing to keep.
if [ -r deploy/mail.env ]; then
	set -a; . ./deploy/mail.env; set +a
	python3 deploy/mailcheck.py || status=$?
else
	echo "deploy/mail.env not readable: bounces NOT checked" >&2
	[ "$status" = 0 ] && status=2
fi

exit "$status"
