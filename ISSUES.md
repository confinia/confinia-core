# ISSUES.md — where every open issue actually stands

One line per open issue, kept current **every time work touches an issue or a
PR** (RULES 14). GitHub says whether an issue is open; it does not say whether
the work is deployed, and "merged" has been mistaken for "live" here before.

**Where can I try it** uses the four answers of RULES 12: **nowhere** ·
**sandbox** · **staging** · **production**.

Last updated: **2026-08-16**, `main` at `95e9927`.
Active colour: **blue** on `:8091` (production) · passive: **green** on `:8402`.

**Band 11xxx (1PESI): 9 of 10.** The app caddy answers on **`:11000`** for all
six vhosts (graceful `caddy reload`, production at 200 throughout), alongside
Grafana `11040`, otel `11060`/`11061`/`11062`, Keycloak `11070`, green
`11220`/`11230`, staging `11320` and sandbox `11420`. Verified with `ss`, never
with `podman ps` — PORTS.md records why that distinction cost a day. **Blue
`11120`/`11130` is the only one left**, and it waits on the promotion because it
is the active colour. Full account for the platform session: MOVE.md.

## Open, most worth doing first

Ranked by what it costs us to leave undone, not by issue number.

| Rank | # | Issue | Stage | Where can I try it |
|---|---|---|---|---|
| 1 | [#113](https://github.com/confinia/confinia-core/issues/113) | Staging needs its own stack and its own database | **DONE, awaiting close.** Verified 2026-08-16: `confinia-staging_api_1` writes to `confinia_staging`, which is not production's `confinia`. The only work left is closing the issue | **staging** — `11320` + `8403` |
| 2 | [#167](https://github.com/confinia/confinia-core/issues/167) + [#169](https://github.com/confinia/confinia-core/issues/169) | The page implies a boundary change at every event (#167), and never says what actually changed (#169) | created 2026-08-16, **not started**. Labastida (01028, ES) draws five panels for four name changes, 0.115 % area spread; Bad Berneck (09472116, DE) mints a version whose entire difference is **one deleted space**. **Founder decision on #167**: a name-only change shows no boundary panel at all. #169 adds the sentence that replaces it, and asks whether a whitespace-only difference is a version at all | nowhere — the page is wrong in **production** today |
| 3 | [#132](https://github.com/confinia/confinia-core/issues/132) | Transactional email | **one step from done.** Ops alerts delivered; registration mail sent from the sandbox realm. Remaining: see the *real* self-registration mail (what was tested was the admin-initiated template), then `VERIFY_EMAIL=1` on the production realm | **production** (alerts) · **sandbox** (registration) |
| 4 | [#90](https://github.com/confinia/confinia-core/issues/90) + [#168](https://github.com/confinia/confinia-core/issues/168) | Traceability annex — per kind of fact (#90), per change with a URL (#168), per source with a download link or a site to consult ([#188](https://github.com/confinia/confinia-core/issues/188)) | **three issues, one annex.** None started. Built separately they would put three annexes in one PDF; **#90 should be the umbrella and all three land in one pass** — awaiting your go-ahead to consolidate | nowhere |
| 5 | [#205](https://github.com/confinia/confinia-core/issues/205) | Commune report: make it a document an expert office would sign | **not started**. The gap is form, not information — and form decides whether a professional attaches it to a file | nowhere |
| 6 | [#127](https://github.com/confinia/confinia-core/issues/127) | Report: colour what a boundary gained and lost — refuse to draw when the data cannot support it | **not started**; measured and found unusable as a naive `ST_Difference` (97 pieces each way, one significant). #167 must settle first — no delta can be drawn for a change that is a re-digitisation | nowhere |
| 7 | [#91](https://github.com/confinia/confinia-core/issues/91) | Italy: historical demography | **half delivered** — lineage merged (PR #120) and live; demography not started | **production** — 2 128 dead comuni route to a successor, 1865-2024 |
| 8 | [#185](https://github.com/confinia/confinia-core/issues/185) | Serve MapLibre once from a shared directory, same-origin | **founder approved**; not a `lib.*` host — v6's worker never starts cross-origin, silently | **staging** |
| 9 | [#115](https://github.com/confinia/confinia-core/issues/115) | Adopt STACK_template.md: close the documented gaps | umbrella, tracking the others | nowhere |

Why this order: #113 is finished work that only needs closing; #167/#169 are the
only entries where **production actively tells customers something untrue**, and
the founder has already settled #167's one open question; #132 is a single flag
from done. Then the provenance chain (#90/#168 → #205), which is what the product
sells, before #127 and #91 which are both gated on data we do not hold.

## Closed, but this file still listed them as open

The drift this ranking pass was meant to catch.

| # | What | Verified |
|---|---|---|
| [#123](https://github.com/confinia/confinia-core/issues/123) | Colour port publisher dies | **CLOSED.** Cause 1: another tenant squatted 8092/8093 → green moved to 84xx. Cause 2: a container created by a CI job is a **child of that job** and died ~2 min after it ended → Quadlet units, so it belongs to systemd |
| [#114](https://github.com/confinia/confinia-core/issues/114) | **SECURITY** — CI runner ran as `debian`, passwordless root | **CLOSED.** Runs as `confinia`; `deploy-staging` asserts `sudo -n` fails every run |
| [#111](https://github.com/confinia/confinia-core/issues/111) | Sandbox needs its own working directory | **CLOSED.** Per-environment static roots proven; the sandbox API itself was repaired 2026-08-16 — it could reach neither of its databases |
| [#99](https://github.com/confinia/confinia-core/issues/99) | Move the stack to its own Unix user | **CLOSED.** ~23 min downtime, every count matched first time (205 370 / 2 128 / 1 285 119) |

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

- **Promote to production** — `promote-production`, manual, requires your
  review. Blue's move to `11120`/`11130` is the last thing waiting on it.
- **2FA on GitHub** — load-bearing since the self-hosted runner exists (#114).
- **Off-VM backups** — dumps exist, on the same VM, which is not a backup.
