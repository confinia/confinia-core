#!/bin/bash
# IDEMPOTENT bootstrap of the `confinia` realm (issue #19), via the admin
# API: self-registration realm + organization attribute REQUIRED at signup
# + public PKCE client `confinia-web`. Replayable at will; no fragile
# realm export. Run ON THE VM after `up keycloak`.
set -eu
cd "$(dirname "$0")/.."
# CI override: KC_SETUP_URL / KC_SETUP_ADMIN_USER / KC_SETUP_ADMIN_PASS let
# the exact same script run against a throwaway Keycloak (no secrets.env).
KC=${KC_SETUP_URL:-http://127.0.0.1:8180/auth}
ADMIN_USER=${KC_SETUP_ADMIN_USER:-$(grep '^KC_BOOTSTRAP_ADMIN_USERNAME=' secrets.env | cut -d= -f2-)}
ADMIN_PASS=${KC_SETUP_ADMIN_PASS:-$(grep '^KC_BOOTSTRAP_ADMIN_PASSWORD=' secrets.env | cut -d= -f2-)}

echo "== admin token"
TOKEN=$(curl -sf "$KC/realms/master/protocol/openid-connect/token" \
  -d grant_type=password -d client_id=admin-cli \
  --data-urlencode "username=$ADMIN_USER" --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
AUTH="Authorization: Bearer $TOKEN"

echo "== realm confinia"
if ! curl -sf -H "$AUTH" "$KC/admin/realms/confinia" >/dev/null 2>&1; then
	curl -sf -X POST "$KC/admin/realms" -H "$AUTH" -H "Content-Type: application/json" -d '{
	  "realm": "confinia", "enabled": true,
	  "registrationAllowed": true, "registrationEmailAsUsername": true,
	  "resetPasswordAllowed": true, "rememberMe": true,
	  "sslRequired": "external", "loginWithEmailAllowed": true
	}'
	echo "  realm created"
else
	echo "  realm already present"
fi

echo "== organization attribute (required at signup)"
curl -sf -H "$AUTH" "$KC/admin/realms/confinia/users/profile" \
  | python3 -c '
import json, sys
p = json.load(sys.stdin)
if not any(a["name"] == "organization" for a in p["attributes"]):
    p["attributes"].append({
        "name": "organization",
        "displayName": "Organization / company",
        "required": {"roles": ["user"]},
        "permissions": {"view": ["user", "admin"], "edit": ["user", "admin"]},
        "validations": {"length": {"min": 2, "max": 120}},
        "multivalued": False,
    })
print(json.dumps(p))' > /tmp/kc-profile.json
curl -sf -X PUT "$KC/admin/realms/confinia/users/profile" -H "$AUTH" \
  -H "Content-Type: application/json" --data-binary @/tmp/kc-profile.json >/dev/null
rm -f /tmp/kc-profile.json
echo "  user profile in place"

echo "== public PKCE client confinia-web"
CID=$(curl -sf -H "$AUTH" "$KC/admin/realms/confinia/clients?clientId=confinia-web" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id'] if d else '')")
BODY='{
  "clientId": "confinia-web", "protocol": "openid-connect",
  "publicClient": true, "standardFlowEnabled": true,
  "directAccessGrantsEnabled": false, "serviceAccountsEnabled": false,
  "redirectUris": ["https://www.confinia.io/*", "https://staging.confinia.io/*"],
  "webOrigins": ["https://www.confinia.io", "https://staging.confinia.io"],
  "attributes": {"pkce.code.challenge.method": "S256",
                 "post.logout.redirect.uris": "https://www.confinia.io/*"},
  "protocolMappers": [{
    "name": "organization", "protocol": "openid-connect",
    "protocolMapper": "oidc-usermodel-attribute-mapper",
    "config": {"user.attribute": "organization", "claim.name": "organization",
               "id.token.claim": "true", "access.token.claim": "true",
               "userinfo.token.claim": "true", "jsonType.label": "String"}
  }]
}'
if [ -z "$CID" ]; then
	curl -sf -X POST "$KC/admin/realms/confinia/clients" -H "$AUTH" \
	  -H "Content-Type: application/json" -d "$BODY"
	echo "  client created"
else
	curl -sf -X PUT "$KC/admin/realms/confinia/clients/$CID" -H "$AUTH" \
	  -H "Content-Type: application/json" -d "$BODY" >/dev/null
	echo "  client updated"
fi
echo "OK: realm confinia ready (registration open, organization required)"
