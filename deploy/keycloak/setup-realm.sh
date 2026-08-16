#!/bin/bash
# IDEMPOTENT bootstrap of the `confinia` realm (issue #19), via the admin
# API: self-registration realm + organization attribute REQUIRED at signup
# + public PKCE client `confinia-web`. Replayable at will; no fragile
# realm export. Run ON THE VM after `up keycloak`.
set -eu
cd "$(dirname "$0")/.."
# CI override: KC_SETUP_URL / KC_SETUP_ADMIN_USER / KC_SETUP_ADMIN_PASS let
# the exact same script run against a throwaway Keycloak (no secrets.env).
KC=${KC_SETUP_URL:-http://127.0.0.1:8095/auth}
# Which realm to configure. `confinia` is production; `confinia-sbx` is the
# sandbox, and it is where anything touching e-mail is proven first -- a broken
# SMTP with verifyEmail on makes registration fail for everyone (issue #132).
# The default matters: with `set -u` a self-referential REALM="$REALM" is an
# unbound variable and the script dies on line 14, which is exactly what a
# blanket ${REALM:-confinia} -> $REALM substitution did to its own declaration.
REALM="${REALM:-confinia}"

ADMIN_USER=${KC_SETUP_ADMIN_USER:-$(grep '^KC_BOOTSTRAP_ADMIN_USERNAME=' secrets.env | cut -d= -f2-)}
ADMIN_PASS=${KC_SETUP_ADMIN_PASS:-$(grep '^KC_BOOTSTRAP_ADMIN_PASSWORD=' secrets.env | cut -d= -f2-)}

echo "== admin token"
TOKEN=$(curl -sf "$KC/realms/master/protocol/openid-connect/token" \
  -d grant_type=password -d client_id=admin-cli \
  --data-urlencode "username=$ADMIN_USER" --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
AUTH="Authorization: Bearer $TOKEN"

echo "== realm $REALM"
if ! curl -sf -H "$AUTH" "$KC/admin/realms/$REALM" >/dev/null 2>&1; then
	curl -sf -X POST "$KC/admin/realms" -H "$AUTH" -H "Content-Type: application/json" -d '{
	  "realm": "'"$REALM"'", "enabled": true,
	  "registrationAllowed": true, "registrationEmailAsUsername": true,
	  "resetPasswordAllowed": true, "rememberMe": true,
	  "sslRequired": "external", "loginWithEmailAllowed": true
	}'
	echo "  realm created"
else
	echo "  realm already present"
fi

echo "== SMTP (issue #132)"
# Keycloak sends the registration confirmation itself; no application code is
# involved. Credentials come from deploy/mail.env, which is gitignored -- never
# from this script, which is committed.
MAIL_ENV="${MAIL_ENV:-mail.env}"
if [ -f "$MAIL_ENV" ]; then
	get() { grep "^$1=" "$MAIL_ENV" | cut -d= -f2- | tr -d '"'"'"'"'; }
	# Reads the SAME variables Grafana uses. One secret, one name: the first
	# version of mail.env asked for the password twice and got it once.
	# GF_SMTP_HOST carries "host:port", which Keycloak wants split.
	SMTP_JSON=$(python3 -c '
import json, sys
hostport, user, pw, frm, name = sys.argv[1:6]
host, _, port = hostport.partition(":")
print(json.dumps({"smtpServer": {
    "host": host, "port": port or "587", "from": frm, "fromDisplayName": name,
    "auth": "true", "user": user, "password": pw,
    "starttls": "true", "ssl": "false",
}}))' "$(get GF_SMTP_HOST)" "$(get GF_SMTP_USER)" \
     "$(get GF_SMTP_PASSWORD)" "$(get GF_SMTP_FROM_ADDRESS)" "$(get GF_SMTP_FROM_NAME)")
	curl -sf -X PUT "$KC/admin/realms/$REALM" -H "$AUTH" \
	  -H "Content-Type: application/json" -d "$SMTP_JSON" >/dev/null \
	  && echo "  SMTP configured from $MAIL_ENV" \
	  || { echo "  [!] SMTP update REJECTED by Keycloak" >&2; exit 1; }
else
	echo "  [!] $MAIL_ENV absent -> SMTP left untouched (registration mails will not be sent)"
fi

echo "== language of the transactional mail"
# Without this Keycloak serves its ENGLISH defaults, and the first message a
# French user gets after signing up reads "Update Your Account — Your
# administrator has just requested that you update your Confinia-sbx account".
# That is wrong twice over: the product is French-first (issue #79), and the
# subject names neither Confinia nor what the reader is meant to do -- it reads
# like phishing, which is how a verification mail gets deleted unread.
curl -sf -X PUT "$KC/admin/realms/$REALM" -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"internationalizationEnabled": true, "defaultLocale": "fr", "supportedLocales": ["fr","en"]}' \
  >/dev/null && echo "  French by default, English available"

echo "== e-mail verification"
# DELIBERATELY NOT automatic. With verifyEmail true and SMTP broken, Keycloak
# fails the registration flow at the send step: nobody can sign up. So: configure
# SMTP, send a test, confirm it ARRIVES, and only then run with VERIFY_EMAIL=1.
#
# ⚠️ Do NOT use the admin console's "Test connection" button to decide that.
# It sends to the LOGGED-IN ADMIN's e-mail address, and the bootstrap admin has
# none -- so it returns 500 "Failed to send email" with a null cause, whatever
# the SMTP settings are. On 2026-08-14 that sent me hunting a configuration
# problem that did not exist: a direct SMTP conversation with the same
# credentials returned `235 Authentication successful` and the message was
# accepted.
#
# The honest check is the real path:
#   PUT /admin/realms/$REALM/users/<id>/execute-actions-email  -d '["VERIFY_EMAIL"]'
# with NO redirect_uri (the client only allows www/staging, and an unlisted one
# fails with "Invalid redirect uri"). 204 means Keycloak sent it.
if [ "${VERIFY_EMAIL:-0}" = 1 ]; then
	curl -sf -X PUT "$KC/admin/realms/$REALM" -H "$AUTH" \
	  -H "Content-Type: application/json" -d '{"verifyEmail": true}' >/dev/null \
	  && echo "  verifyEmail ON"
else
	echo "  verifyEmail left as-is (set VERIFY_EMAIL=1 once a test mail has ARRIVED)"
fi

echo "== organization attribute (required at signup)"
curl -sf -H "$AUTH" "$KC/admin/realms/$REALM/users/profile" \
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
curl -sf -X PUT "$KC/admin/realms/$REALM/users/profile" -H "$AUTH" \
  -H "Content-Type: application/json" --data-binary @/tmp/kc-profile.json >/dev/null
rm -f /tmp/kc-profile.json
echo "  user profile in place"

# Which hosts may complete a login, PER REALM. The sandbox realm exists to be
# used FROM sandbox.confinia.io, and listing only www and staging made that
# impossible: Keycloak answers "Paramètre invalide : redirect_uri" and the
# journey cannot start at all.
#
# Deliberately NOT one shared list. A redirect URI is what stops an attacker
# sending a code to a host they control, so production must never accept the
# sandbox host, and the sandbox realm has no reason to accept www.
case "$REALM" in
	confinia-sbx)
		HOSTS='"https://sandbox.confinia.io/*"'
		ORIGINS='"https://sandbox.confinia.io"'
		LOGOUT="https://sandbox.confinia.io/*" ;;
	*)
		HOSTS='"https://www.confinia.io/*", "https://staging.confinia.io/*"'
		ORIGINS='"https://www.confinia.io", "https://staging.confinia.io"'
		LOGOUT="https://www.confinia.io/*" ;;
esac

echo "== public PKCE client confinia-web (redirects: $HOSTS)"
CID=$(curl -sf -H "$AUTH" "$KC/admin/realms/$REALM/clients?clientId=confinia-web" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id'] if d else '')")
BODY='{
  "clientId": "confinia-web", "protocol": "openid-connect",
  "publicClient": true, "standardFlowEnabled": true,
  "directAccessGrantsEnabled": false, "serviceAccountsEnabled": false,
  "redirectUris": ['"$HOSTS"'],
  "webOrigins": ['"$ORIGINS"'],
  "attributes": {"pkce.code.challenge.method": "S256",
                 "post.logout.redirect.uris": "'"$LOGOUT"'"},
  "protocolMappers": [{
    "name": "organization", "protocol": "openid-connect",
    "protocolMapper": "oidc-usermodel-attribute-mapper",
    "config": {"user.attribute": "organization", "claim.name": "organization",
               "id.token.claim": "true", "access.token.claim": "true",
               "userinfo.token.claim": "true", "jsonType.label": "String"}
  }]
}'
if [ -z "$CID" ]; then
	curl -sf -X POST "$KC/admin/realms/$REALM/clients" -H "$AUTH" \
	  -H "Content-Type: application/json" -d "$BODY"
	echo "  client created"
else
	curl -sf -X PUT "$KC/admin/realms/$REALM/clients/$CID" -H "$AUTH" \
	  -H "Content-Type: application/json" -d "$BODY" >/dev/null
	echo "  client updated"
fi
echo "OK: realm $REALM ready (registration open, organization required)"
