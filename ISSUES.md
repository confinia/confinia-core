# ISSUES.md — where every open issue actually stands

One line per open issue, kept current **every time work touches an issue or a
PR** (RULES 14). GitHub says whether an issue is open; it does not say whether
the work is deployed, and "merged" has been mistaken for "live" here before.

**Where can I try it** uses the four answers of RULES 12: **nowhere** ·
**sandbox** · **staging** · **production**.

Last updated: **2026-08-16**, `main` at `d5a8f28`.
Active colour: **blue** on `:8091` (production) · passive: **green** on `:8402`.
**Band 11xxx (1PESI): migration step 2b done except the two that need the
founder.** Grafana, otel, Keycloak, green, staging and sandbox all answer on
their 11xxx port (verified with `ss`). **Blue (11120/11130) waits on the
promotion** — it is the active colour — and the **app caddy (11000) is
recreated last**, because the edge flip depends on it and the last caddy
recreate cost 46 seconds of public downtime.

## Open

| # | Issue | Stage | Where can I try it |
|---|---|---|---|
| [#123](https://github.com/confinia/confinia-core/issues/123) | Colour port publisher problems | **half solved.** Cause 1 — another tenant squatted 8092/8093 — **fixed**: green moved to band 84xx, burned ports recorded, and `deploy-api.sh` now refuses to destroy a container for a port it cannot get back. Cause 2 — the publisher stops some minutes after start, on an uncontested port — **open**; correlates with other tenants' throwaway containers on the shared `debian` podman, which points at #99 | staging works again; `deploy-staging` green on `6a88b86` |
| [#132](https://github.com/confinia/confinia-core/issues/132) | Transactional email | **ops alerts DELIVERED**; **registration mail sent from the sandbox realm 2026-08-14** (`execute-actions-email` → 204). Awaiting the founder's confirmation that both arrived, then `VERIFY_EMAIL=1` | **production** (alerts) · **sandbox** (registration) |
| [#127](https://github.com/confinia/confinia-core/issues/127) | Report: colour what a boundary gained and lost — refuse to draw when the data cannot support it | issue created, **not started**; blocked by missing historical geometry, not by rendering | nowhere |
| [#121](https://github.com/confinia/confinia-core/issues/121) | Commune report: make it a document an expert office would sign | issue created, **not started** | nowhere |
| [#115](https://github.com/confinia/confinia-core/issues/115) | Adopt STACK_template.md: close the documented gaps | issue created, tracking others | nowhere (umbrella) |
| [#114](https://github.com/confinia/confinia-core/issues/114) | **SECURITY** — CI runner ran as `debian`, passwordless root | **DONE 2026-08-12**: runs as `confinia` via a user-level unit; `deploy-staging` asserts `sudo -n` fails on every run | **production** — the next `deploy-staging` run |
| [#113](https://github.com/confinia/confinia-core/issues/113) | Staging needs its own stack and DB; today it writes into production data | issue created, **not started** | nowhere |
| [#111](https://github.com/confinia/confinia-core/issues/111) | Sandbox and staging need their own working directories | **static files DONE 2026-08-12**: `~/staging/confinia` and `~/sandbox/confinia`, mounted separately; proven a staging edit does not reach www. Deploying a PR branch to the sandbox API is the remaining half | **staging** |
| [#99](https://github.com/confinia/confinia-core/issues/99) | Move the stack to its own Unix user | **DONE 2026-08-11/12.** ~23 min downtime, every count matched first time; green rebuilt by double ingestion and both colours now agree exactly (205 370 / 2 128 / 1 285 119). Old volumes kept until #114 lands | **production** — the whole stack runs as `confinia`, rollback target restored |
| [#91](https://github.com/confinia/confinia-core/issues/91) | Italy: temporal model, then historical demography | **half delivered** — lineage merged (PR #120); demography not started | **staging** — 2 128 dead comuni now route to a successor, 1865-2024 |
| [#90](https://github.com/confinia/confinia-core/issues/90) | PDF/SVG report: traceability annex | issue created, **not started**; folded into #121's structure | nowhere |

## Promoted to production 2026-08-03, verified live

| # | What | Verified in production |
|---|---|---|
| [#96](https://github.com/confinia/confinia-core/issues/96) | Neighbouring communes on the report and page | `api.confinia.io/v1/communes/01187/history?geometry=true&neighbours=true` → **16 neighbours** |
| [#91](https://github.com/confinia/confinia-core/issues/91) | Italian comune lineage | `api.confinia.io/v1/units/024044/history?country=IT` → **ends 2024-01-22, children `[024128]`**. Replayed on the new passive colour: both colours hold 2 128 rows |
| [#109](https://github.com/confinia/confinia-core/issues/109) | CI/CD: merge deploys staging, promotion is manual and reviewed | first CI promotion done 2026-08-03 |

## Live in production since 2026-08-03

| # | What | Where |
|---|---|---|
| [#105](https://github.com/confinia/confinia-core/issues/105) | Demo self-hosts MapLibre instead of a floating CDN major | https://confinia.github.io/ |
| [#118](https://github.com/confinia/confinia-core/issues/118) | `make demo-publish` no longer deletes the GIF, the README and another product's directory | (the fix that made the publish above safe) |

## Founder-only, not GitHub work

- **2FA on GitHub** — load-bearing since the self-hosted runner exists (#114).
- **Off-VM backups** — dumps exist, on the same VM, which is not a backup.
- **Promote to production** — `promote-production`, manual, requires your review.
