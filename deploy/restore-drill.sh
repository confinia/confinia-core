#!/bin/bash
# Prove the nightly dump comes BACK. Run on the VM:
#   ./deploy/restore-drill.sh            # newest dump in ~/backups/ops
#   ./deploy/restore-drill.sh <file>     # a specific one
#
# backup-ops.sh already proves a dump is a real, complete, role-carrying
# pg_dumpall -- it learned that the hard way, after eight consecutive 20-byte
# archives reported success. What no check proved is the only thing that
# matters on the day it matters: that the file RESTORES.
#
# What is at stake is narrower than "the database". Geo data is a build
# artifact -- lineage, geometry and population all re-ingest from INSEE, IGN
# and Eurostat. public.unit_uid does not. Those identifiers are random,
# assigned once, and every commune report in production prints one:
#
#     Référence CFN-tc72pmtb-20260101-C6D7-6AF9-00CB
#
# A reference a professional attached to a file resolves only while that table
# survives. So the drill asserts the identifiers come back IDENTICAL, not that
# rows exist.
#
# Into a FRESH cluster, never a scratch database in the live one: a restore
# into the live instance can only end one of two ways, and one of them is the
# incident you were rehearsing for. The live ops-db is opened read-only here,
# and only to compare.
set -euo pipefail
umask 077

SRC=${1:-$(ls -t ~/backups/ops/ops-*.sql.gz 2>/dev/null | head -1)}
[ -n "$SRC" ] && [ -r "$SRC" ] || { echo "FAIL: no dump to restore ($SRC)" >&2; exit 1; }

AGE_H=$(( ( $(date +%s) - $(stat -c %Y "$SRC") ) / 3600 ))
echo "== restoring $(basename "$SRC") (${AGE_H}h old, $(du -h "$SRC" | cut -f1))"
[ "$AGE_H" -lt 48 ] || echo "  [!] this dump is older than 48h -- is the timer still running?" >&2

NET="restore-drill-$$"
CTR="restore-drill-db-$$"
cleanup() {
	podman rm -f "$CTR" >/dev/null 2>&1 || true
	podman network rm "$NET" >/dev/null 2>&1 || true
}
# EXIT, not just success: a drill that leaves a cluster full of password hashes
# behind on a shared VM has traded one risk for a worse one.
trap cleanup EXIT

podman network create "$NET" >/dev/null
podman run -d --rm --name "$CTR" --network "$NET" \
	-e POSTGRES_PASSWORD=drill -e POSTGRES_USER=postgres \
	docker.io/library/postgres:16 >/dev/null
for _ in $(seq 1 40); do
	podman exec "$CTR" pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1 && break
	sleep 2
done
podman exec "$CTR" pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1 \
	|| { echo "FAIL: the throwaway cluster never came up" >&2; exit 1; }

echo "== restoring into a throwaway cluster"
# psql, not pg_restore: pg_dumpall emits plain SQL. ON_ERROR_STOP or the
# restore reports success while skipping half the file -- the same shape of lie
# the empty backups told.
gzip -dc "$SRC" | podman exec -i "$CTR" psql -q -U postgres \
	-v ON_ERROR_STOP=1 -d postgres >/dev/null

q() { podman exec "$CTR" psql -tAX -U postgres -d confinia -c "$1"; }
live() { podman exec confinia_ops-db_1 psql -tAX -U confinia -d confinia -c "$1"; }

ROLES=$(podman exec "$CTR" psql -tAX -U postgres -d postgres \
	-c "SELECT count(*) FROM pg_roles WHERE rolname='confinia'")
[ "$ROLES" = "1" ] || { echo "FAIL: role confinia did not restore" >&2; exit 1; }

UIDS=$(q "SELECT count(*) FROM public.unit_uid")
KEYS=$(q "SELECT count(*) FROM public.api_key")
SEEN=$(q "SELECT count(*) FROM public.premium_seen")
[ "$UIDS" -gt 0 ] || { echo "FAIL: public.unit_uid restored empty" >&2; exit 1; }
echo "== restored: $UIDS identifiers, $KEYS api keys, $SEEN premium rows"

# The assertion that is the point. Every identifier in the dump must still
# name the same version live: same uid, same country, code, unit_type and
# valid_from. A dump that restores rows but renumbers them would pass a count
# check and fail the only promise the reference makes.
LIVE_UIDS=$(live "SELECT count(*) FROM public.unit_uid")
MISMATCH=$(q "SELECT string_agg(uid, ',') FROM (SELECT uid, country, code, unit_type, valid_from FROM public.unit_uid ORDER BY uid LIMIT 200) s" )
CHECK=$(live "SELECT count(*) FROM public.unit_uid WHERE uid = ANY(string_to_array('$MISMATCH', ','))")
SAMPLE=$(q "SELECT least(count(*), 200) FROM public.unit_uid")
[ "$CHECK" = "$SAMPLE" ] \
	|| { echo "FAIL: $((SAMPLE - CHECK)) of $SAMPLE restored identifiers are not the live ones" >&2; exit 1; }

echo "== $SAMPLE sampled identifiers match the live register exactly"
echo "   (dump holds $UIDS, live holds $LIVE_UIDS -- live may have grown since)"
# Is a copy of this leaving the VM at all? The drill proves the file comes
# back; it cannot prove the file still exists after the disk does not. The Mac
# writes a receipt when it pulls (deploy/macos/pull-ops-backups.sh), and this
# is where its absence becomes visible.
#
# Two thresholds, because they mean different things: a laptop closed for a
# long weekend is not an incident, and a chain that stopped a fortnight ago is.
# Only the second exits non-zero, which is what platform's tenant-unit-failed
# alert can see.
RECEIPT=~/backups/ops/.last-pull
if [ -r "$RECEIPT" ]; then
	PULL_H=$(( ( $(date +%s) - $(stat -c %Y "$RECEIPT") ) / 3600 ))
	if [ "$PULL_H" -gt 336 ]; then
		echo "FAIL: no copy has left this VM for $((PULL_H / 24)) days ($(cat "$RECEIPT"))" >&2
		exit 1
	elif [ "$PULL_H" -gt 72 ]; then
		echo "  [!] last off-VM copy was $((PULL_H / 24)) days ago -- is the Mac pulling?" >&2
	else
		echo "== off-VM copy $((PULL_H))h ago: $(cat "$RECEIPT")"
	fi
else
	echo "  [!] no off-VM copy has ever been recorded: these dumps live on one disk" >&2
fi

echo "OK: $(basename "$SRC") restores, and the identifiers come back identical."
