# SPECIFICATIONS — what the Confinia SaaS is expected to do

Product reference document. When an implementation and this document
diverge, either this document is amended EXPLICITLY or the code is brought
back in line; silent drift is not an option. Execution details live in
GitHub issues, work state in TODO.md, working rules in RULES.md.

## 1. The product

An API (and its web surfaces) answering one question with full
traceability: **"which administrative unit existed HERE, at THAT date?"**
The temporal model is the backbone: every unit is a sequence of versions
(code, name, validity period, dated geometry); every change is a dated,
sourced event (merger, split, rename, creation, dissolution,
re-establishment).

- Coverage: France at exact dates back to 1870 (INSEE + IGN, TRF-GIS
  before 1943), Europe through national editions and Eurostat, the UK at
  exact legal dates (ONS), New Zealand (Stats NZ). Country depth follows
  demand, not the other way around.
- Every served fact carries its provenance (source, licence, attribution):
  `data_source` registry, `/v1/attributions`, attributions embedded in
  exports and reports.
- No ODbL source anywhere in the chain (OHM and commercial reuse without
  contamination); no source containing corrupted characters.

## 2. The surfaces

| Surface | Role |
|---|---|
| `api.confinia.io` | the public versioned API (`/v1/...`), GeoJSON |
| `www.confinia.io` | the time-slider demo as homepage; `/about` (pitch), `/pricing` (purchase), `/commune/<code>` (detailed record), `/blog`, `/grafana` (application observability), `/auth` (identity) |
| `confinia.github.io` | public mirror of the demo: historical target of every published link, always shipped together with the VM mirror |
| `staging.confinia.io` + `staging.api.confinia.io` | human validation gate before promotion, basic auth, always serving the passive color |

## 3. The business model (fully self-service)

The service must run **entirely self-service, with no human in the loop**:
discovery, purchase, provisioning, upgrade and churn all happen without
manual action.

- **Free**: the whole API, fair-use rate limits, 9 premium reports
  included (lifetime, per caller), source attribution required.
- **Pro (49 EUR/month, launch price)** and **Enterprise (249 EUR/month)**:
  self-service purchase through Polar as merchant of record (checkout,
  EU VAT, invoices, dunning and compliance handled by the MoR).
- Premium products are **unit-value artifacts**: area-change report
  (`/v1/changes`), downloadable citable commune record (SVG/PDF), bulk
  exports (planned). Pattern: a free taste (9 reports), then paid
  (HTTP 402 pointing to `/pricing`).
- Provisioning is automatic both ways: signed Polar webhook applies the
  purchased tier to every key of the buyer's email (existing and future);
  cancellation demotes back to free.
- Paid tiers will move from "unlimited" to **per-day allowances**; the
  `/pricing` copy must be aligned on the day that changes (issue #19,
  phase 3).

## 4. Identity and organizations (issue #19)

- Sign-up and sign-in from the frontend, managed by **Keycloak**
  (realm `confinia`, served under `www.confinia.io/auth`).
- At registration the user **declares an organization** (company, tenant):
  mandatory profile attribute.
- Once signed in: account page (profile, API key bound to the token's
  email, Polar checkout prefilled with that email so the webhook links the
  purchase automatically).
- The organization eventually becomes the tenant dimension in metering.

## 5. Operations

- **Full blue/green**: two complete stacks (each with its own geo
  database); the geo database is a rebuildable artifact produced by
  **double ingestion** (never copied between colors); precious state
  (keys, usage, intents, subscriptions, identities) lives in the shared
  ops database.
- **Human gate**: every deployment goes through staging (passive color,
  test links handed over systematically) before promotion; one-command
  rollback.
- **GitHub flow**: every change is an issue + a PR, merged after staging
  validation; tagged releases, version visible everywhere (healthz,
  frontend footer).
- **Observability without personal data**: IP addresses are never stored;
  countries via GeoIP, unique visitors via salted daily hashes; 404s
  tracked in Grafana and fed back into the edge filters (see
  MONITORING.md).
- **Automated tests of the revenue journeys** (issue #18): signup and
  Polar provisioning tested end to end in CI (TEST_SUBSCRIPTION.md,
  TEST_POLAR.md).
