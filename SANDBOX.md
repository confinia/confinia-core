# SANDBOX.md — manual test guide for the dedicated sandbox

A full, isolated clone of production at **https://sandbox.confinia.io**, running
Polar in **sandbox mode** (Stripe test cards, no real money). Use it to walk the
whole signup + subscription journey safely. Nothing here touches production.

## What is isolated

| Piece | Sandbox | Prod |
|---|---|---|
| Host | sandbox.confinia.io | www / api.confinia.io |
| API | dedicated container `:8009` (`deploy/sandbox-up.sh`) | blue/green stacks |
| Ops database | throwaway `confinia_sbx` | shared ops db |
| Identity realm | `confinia-sbx` (test accounts only) | `confinia` |
| Polar | sandbox org (test cards) | production org |
| Access | basic auth (webhook exempt) | public |

## Access

- URL: **https://sandbox.confinia.io/account.html**
- Basic auth: user **`confinia`**, password = the staging/sandbox password in
  `deploy/secrets.env` (`STAGING_*`, never in the repo). The webhook path
  `/polar/webhook` stays public so Polar can reach it.

## Manual steps

1. **Open** https://sandbox.confinia.io/account.html and pass the basic-auth
   prompt (`confinia` / the sandbox password).
2. **Create an account**: click *Sign in / Create account* → the Keycloak
   registration form. Fill:
   - **Email**: use an address you can check, e.g. `you+sbx1@yourmail.com`
     (a fresh one each run keeps tests clean; `+sbx1`, `+sbx2`, …).
   - **Password**, **First / Last name**.
   - **Organization / company**: required (e.g. `Ville de Testville`).
3. Back on the account page you should see: your **email**, your
   **organization**, plan **FREE**, and your **API key**.
4. **Upgrade to Pro**: click *Subscribe · Pro €49/mo*. The Polar checkout opens
   as an **overlay on the page** (no redirect), with your email prefilled.
5. **Pay with a Stripe test card**:
   - Success: `4242 4242 4242 4242`, any future expiry, any CVC, any postcode.
   - (Decline test: `4000 0000 0000 0002`.)
   A banner confirms *"payments are not processed"* — no real charge.
6. **Reload** the account page (button *Reload*). Your plan flips to **PRO**
   automatically — the Polar sandbox webhook upgraded the key bound to your
   email. This is the full free → pro provisioning, end to end.

## What to check while testing

- The registration form is clear and the **organization** field is required.
- The checkout feels like it stays **on confinia.io** (overlay, no visible
  redirect to Polar); the email is prefilled so the upgrade matches your key.
- After paying, the plan flips **without any manual step**.

## Test data reference

- **Success card**: `4242 4242 4242 4242` · any future date · any CVC.
- **Decline card**: `4000 0000 0000 0002`.
- **Emails**: any address; sub-addressing (`+sbx1`) gives you disposable,
  trackable variants. Test accounts live in the `confinia-sbx` realm only.

## Operate the sandbox

- Bring the sandbox API up (VM): `./deploy/sandbox-up.sh` (isolated container on
  `:8009`, sandbox Polar secret from `deploy/sandbox.env`, throwaway
  `confinia_sbx` ops db, active-color geo read-only).
- Sandbox Polar products/webhook are provisioned with
  `POLAR_ENV=sandbox … ./deploy/polar/setup-polar.sh` (see POLAR.md).
- Reset test data: drop and recreate the `confinia_sbx` database, and delete
  test users in the `confinia-sbx` realm.

## Going to production later

When ready to charge real money, the SAME journey runs on prod: activate the
prod Polar org (account activation + payout KYC), keep /pricing and /account on
the prod checkout links, deploy, and run `tests/smoke_prod.py`. The sandbox is
the rehearsal; prod is the same play on the real Polar org.
