# ISSUES.md — where every open issue actually stands

One line per open issue, kept current **every time work touches an issue or a
PR** (RULES 14). GitHub says whether an issue is open; it does not say whether
the work is deployed, and "merged" has been mistaken for "live" here before.

**Where can I try it** uses the four answers of RULES 12: **nowhere** ·
**sandbox** · **staging** · **production**.

Last updated: **2026-08-05**, `main` at `d2c6f96`.
Active colour: **blue** (production, promoted 2026-08-03) · passive: **green** (staging).

## Open

| # | Issue | Stage | Where can I try it |
|---|---|---|---|
| [#123](https://github.com/confinia/confinia-core/issues/123) | Blue colour's port publisher dies; podman reports a mapping that does not exist | **root cause not found** — seen 4× on 2026-08-03, including **immediately after the production promotion** | nowhere. It degraded production for ~10 min: caddy fell back to the old colour and every public check passed. Guard added (PR below); workaround `podman-compose … --profile serve up -d --no-deps api` |
| [#132](https://github.com/confinia/confinia-core/issues/132) | Transactional email: confirm registrations and deliver ops alerts from `alert@confinia.io` | issue created, **not started**. Today there is **no SMTP anywhere** — registration collects an address it never verifies, and we cannot email our own 10 users | nowhere |
| [#127](https://github.com/confinia/confinia-core/issues/127) | Report: colour what a boundary gained and lost — refuse to draw when the data cannot support it | issue created, **not started**; blocked by missing historical geometry, not by rendering | nowhere |
| [#121](https://github.com/confinia/confinia-core/issues/121) | Commune report: make it a document an expert office would sign | issue created, **not started** | nowhere |
| [#115](https://github.com/confinia/confinia-core/issues/115) | Adopt STACK_template.md: close the documented gaps | issue created, tracking others | nowhere (umbrella) |
| [#114](https://github.com/confinia/confinia-core/issues/114) | **SECURITY** — CI runner runs as `debian`, passwordless root | issue created, **blocked on #99** | nowhere |
| [#113](https://github.com/confinia/confinia-core/issues/113) | Staging needs its own stack and DB; today it writes into production data | issue created, **not started** | nowhere |
| [#111](https://github.com/confinia/confinia-core/issues/111) | Sandbox needs its own working directory before PR branches can deploy | issue created, **not started** | nowhere |
| [#99](https://github.com/confinia/confinia-core/issues/99) | Move the stack to its own Unix user | **phase 1 complete, no downtime**: artefacts staged with matching checksums, DB restore and cross-user volume import both rehearsed, target user verified. **Only the cutover remains** — needs an announced window and a 30-min timer | nowhere — the stack still runs as `debian` |
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
