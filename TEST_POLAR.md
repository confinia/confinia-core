# TEST_POLAR — the Pro subscription journey

This document describes HOW the "a user subscribes to a Pro account"
journey is validated, and carries the LATEST results of the automated
validation. Polar (the merchant of record) is simulated with **webhooks
signed with the exact same mathematics as production** (Standard
Webhooks: HMAC-SHA256 of `id.timestamp.body`, base64 secret); the
application, the signature verification and the provisioning under test
are EXACTLY the code running in production. The secret and product ids
are dedicated CI test values.

## The journey covered

1. **Security first**: an unsigned webhook is rejected (401), a tampered
   signature is rejected (401), an unknown product is ignored with no
   side effect.
2. **Purchase**: a `subscription.active` webhook (Pro product) for an
   email → the EXISTING key of that email moves to the `pro` tier.
3. **Order-independent**: a key created AFTER the purchase is born `pro`
   (buyers can pay first and create their key later).
4. **Tier effect**: with a pro key, `/v1/changes` answers
   `remaining: unlimited` (no quota).
5. **Hierarchy**: an active Enterprise subscription outranks Pro;
   cancelling only the Enterprise falls back to the still-active Pro.
6. **Cancellation**: a `subscription.revoked` webhook for the Pro → the
   email drops to `free`, its keys become quota-bound callers again.

Not covered here (depends on live infrastructure, hand-verified during
the 2026-07-21 wiring): actual webhook delivery by Polar to
`https://api.confinia.io/polar/webhook` (endpoint configured at Polar,
shared secret in `deploy/secrets.env`) and the hosted checkout
(`buy.polar.sh`, links on `/pricing`).

## Where and when it runs

- **GitHub Actions CI**: workflow
  [`subscription-tests`](.github/workflows/subscription-tests.yml),
  "Polar pro journey" step. Triggers: every push to `main`, every pull
  request, weekly (Monday 05:17 UTC), and on demand.
- **Files**: [`tests/test_polar.py`](tests/test_polar.py), shared
  fixtures with the signup journey.

## Latest results

| Date (UTC) | Trigger | Result | Detail |
|---|---|---|---|
| 2026-07-22 10:16 | pull request #24 (first run) | ✅ **8/8 passed** (0.06 s) | [run 29911277810](https://github.com/confinia/confinia-core/actions/runs/29911277810): unsigned/tampered rejected, unknown product ignored, purchase upgrades existing AND future keys, unlimited premium, enterprise outranks pro, cancellation demotes |

Full history: repository Actions tab, `subscription-tests` workflow.
