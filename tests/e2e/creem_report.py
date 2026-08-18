"""Server-side verdict for the subscribe-and-pay journey (issue #208).

The hosted page saying "Thank you for subscribing" proves the browser reached a
success screen. It does NOT prove the provider recorded a subscription, nor that
our webhook flipped the tier -- and the report must state what the PROVIDER
holds, checked against its sandbox API, not what a page displayed.

Exit codes, and they are the point:
  0  an active subscription exists for the email        -> the journey worked
  1  no active subscription                             -> it FAILED, even if
                                                            the page looked right
  2  cannot tell (auth, network)                        -> "could not check"
                                                            must never read as
                                                            "checked, fine"
"""
import json
import os
import sys
import urllib.request


def _api(base, key, path):
    req = urllib.request.Request(base + path, headers={
        "x-api-key": key, "accept": "application/json",
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> int:
    env = os.environ.get("CREEM_ENV", "test")
    if env != "test":
        print("VERDICT: refusing to check anything but the test environment")
        return 2
    key = os.environ["CREEM_API_KEY"]
    if not key.startswith("creem_test_"):
        print("VERDICT: the key is not a creem_test_ key; refusing")
        return 2
    email = os.environ["E2E_EMAIL"].strip().lower()
    base = "https://test-api.creem.io"

    try:
        subs = _api(base, key, "/v1/subscriptions/search?page_number=1&page_size=50")
    except Exception as e:
        print(f"VERDICT: cannot tell ({type(e).__name__}: {str(e)[:80]})")
        return 2

    items = subs.get("items", [])
    mine = [s for s in items
            if ((s.get("customer") or {}).get("email") or "").lower() == email]
    active = [s for s in mine if s.get("status") in ("active", "trialing")]
    print(f"VERDICT: {len(mine)} subscription(s) for {email}, {len(active)} active")
    for s in mine:
        prod = (s.get("product") or {})
        print(f"  {s.get('id')}  status={s.get('status')}  "
              f"product={prod.get('name') if isinstance(prod, dict) else prod}")
    return 0 if active else 1


if __name__ == "__main__":
    sys.exit(main())
