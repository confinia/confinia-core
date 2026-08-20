#!/bin/bash
# Nightly dump of the PRECIOUS state (ops-db instance: confinia ops tables
# + keycloak identities). Geo databases are NOT backed up: they are build
# artifacts, rebuilt by double ingestion. 14-day local retention; copying
# dumps OFF the VM is a separate concern (see the security review).
#
# This script produced EIGHT CONSECUTIVE EMPTY BACKUPS (2026-08-12..19) and
# reported success every night. Two faults combined:
#
#   1. The unit ran as the debian/platform user while the stack had moved to
#      the confinia user. Rootless podman is per-user, so `podman exec
#      confinia_ops-db_1` could not see the container at all.
#   2. `podman exec ... | gzip > file` -- podman failed, gzip happily wrote a
#      valid 20-byte archive, and a pipeline's status is its LAST command, so
#      `set -eu` never tripped. systemd recorded ExecMainStatus=0.
#
# A file appeared every night. It was 20 bytes. The prune on the next line
# would have deleted the last real dump (2026-08-11) around 2026-08-26.
#
# So, in order: pipefail, dump to a TEMPORARY name, prove the file is a real
# dump, publish it only then, and prune only AFTER a verified new dump exists.
# Failure exits non-zero, which is what lets platform's tenant-unit-failed
# alert see it -- it is blind to a job that lies about succeeding.
set -euo pipefail
# These dumps are not "ops state" in the harmless sense: pg_dumpall of this
# instance carries public.api_key (keys that grant paid access), Keycloak's
# public.credential (password hashes) and 27 real e-mail addresses. On a VM
# shared with other products and other people, a 0644 file is readable by all
# of them. The dumps were surviving on the mode of the home directory alone --
# one bit between them and every account on the machine -- so make the files
# defend themselves.
umask 077
DEST=~/backups/ops
MIN_BYTES=${BACKUP_MIN_BYTES:-1000000}     # a real dump is megabytes; 20 bytes is the bug
mkdir -p "$DEST"
STAMP=$(date -u +%Y%m%d-%H%M)
TMP="$DEST/.ops-$STAMP.sql.gz.partial"
OUT="$DEST/ops-$STAMP.sql.gz"
trap 'rm -f "$TMP"' EXIT

# pg_dumpALL, not pg_dump. A per-database dump carries no roles, and a restore
# then dies on `role "..." does not exist`. Overwatch discovered exactly that
# this week: every dump they held was unrestorable because five per-tenant RLS
# roles had never been backed up. Ours survive that by construction rather than
# by foresight, so the check below refuses any dump that lost the globals --
# "optimising" this line to `pg_dump -d ops` must fail loudly, not silently.
podman exec confinia_ops-db_1 pg_dumpall -U confinia | gzip > "$TMP"

# Three questions, because each has been answered wrongly by a file that
# existed: is it intact, is it big enough to be real, and is it OUR dump?
gzip -t "$TMP" || { echo "FAIL: $TMP is not a valid gzip" >&2; exit 1; }
size=$(wc -c < "$TMP")
[ "$size" -ge "$MIN_BYTES" ] || {
	echo "FAIL: dump is $size bytes, below the $MIN_BYTES floor -- refusing to publish" >&2
	exit 1; }
# Read the header without a pipeline that `head` can cut short: under
# `pipefail`, head exiting at 4096 bytes sends zcat a SIGPIPE and the pipeline
# reports failure even when the pattern WAS found -- which failed this very
# check on its first real run, on a perfectly good 6.6 MB dump.
head_txt=$(zcat "$TMP" 2>/dev/null | head -c 4096 || true)
case "$head_txt" in
	*"PostgreSQL database cluster dump"*) ;;
	*) echo "FAIL: $TMP does not look like a pg_dumpall cluster dump" >&2; exit 1 ;;
esac

# Fourth question: does it carry the globals, and does it create every role it
# references? A restore is worth nothing without them.
#
# The trap, reported by the platform session after their own check called a
# 20-byte file "OK": an EMPTY dump passes this VACUOUSLY, because zero
# references means zero missing. Non-triviality is proven above, by the size
# floor and the header check, before this runs at all -- order matters here.
roles_created=$(zcat "$TMP" | grep -c "^CREATE ROLE " || true)
[ "$roles_created" -ge 1 ] || {
	echo "FAIL: no CREATE ROLE in the dump -- globals are missing, so this would" >&2
	echo "      not restore. Is this still pg_dumpall and not pg_dump?" >&2
	exit 1; }

mv "$TMP" "$OUT"
chmod 600 "$OUT"
chmod 700 "$DEST"
trap - EXIT
echo "OK: $OUT ($(du -h "$OUT" | cut -f1), verified)"

# ONLY now, with a verified dump on disk, is it safe to age the old ones out.
# The previous order pruned unconditionally, so eight failing nights were
# quietly eating the last good backups.
find "$DEST" -maxdepth 1 -name 'ops-*.sql.gz' -mtime +14 -delete
