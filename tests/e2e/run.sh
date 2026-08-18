#!/bin/bash
# End-to-end subscribe-and-pay against the Creem TEST store (issue #208).
#   tests/e2e/run.sh
#
# TWO engines, on purpose. Creem's hosted checkout guards its card input with a
# fraud/bot layer that refuses to render under Selenium standalone-chrome
# (proven: 30 s, the card iframe never loads; Playwright loads it at once). So:
#
#   Stage A -- Selenium IDE (journey.side): opens the checkout, fills payer
#     details, and asserts the PAYMENT step is reached. This is the maintainable
#     .side artifact, deterministic and green, covering the part Selenium can do.
#   Stage B -- Playwright (pay_and_verify.py): completes the card entry Selenium
#     cannot, then takes the verdict from the PROVIDER's API -- a browser
#     reaching a success screen is not proof the subscription exists.
#
# TEST-only by construction: a live API key is refused, the verdict refuses any
# environment but test. A rehearsal cannot touch a live store by accident.
set -eu
cd "$(dirname "$0")"
[ -r .env ] || { echo "tests/e2e/.env missing -- copy .env.example and fill it" >&2; exit 2; }
set -a; . ./.env; set +a
case "${CREEM_API_KEY:-}" in
	creem_test_*) : ;;
	*) echo "CREEM_API_KEY must be a creem_test_ key; refusing" >&2; exit 2 ;;
esac
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"

echo "== stage A: Selenium IDE reaches the payment step"
CHECKOUT_URL=$(podman run --rm -i --network host \
	-e KEY="$CREEM_API_KEY" -e PROD="$CREEM_PRODUCT" -e EMAIL="$E2E_EMAIL" -e UA="$UA" \
	docker.io/library/python:3.12-slim python - <<'PY'
import json, os, urllib.request
body = json.dumps({"product_id": os.environ["PROD"], "customer": {"email": os.environ["EMAIL"]}}).encode()
req = urllib.request.Request("https://test-api.creem.io/v1/checkouts", data=body,
    headers={"x-api-key": os.environ["KEY"], "content-type": "application/json",
             "user-agent": os.environ["UA"]})
print(json.load(urllib.request.urlopen(req, timeout=30))["checkout_url"])
PY
)
[ -n "$CHECKOUT_URL" ] || { echo "no checkout URL returned" >&2; exit 1; }
echo "   $CHECKOUT_URL"
sed "s#__CHECKOUT_URL__#${CHECKOUT_URL}#" journey.side > /tmp/journey.side

podman rm -f e2e-chrome >/dev/null 2>&1 || true
podman run -d --name e2e-chrome --network host --shm-size=1g \
	docker.io/selenium/standalone-chrome:4 >/dev/null
for _ in $(seq 1 30); do
	curl -sf http://127.0.0.1:4444/status >/dev/null 2>&1 && break
	sleep 2
done
set +e
podman run --rm --network host -v /tmp/journey.side:/journey.side:ro \
	-e SELENIUM_REMOTE_URL=http://127.0.0.1:4444 \
	docker.io/library/node:22 \
	npx -y selenium-side-runner \
	  -c "browserName=chrome goog:chromeOptions.args=[--no-sandbox,--disable-dev-shm-usage]" \
	  /journey.side
side_rc=$?
set -e
podman rm -f e2e-chrome >/dev/null 2>&1 || true
[ "$side_rc" = 0 ] || { echo "stage A FAILED (rc=$side_rc)"; exit "$side_rc"; }
echo "   stage A green: the checkout link reaches payment"

echo "== stage B: complete the card payment and verify with the provider"
podman run --rm --network host \
	-e CREEM_ENV="$CREEM_ENV" -e CREEM_API_KEY="$CREEM_API_KEY" \
	-e CREEM_PRODUCT="$CREEM_PRODUCT" -e E2E_EMAIL="$E2E_EMAIL" \
	-v "$PWD/pay_and_verify.py:/pay.py:ro" \
	mcr.microsoft.com/playwright/python:v1.49.0-noble \
	sh -c "pip install -q playwright==1.49.0 2>/dev/null; python /pay.py"
