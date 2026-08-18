# E2E subscribe-and-pay harness (issue #208)

Rehearses the whole payment journey against the **Creem test store** and checks
the outcome against the provider, not the page. `tests/e2e/run.sh`, everything
containerised (DEV.md).

## Why two engines

Creem's hosted checkout guards its card input with a fraud/bot layer (Sardine,
HumanSecurity, hCaptcha). Measured: under Selenium standalone-chrome the card
iframe **never renders** — 30 s and it is still absent — while Playwright
chromium loads it at once. That is a property of the target, not a harness
defect. So the harness splits along the line Selenium can hold:

- **Stage A — Selenium IDE** (`journey.side`, the requested `.side` artifact):
  opens the checkout, fills payer details, asserts the **payment step is
  reached** (the priced Pay button appears). Deterministic and green. This is
  the part a maintainer edits in Selenium IDE.
- **Stage B — Playwright** (`pay_and_verify.py`): completes the card entry
  Selenium cannot, then polls the **provider's subscription API** — a browser
  reaching a success screen is not proof a subscription exists.

## Test-only by construction

`run.sh` refuses anything but a `creem_test_` key; the verdict refuses any
environment but `test`. A rehearsal cannot touch a live store by accident.

## Running it

```
cp tests/e2e/.env.example tests/e2e/.env    # fill CREEM_API_KEY (test), CREEM_PRODUCT, E2E_EMAIL
./tests/e2e/run.sh
```

`.env` is gitignored — it carries the test key. Use a fresh `E2E_EMAIL` per run
so the verdict is unambiguous.

## What it caught

The first real signed webhook (driven by this journey) 500'd: the Creem handler
keyed its upsert on `(email, tier)`, a unique constraint that exists in no
environment. No unit test had sent a payload the live schema rejects; the e2e
run did (#217).
