#!/bin/bash
# Provision the Polar products + webhook for one environment, from an
# Organization Access Token. Semi-automated and idempotent-ish: products are
# matched by name (created once, updated after), the webhook by URL.
#
# Run SANDBOX first, then PRODUCTION (separate orgs, separate tokens, separate
# ids). Nothing crosses between environments.
#
#   POLAR_ENV=sandbox POLAR_TOKEN=polar_oat_xxx ./deploy/polar/setup-polar.sh
#   POLAR_ENV=prod    POLAR_TOKEN=polar_oat_yyy WEBHOOK_URL=https://api.confinia.io/polar/webhook \
#       ./deploy/polar/setup-polar.sh
#
# The token needs scopes: products:read, products:write, webhooks:read,
# webhooks:write, checkout_links:write. It is read from the environment and
# never written to disk. The script prints the product ids and webhook secret
# to paste into deploy/secrets.env (POLAR_PRODUCT_PRO/ENTERPRISE,
# POLAR_WEBHOOK_SECRET) — those stay out of git.
set -eu

: "${POLAR_TOKEN:?set POLAR_TOKEN (Organization Access Token)}"
ENV="${POLAR_ENV:-sandbox}"
case "$ENV" in
  sandbox) API=https://sandbox-api.polar.sh ;;
  prod)    API=https://api.polar.sh ;;
  *) echo "POLAR_ENV must be sandbox or prod" >&2; exit 2 ;;
esac
WEBHOOK_URL="${WEBHOOK_URL:-https://api.confinia.io/polar/webhook}"
AUTH="Authorization: Bearer $POLAR_TOKEN"
echo "== environment: $ENV ($API)"

# Find an existing item's id by a field match. Logs go to stderr so the
# captured stdout is ONLY the id (a subtle bug once created duplicates because
# a log line leaked into the captured value — keep stdout clean).
find_id() { # stdin=json list under .items ; $1=field ; $2=value
  python3 -c "import sys,json; d=json.load(sys.stdin); \
print(next((i['id'] for i in d.get('items',[]) if i.get(sys.argv[1])==sys.argv[2]),''))" \
    "$1" "$2"
}
json_field() { python3 -c "import sys,json; print(json.load(sys.stdin).get(sys.argv[1],''))" "$1"; }

# --- products (matched by name) ---------------------------------------------
ensure_product() { # $1 name  $2 amount_cents  $3 description ; echoes the id (stdout), logs to stderr
  local name="$1" amount="$2" desc="$3" id
  id=$(curl -sf -H "$AUTH" "$API/v1/products/?is_archived=false" | find_id name "$name")
  if [ -z "$id" ]; then
    id=$(curl -sf -X POST "$API/v1/products/" -H "$AUTH" -H "Content-Type: application/json" \
      -d "{\"name\":\"$name\",\"description\":\"$desc\",\"recurring_interval\":\"month\",
           \"prices\":[{\"amount_type\":\"fixed\",\"price_amount\":$amount,\"price_currency\":\"eur\"}]}" \
      | json_field id)
    echo "  created $name -> $id" >&2
  else
    echo "  exists  $name -> $id" >&2
  fi
  printf '%s' "$id"
}

PRO_ID=$(ensure_product "Confinia Pro" 4900 \
  "Confinia Pro tier: monthly report allowance, professional features. Your API key is upgraded automatically on purchase.")
ENT_ID=$(ensure_product "Confinia Enterprise" 24900 \
  "Confinia Enterprise tier: unlimited reports, bulk exports, team features.")

# --- webhook (matched by URL) ------------------------------------------------
EVENTS='["subscription.created","subscription.updated","subscription.active","subscription.canceled","subscription.uncanceled","subscription.revoked"]'
WID=$(curl -sf -H "$AUTH" "$API/v1/webhooks/endpoints" | find_id url "$WEBHOOK_URL")
if [ -z "$WID" ]; then
  RESP=$(curl -sf -X POST "$API/v1/webhooks/endpoints" -H "$AUTH" -H "Content-Type: application/json" \
    -d "{\"url\":\"$WEBHOOK_URL\",\"format\":\"raw\",\"events\":$EVENTS}")
  WID=$(printf '%s' "$RESP" | json_field id)
  SECRET=$(printf '%s' "$RESP" | json_field secret)
  echo "  webhook created -> $WID" >&2
else
  echo "  webhook exists  -> $WID (secret unchanged; rotate in the dashboard if needed)" >&2
  SECRET="(unchanged)"
fi

cat <<EOF

== paste into deploy/secrets.env (environment: $ENV), never commit:
POLAR_ACCESS_TOKEN=<this token>
POLAR_PRODUCT_PRO=$PRO_ID
POLAR_PRODUCT_ENTERPRISE=$ENT_ID
POLAR_WEBHOOK_SECRET=$SECRET

Checkout links (need checkout_links:write): create them in the dashboard or via
POST $API/v1/checkout-links/ with {"products":["<id>"], "success_url":...}.
EOF
