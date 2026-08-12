#!/bin/bash
# Bring up the dedicated STAGING stack (issue #113).
#
#   ./deploy/staging-up.sh     # (re)start staging against the passive colour
#
# Staging is no longer the passive colour. Until 2026-08-12 it WAS, which made
# it two things at once: the environment you exercise and the one production
# rolls back to. So a staging test wrote into production's operational tables --
# api_usage, premium_seen, api_key -- and could consume a paying customer's
# quota. The API creates its tables on boot, so a schema change tried on staging
# was applied to the PRODUCTION ops database the moment the container started.
#
# SEPARATE : the operational database (its own `confinia_staging`), the identity
#            realm (confinia-sbx), the billing mode (Polar test).
# SHARED   : the passive colour's geo database, READ-ONLY in practice -- it is a
#            build artefact and every write in api/main.py targets OPS_DSN.
#            tests/test_staging_isolation.py asserts that premise still holds.
#
# PORT 8403 is inside confinia's own 84xx band (platform table: "confinia GREEN
# + staging"). It was 8501 for a few hours on 2026-08-12 -- self-assigned from a
# band I had only checked was FREE, not whether it was MINE. 85xx belongs to
# panoramax. "Free" is not "available", which is exactly the reasoning that cost
# us 8092: another tenant checked the same way.
# If 84xx ever fills up, request an extension via confinia/platform. Never
# self-assign.
#
# The ops database is reached BY CONTAINER NAME, not through a published host
# port. Proven on 2026-08-12: confinia_ops-db_1 resolves on the colour network
# and answers on 5432, so nothing needs to be exposed on the host at all. The
# colour APIs and Keycloak were moved the same way on 2026-08-12, and the
# database no longer publishes a host port at all.
#
# Uses `podman run` rather than compose, following deploy/sandbox-up.sh: the
# container must join the PASSIVE colour's network to reach its database, and
# which colour that is changes at every promotion. RE-RUN THIS AFTER A PROMOTION.
set -eu
cd "$(dirname "$0")/.."

PORT=8403
NAME=confinia-staging_api_1
OPS_DB=confinia_staging

active() { cat ~/confinia-edge-state/ACTIVE_COLOR 2>/dev/null || echo blue; }
PASSIVE=$([ "$(active)" = blue ] && echo green || echo blue)
NET="confinia-${PASSIVE}_default"

PW=$(grep '^POSTGRES_PASSWORD=' deploy/secrets.env | cut -d= -f2-)

echo "== staging reads the geo database of the PASSIVE colour ($PASSIVE, network $NET)"
echo "== staging writes to its OWN operational database ($OPS_DB)"

# Its own database, created once. The API builds its schema on boot, which is
# also precisely why pointing it at the production database was so damaging.
podman exec confinia_ops-db_1 psql -U confinia -d postgres -tAc \
	"SELECT 1 FROM pg_database WHERE datname='${OPS_DB}'" | grep -q 1 \
	|| podman exec confinia_ops-db_1 createdb -U confinia "${OPS_DB}"

# The guard from #123: never destroy a working container for a port we cannot
# get back.
if ss -ltn 2>/dev/null | grep -qE "127\.0\.0\.1:$PORT " \
   && ! podman ps --format '{{.Names}}' | grep -qx "$NAME"; then
	echo "REFUSING: 127.0.0.1:$PORT is held by something that is not $NAME" >&2
	exit 1
fi

podman rm -f "$NAME" >/dev/null 2>&1 || true
podman run -d --name "$NAME" --network "$NET" \
	-p "127.0.0.1:${PORT}:8000" \
	--env-file deploy/secrets.env \
	-e PG_DSN="postgresql://confinia:${PW}@db:5432/confinia" \
	-e OPS_DSN="postgresql://confinia:${PW}@confinia_ops-db_1:5432/${OPS_DB}" \
	-e KC_ISSUER="https://www.confinia.io/auth/realms/confinia-sbx" \
	-e POLAR_MODE=sandbox \
	-e CONFINIA_ENV=staging \
	--restart unless-stopped \
	localhost/confinia-api:latest >/dev/null

for i in $(seq 1 60); do
	curl -sf --max-time 3 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && {
		echo "OK: staging on :$PORT — try https://staging.api.confinia.io"; exit 0; }
	sleep 2
done
echo "FAILURE: /healthz on $PORT not responding after 120 s" >&2
podman logs --tail 15 "$NAME" >&2 || true
exit 1
