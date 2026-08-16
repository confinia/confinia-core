#!/bin/bash
# Launch the dedicated SANDBOX API (issue #51): isolated container on :11420
# (1PESI band, was 8089 — direct swap, the stack was down at migration) with
# the SANDBOX Polar secret (deploy/sandbox.env), a throwaway ops db (confinia_sbx
# in the ops-db instance) and the active color's geo db (read-only). Sandbox
# only — test cards, no real fees. Run ON THE VM after rsync.
set -eu
cd "$(dirname "$0")/.."
PGPW=$(grep '^POSTGRES_PASSWORD=' deploy/secrets.env | cut -d= -f2)
set -a; . deploy/sandbox.env; set +a
ACTIVE=$(cat ~/confinia-edge-state/ACTIVE_COLOR)
podman exec confinia_ops-db_1 psql -U confinia -d confinia -tc \
  "SELECT 1 FROM pg_database WHERE datname='confinia_sbx'" | grep -q 1 || \
  podman exec confinia_ops-db_1 psql -U confinia -d confinia -c 'CREATE DATABASE confinia_sbx OWNER confinia'
podman rm -f confinia-sbx_api 2>/dev/null || true
# The sandbox joins the ACTIVE COLOUR'S NETWORK, and reaches both databases by
# container name — it does NOT run on the host network.
#
# It used to. That worked only while the ops database still published
# 0.0.0.0:5440; the platform audit removed that publish on 2026-08-12, and a
# --network host container has no route left to a name that only resolves
# inside a podman network. The sandbox happened to be stopped that day, so the
# breakage surfaced only when it was next started, as a startup that hung
# forever on "Waiting for application startup" — no error, no exit.
#
# The DSN comment was already right about WHY a container name is needed; the
# network the container ran on made it impossible to honour.
#
# `db` is the colour's own alias on that network, so the sandbox reads whichever
# geo database the active colour serves, with no host port involved.
#
# Keycloak lives on confinia_default, so the sandbox joins that network too and
# reaches it by name. Not through host.containers.internal: Keycloak publishes on
# 127.0.0.1:8095, and the host GATEWAY address is not the host's loopback, so that
# route is refused -- the same distinction that made the ops database unreachable.
# (Comment kept out of the podman run: a # line inside a backslash
# continuation ends the command and the remaining -e flags ran as their
# own commands — the script could not work as committed.)
podman run -d --name confinia-sbx_api --restart unless-stopped \
  --network "confinia-${ACTIVE}_default" --network confinia_default \
  -p 127.0.0.1:11420:8000 \
  -e PG_DSN="postgresql://confinia:${PGPW}@db:5432/confinia" \
  -e OPS_DSN="postgresql://confinia:${PGPW}@confinia_ops-db_1:5432/confinia_sbx" \
  -e VISITOR_SALT_SECRET="sbx-salt" \
  -e POLAR_WEBHOOK_SECRET="$POLAR_WEBHOOK_SECRET" \
  -e POLAR_PRODUCT_PRO="$POLAR_PRODUCT_PRO" \
  -e POLAR_PRODUCT_ENTERPRISE="$POLAR_PRODUCT_ENTERPRISE" \
  -e POLAR_API_BASE="https://sandbox-api.polar.sh" \
  -e POLAR_ACCESS_TOKEN="${POLAR_ACCESS_TOKEN:-}" \
  -e KC_ISSUER="https://sandbox.confinia.io/auth/realms/confinia-sbx" \
  -e KC_DISCOVERY="http://confinia_keycloak_1:8180/auth/realms/confinia-sbx" \
  localhost/confinia-api:latest \
  python -m uvicorn main:app --host 0.0.0.0 --port 8000
sleep 5; curl -sf http://127.0.0.1:11420/healthz && echo " sandbox API up on :11420"
