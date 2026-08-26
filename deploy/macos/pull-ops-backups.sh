#!/bin/bash
# Pull the ops dumps OFF the VM. RUNS ON THE MAC, not on the VM -- that is the
# whole point: a dump beside the database it protects survives a bad migration
# and nothing else.
#
#   CONFINIA_REMOTE=confinia@<host> ./pull-ops-backups.sh
#
# The remote is not hardcoded: infrastructure addresses stay out of a public
# repository. Set it in the launchd plist beside this file, or export it.
#
# What is at stake is narrower than "the database". Geo data re-ingests from
# INSEE, IGN and Eurostat. public.unit_uid does not: those identifiers are
# random, assigned once, and every commune report in production prints one. A
# reference a professional filed resolves only while that table exists
# somewhere.
set -euo pipefail

REMOTE=${CONFINIA_REMOTE:-}
[ -n "$REMOTE" ] || { echo "FAIL: set CONFINIA_REMOTE=confinia@<host>" >&2; exit 2; }
DEST=${CONFINIA_BACKUP_DIR:-$HOME/Backups/confinia-ops}
KEEP=${CONFINIA_BACKUP_KEEP:-30}

umask 077
mkdir -p "$DEST"

echo "== pulling from $REMOTE"
# --ignore-existing, not a plain sync: a dump never changes once published, so
# re-fetching one is waste, and DELETING one because the VM pruned it would
# make this an off-site mirror of the VM's retention rather than a backup.
rsync -a --ignore-existing --chmod=600 \
	"$REMOTE:backups/ops/ops-*.sql.gz" "$DEST/"

# Verify what ARRIVED, not what was sent. This project has already shipped
# eight consecutive 20-byte "backups" that reported success; a transfer that
# truncates is the same failure wearing a different coat.
BAD=0
for f in "$DEST"/ops-*.sql.gz; do
	gzip -t "$f" 2>/dev/null || { echo "FAIL: $f is not a valid gzip" >&2; BAD=1; }
done
[ "$BAD" = "0" ] || exit 1

COUNT=$(ls -1 "$DEST"/ops-*.sql.gz 2>/dev/null | wc -l | tr -d ' ')
NEWEST=$(ls -t "$DEST"/ops-*.sql.gz | head -1)
echo "== $COUNT dumps held locally, newest $(basename "$NEWEST")"

# Prune AFTER a verified pull, never before -- the same order backup-ops.sh
# learned to use, for the same reason.
if [ "$COUNT" -gt "$KEEP" ]; then
	ls -t "$DEST"/ops-*.sql.gz | tail -n +$((KEEP + 1)) | while read -r old; do
		rm -f "$old"; echo "   pruned $(basename "$old")"
	done
fi

# Leave a RECEIPT on the VM. Without it the VM cannot tell a laptop that is off
# for the weekend from a backup chain that stopped three weeks ago, and the
# second one is invisible precisely when it matters. restore-drill.sh reads it.
if ssh "$REMOTE" "printf '%s %s %s\n' \"\$(date -u +%FT%TZ)\" '$(hostname -s)' '$COUNT' > backups/ops/.last-pull" 2>/dev/null; then
	echo "== receipt written on the VM"
else
	echo "  [!] could not write the receipt; the VM will report the copy as stale" >&2
fi
echo "OK: $COUNT dumps off the VM, in $DEST"
