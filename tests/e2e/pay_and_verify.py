"""Complete the card payment that Selenium cannot, then take the server verdict.

Creem's hosted checkout guards its card input with a fraud/bot layer (Sardine,
HumanSecurity, hCaptcha) that refuses to render the card iframe under Selenium
standalone-chrome -- proven: 30 s and it never loads, while Playwright chromium
loads it at once. So the card half of the journey runs here, on Playwright, and
the RESULT is checked against the provider, never the page.

Reads CREEM_API_KEY (test only), CREEM_PRODUCT, E2E_EMAIL from the environment.
Exit 0 only if an active subscription exists for the email afterwards.
"""
import json
import os
import re
import sys
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


def _api(path, data=None):
    key = os.environ["CREEM_API_KEY"]
    req = urllib.request.Request(
        "https://test-api.creem.io" + path,
        data=json.dumps(data).encode() if data else None,
        headers={"x-api-key": key, "content-type": "application/json",
                 "accept": "application/json", "user-agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> int:
    if os.environ.get("CREEM_ENV", "test") != "test" \
       or not os.environ["CREEM_API_KEY"].startswith("creem_test_"):
        print("refusing: test environment and a creem_test_ key only")
        return 2
    email = os.environ["E2E_EMAIL"].strip().lower()
    url = _api("/v1/checkouts", {"product_id": os.environ["CREEM_PRODUCT"],
                                 "customer": {"email": email}})["checkout_url"]
    print(f"  checkout: {url}")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--no-sandbox"])
        p = b.new_context(viewport={"width": 1280, "height": 1500},
                          user_agent=UA).new_page()
        p.goto(url, wait_until="networkidle", timeout=60000)
        p.wait_for_timeout(4000)
        p.locator("input[placeholder='John Doe']").fill("E2E Run")
        p.locator("select").first.select_option("FR")
        p.wait_for_timeout(600)
        p.get_by_role("button", name=re.compile("Continue to payment", re.I)).click()
        p.wait_for_timeout(6000)
        p.locator("input[name=cardHolderName]").fill("E2E Run")
        card = None
        for _ in range(8):
            for fr in p.frames:
                if "card-form" in fr.url:
                    card = fr
                    break
            if card:
                break
            p.wait_for_timeout(2000)
        if not card:
            print("  card iframe never loaded even under Playwright")
            b.close()
            return 1
        card.locator("input[name=number]").fill("4111 1111 1111 1111")
        card.locator("input[name=expirationDate]").fill("12/30")
        card.locator("input[name=cvv]").fill("123")
        p.wait_for_timeout(800)
        p.get_by_role("button", name=re.compile(r"Pay\s*€", re.I)).first.click()
        try:
            p.wait_for_url(re.compile(r"/return"), timeout=40000)
            print("  hosted page: payment confirmed")
        except Exception:
            print("  hosted page did not confirm")
        b.close()

    # The verdict is the provider's, not the page's -- and Creem records the
    # subscription a little AFTER the redirect, so poll rather than read once.
    import time
    for attempt in range(10):
        subs = _api("/v1/subscriptions/search?page_number=1&page_size=50").get("items", [])
        active = [s for s in subs
                  if ((s.get("customer") or {}).get("email") or "").lower() == email
                  and s.get("status") in ("active", "trialing")]
        if active:
            print(f"  VERDICT: {len(active)} active subscription(s) for {email} "
                  f"(after {attempt * 3}s)")
            return 0
        time.sleep(3)
    print(f"  VERDICT: no active subscription for {email} after 30s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
