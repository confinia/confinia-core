# ENVIRONMENTS — sandbox, staging, production

Three environments, three clear jobs. Work flows left to right; only
production faces end users and only production touches real money.

| | **Sandbox** | **Staging** | **Production** |
|---|---|---|---|
| **Job** | act with **no accounting impact**; validate in a **short loop** | validate the prod-bound build **before opening the gates** to end users | **deliver** the work to end users; close issue + PR |
| **Host** | sandbox.confinia.io | staging.confinia.io · staging.api.confinia.io | www.confinia.io · api.confinia.io |
| **Access** | basic auth | basic auth | public |
| **Payments (Polar)** | **sandbox mode** — Stripe test cards, no real charge | prod config, but **payments are tested in sandbox, not here** | **production** — real cards, real money |
| **Data / API** | fully **isolated**: own API (:8089), throwaway ops db `confinia_sbx`, realm `confinia-sbx` | the **passive color** (candidate), shared ops db, realm `confinia` | the **active color**, shared ops db, realm `confinia` |
| **Identity** | realm `confinia-sbx`, auth on sandbox host | realm `confinia` | realm `confinia` |
| **Banner** | SANDBOX | STAGING | none |
| **Deploy** | direct (rsync + relaunch), fast iteration | `deploy-api.sh stage` → serves the passive color | `deploy-api.sh promote` → passive becomes active |

## What each is for

- **Sandbox** is the safe playground. Do anything here — test payments with
  card `4242 4242 4242 4242`, break data, retry — nothing hits accounting and
  nothing reaches end users. It is fully isolated, so it is also the fastest
  short loop for trying a change end to end. See SANDBOX.md.
- **Staging** is the human validation gate. It serves the exact build that is
  one command away from production (the passive color), behind basic auth, so
  you confirm behaviour and UX before flipping it to end users. Real-payment
  side effects are NOT exercised here — that is the sandbox's job.
- **Production** is where the work is valorised: promoted to end users, and the
  issue + PR are closed. Real money flows only here.

## The flow (a change from idea to end users)

1. **Build** on a branch; open a **draft PR** to track progress (RULES 7).
2. **Sandbox**: exercise the change end to end, payments included, no impact.
3. **Staging**: `deploy-api.sh stage`; hand over the staging links (RULES 1);
   validate the prod-bound build; run `tests/smoke_prod.py` against it.
4. **Production**: `deploy-api.sh promote`; run the post-deploy smoke suite
   (RULES 6); publish the demo to both surfaces (RULES 2); **close issue + PR**.

## Coherence rules

- **Real money only in production.** Sandbox is Polar-sandbox; staging never
  runs a real test payment (use the sandbox for that).
- **Every non-production surface is behind basic auth** and shows an
  environment banner, so no one mistakes it for the live product.
- **Links stay in their environment**: pages are host-relative so a sandbox
  page never sends the user to production (and vice versa).
- **Ports** live in Confinia's reserved range (see PORTS.md); no generic host
  ports shared with other VM products.
