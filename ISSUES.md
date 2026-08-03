# ISSUES.md — where every open issue actually stands

One line per open issue, kept current **every time work touches an issue or a
PR** (RULES 14). GitHub says whether an issue is open; it does not say whether
the work is deployed, and "merged" has been mistaken for "live" here before.

**Where can I try it** uses the four answers of RULES 12: **nowhere** ·
**sandbox** · **staging** · **production**.

Last updated: **2026-08-03**, `main` at `54389ab`.
Active colour: **green** (production) · passive: **blue** (staging).

## Open

| # | Issue | Stage | Where can I try it |
|---|---|---|---|
| [#123](https://github.com/confinia/confinia-core/issues/123) | Blue colour's port publisher dies; podman reports a mapping that does not exist | issue created, **not started** — seen 3× on 2026-08-03 | nowhere. **This is why staging returned 503 and "unknown unit"**: caddy marked the upstream down. Workaround: `SKIP_BUILD=1 ./deploy/deploy-api.sh stage` |
| [#127](https://github.com/confinia/confinia-core/issues/127) | Report: colour what a boundary gained and lost — refuse to draw when the data cannot support it | issue created, **not started**; blocked by missing historical geometry, not by rendering | nowhere |
| [#121](https://github.com/confinia/confinia-core/issues/121) | Commune report: make it a document an expert office would sign | issue created, **not started** | nowhere |
| [#115](https://github.com/confinia/confinia-core/issues/115) | Adopt STACK_template.md: close the documented gaps | issue created, tracking others | nowhere (umbrella) |
| [#114](https://github.com/confinia/confinia-core/issues/114) | **SECURITY** — CI runner runs as `debian`, passwordless root | issue created, **blocked on #99** | nowhere |
| [#113](https://github.com/confinia/confinia-core/issues/113) | Staging needs its own stack and DB; today it writes into production data | issue created, **not started** | nowhere |
| [#111](https://github.com/confinia/confinia-core/issues/111) | Sandbox needs its own working directory before PR branches can deploy | issue created, **not started** | nowhere |
| [#99](https://github.com/confinia/confinia-core/issues/99) | Move the stack to its own Unix user | **procedure written** (MOVE.md, PR #100 merged); migration **attempted and rolled back** 2026-08-02 | nowhere — the stack still runs as `debian` |
| [#91](https://github.com/confinia/confinia-core/issues/91) | Italy: temporal model, then historical demography | **half delivered** — lineage merged (PR #120); demography not started | **staging** — 2 128 dead comuni now route to a successor, 1865-2024 |
| [#90](https://github.com/confinia/confinia-core/issues/90) | PDF/SVG report: traceability annex | issue created, **not started**; folded into #121's structure | nowhere |

## Shipped today, awaiting your validation on staging

Merged into `main` and deployed to the passive colour by CI, **not yet promoted**:

| # | What | Try it on staging |
|---|---|---|
| [#96](https://github.com/confinia/confinia-core/issues/96) | Neighbouring communes drawn on the report and page | `staging.confinia.io/commune/01187` — **verified on the staged colour: 16 neighbours returned** |
| [#91](https://github.com/confinia/confinia-core/issues/91) | Italian comune lineage | `staging.api.confinia.io/v1/units/024044/history?country=IT` — **verified: ends 2024-01-22, children `[024128]`** |
| [#109](https://github.com/confinia/confinia-core/issues/109) | CI/CD: staging deploys itself on every merge | the `deploy-staging` run, green since `9594cbe` |

## Live in production since 2026-08-03

| # | What | Where |
|---|---|---|
| [#105](https://github.com/confinia/confinia-core/issues/105) | Demo self-hosts MapLibre instead of a floating CDN major | https://confinia.github.io/ |
| [#118](https://github.com/confinia/confinia-core/issues/118) | `make demo-publish` no longer deletes the GIF, the README and another product's directory | (the fix that made the publish above safe) |

## Founder-only, not GitHub work

- **2FA on GitHub** — load-bearing since the self-hosted runner exists (#114).
- **Off-VM backups** — dumps exist, on the same VM, which is not a backup.
- **Promote to production** — `promote-production`, manual, requires your review.
