"""Self-service billing portal (issue #81): GET /v1/billing/portal mints a Polar
customer-portal session for the SIGNED-IN caller only. It is gated by the
Keycloak Bearer JWT, so an anonymous or bogus caller is refused (401); the
graceful 503 path (authenticated, but no portal to issue) is covered at the
container level and by the account page's receipt-email fallback."""
import requests


def test_portal_requires_bearer(base):
    r = requests.get(f"{base}/v1/billing/portal")
    assert r.status_code == 401


def test_portal_rejects_garbage_bearer(base):
    r = requests.get(f"{base}/v1/billing/portal",
                     headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401
