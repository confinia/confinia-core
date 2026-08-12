#!/bin/bash
# Launch the dedicated SANDBOX API (issue #51): isolated container on :8089 with
# the SANDBOX Polar secret (deploy/sandbox.env), a throwaway ops db (confinia_sbx
# in the ops-db instance) and the active color's geo db (read-only). Sandbox
# only — test cards, no real fees. Run ON THE VM after rsync.
set -eu
cd "$(dirname "$0")/.."
PGPW=$(grep '^POSTGRES_PASSWORD=' deploy/secrets.env | cut -d= -f2)
set -a; . deploy/sandbox.env; set +a
GEO_PORT=$( [ "$(cat ~/confinia-edge-state/ACTIVE_COLOR)" = blue ] && echo 5441 || echo 5442 )
podman exec confinia_ops-db_1 psql -U confinia -d confinia -tc \
  "SELECT 1 FROM pg_database WHERE datname='confinia_sbx'" | grep -q 1 || \
  podman exec confinia_ops-db_1 psql -U confinia -d confinia -c 'CREATE DATABASE confinia_sbx OWNER confinia'
podman rm -f confinia-sbx_api 2>/dev/null || true
podman run -d --name confinia-sbx_api --network host --restart unless-stopped \
  -e PG_DSN="postgresql://confinia:${PGPW}@127.0.0.1:${GEO_PORT}/confinia" \
  # By container name: the ops database publishes no host port since
  # 2026-08-12 (platform audit). 127.0.0.1 inside a container is the
  # container's own loopback, so this line was going to break silently.
  -e OPS_DSN="postgresql://confinia:${PGPW}@confinia_ops-db_1:5432/confinia_sbx" \
  -e VISITOR_SALT_SECRET="sbx-salt" \
  -e POLAR_WEBHOOK_SECRET="$POLAR_WEBHOOK_SECRET" \
  -e POLAR_PRODUCT_PRO="$POLAR_PRODUCT_PRO" \
  -e POLAR_PRODUCT_ENTERPRISE="$POLAR_PRODUCT_ENTERPRISE" \
  -e POLAR_API_BASE="https://sandbox-api.polar.sh" \
  -e POLAR_ACCESS_TOKEN="${POLAR_ACCESS_TOKEN:-}" \
  -e KC_ISSUER="https://sandbox.confinia.io/auth/realms/confinia-sbx" \
  -e KC_DISCOVERY="http://127.0.0.1:8095/auth/realms/confinia-sbx" \
  localhost/confinia-api:latest \
  python -m uvicorn main:app --host 127.0.0.1 --port 8089
sleep 5; curl -sf http://127.0.0.1:8089/healthz && echo " sandbox API up on :8089"
