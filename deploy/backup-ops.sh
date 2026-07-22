#!/bin/bash
# Nightly dump of the PRECIOUS state (ops-db instance: confinia ops tables
# + keycloak identities). Geo databases are NOT backed up: they are build
# artifacts, rebuilt by double ingestion. 14-day local retention; copying
# dumps OFF the VM is a separate concern (see the security review).
set -eu
DEST=~/backups/ops
mkdir -p "$DEST"
STAMP=$(date -u +%Y%m%d-%H%M)
podman exec confinia_ops-db_1 pg_dumpall -U confinia | gzip > "$DEST/ops-$STAMP.sql.gz"
find "$DEST" -name 'ops-*.sql.gz' -mtime +14 -delete
echo "OK: $DEST/ops-$STAMP.sql.gz ($(du -h "$DEST/ops-$STAMP.sql.gz" | cut -f1))"
