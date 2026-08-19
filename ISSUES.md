# ISSUES.md — where every open issue actually stands

One line per open issue, kept current **every time work touches an issue or a
PR** (RULES 14). GitHub says whether an issue is open; it does not say whether
the work is deployed, and "merged" has been mistaken for "live" here before.

**Where can I try it** uses the four answers of RULES 12: **nowhere** ·
**sandbox** · **staging** · **production**.

Last updated: **2026-08-19**, `main` at `7bfc593`.
Active colour: **blue** on `:11120`/`:11130` (production) · passive: **green** on
`:11220`/`:11230`.

**Band 11xxx (1PESI): 10 of 10 — done.** Blue's move to `11120`/`11130` was the
last one, and it went with the 2026-08-18 promotion. Every legacy port is
released: `ss` shows nothing of ours on 8091, 5441, 8088, 2085, 8402 or 8403.
The app caddy answers on `:11000` for all six vhosts, alongside Grafana `11040`,
otel `11060`/`11061`/`11062`, Keycloak `11070`, staging `11300`/`11320` and the
sandbox on its own edge `11400`/`11420`/`11490`. Verified with `ss`, never with
`podman ps` — PORTS.md records why that distinction cost a day.

## Not promoted yet — merged, green on staging, waiting on you

Five commits sit in `main` and not in production (`984f4fe`). Promotion is
founder-only.

| Commit | What |
|---|---|
| `04ebe12` → `b78ca9f` | The report's **situation inset**: where the commune sits in its country. Landed broken twice and was fixed twice — it timed the PDF out by unioning 35 000 communes, then drew all of France's overseas territory (Guadeloupe 63°W to Réunion 56°E) in near-white, which is why the founder could not see it |
| `a116ee1` `7feabf2` | The **district inset** below it — the `nuts3` region containing the commune (Ain for Haut Valromey), found by a 0.35 ms indexed point lookup, country-agnostic |
| `c3db0a9` | The bounce detector fix (#223) |
| `359783f` → `7bfc593` | **#193's first list**: area and how it changed, the lineage in words, named neighbours, density, rank within the district, and what did *not* change. Four defects surfaced only on rendering, and one data problem surfaced with them (#229) |

Try before promoting:
`staging.confinia.io/api/v1/communes/01187/report.svg?country=FR&lang=fr`

## Open, most worth doing first

Ranked by what it costs us to leave undone, not by issue number.

| Rank | # | Issue | Stage | Where can I try it |
|---|---|---|---|---|
| 1 | [#132](https://github.com/confinia/confinia-core/issues/132) | Transactional email | **one step from done.** Ops alerts delivered; registration mail sent from the sandbox realm. Remaining: see the *real* self-registration mail (what was tested was the admin-initiated template), then `VERIFY_EMAIL=1` on the production realm. Note #223's finding: the e2e journey's synthetic recipients bounce, so turning verification on more widely will produce more of them | **production** (alerts) · **sandbox** (registration) |
| 2 | [#205](https://github.com/confinia/confinia-core/issues/205) | Commune report: make it a document an expert office would sign | **partly under way, not claimed done.** The two locator insets are the first instalment of *form*; the annex (#90) landed before them. What remains is typography, page furniture, and the overall impression a professional signs their name under | **staging** (the insets) |
| 3 | [#193](https://github.com/confinia/confinia-core/issues/193) | The report is thin: what should it contain? | **first list delivered**, all six items, verified on both acceptance communes — Bad Berneck is no longer "a page with a map on it": area, a stable boundary stated as evidence, rank among the 44 units of its Landkreis, 8 named neighbours. Left open for the second list, which needs data we do not hold (historical context, links out, Italy's population series) | **staging** |
| 3= | [#229](https://github.com/confinia/confinia-core/issues/229) | 1169 lineage pairs where a predecessor outlives the commune it wholly became | **found by #193, mitigated not fixed.** 714 communes, all French; the parents uniformly "end" on the COG snapshot date and the children "start" at the 1943 nomenclature — pipeline defaults, not measurements. Invisible while lineage only drew shapes; now it would be a sentence a customer reads, so the report declines those dates and says why | **staging** (the mitigation) |
| 4 | [#127](https://github.com/confinia/confinia-core/issues/127) | Report: colour what a boundary gained and lost — refuse to draw when the data cannot support it | **delivered for gained area**, orange/light-blue per the founder's instruction; the lineage-driven `_gained_rings` refuses to draw when the data cannot support it. Left open because the *lost* side is still the naive `ST_Difference` that measured 97 unusable pieces | **production** (partly) |
| 5 | [#91](https://github.com/confinia/confinia-core/issues/91) | Italy: historical demography | **half delivered** — lineage merged (PR #120) and live; demography not started | **production** — 2 128 dead comuni route to a successor, 1865-2024 |
| 6 | [#115](https://github.com/confinia/confinia-core/issues/115) | Adopt STACK_template.md: close the documented gaps | umbrella, tracking the others. Its migration gap bit for real on 2026-08-18: `CREATE TABLE IF NOT EXISTS` never adds a constraint to a pre-existing table, so a Creem webhook `ON CONFLICT (email, tier)` hit a unique index that exists in no environment | nowhere |

Why this order: #132 is a single flag from done. Then the two report issues,
which are what the product actually sells — #205 (form) before #193 (substance)
only because form is already in motion. #127 and #91 are both gated on data we
do not hold, and #115 is a tracker.

## Closed since this file last told the truth

| # | What | Verified |
|---|---|---|
| [#223](https://github.com/confinia/confinia-core/issues/223) | The bounce detector drowned in noise it made itself | **CLOSED 2026-08-19.** 64 messages in `alert@`, the check failing on every run since 2026-08-17 — 53 bounces the e2e journey caused itself, 10 pieces of another system's CI mail, **1** real bounce. Signal to noise 1:63 |
| [#213](https://github.com/confinia/confinia-core/issues/213) | Creem webhook and tier ladder | **CLOSED.** Proven end to end: payment → signed webhook 200 → key flipped free→t2 |
| [#208](https://github.com/confinia/confinia-core/issues/208) | E2E subscribe-and-pay as a Selenium IDE project | **CLOSED.** Two stages on purpose — Selenium reaches the payment step, Playwright completes the card the bot layer will not render for Selenium (measured: 30 s, never loads) |
| [#90](https://github.com/confinia/confinia-core/issues/90) + [#168](https://github.com/confinia/confinia-core/issues/168) + [#188](https://github.com/confinia/confinia-core/issues/188) | Traceability annex — per kind of fact, per change with a URL, per source with a link | **CLOSED.** Consolidated into one annex rather than three |
| [#167](https://github.com/confinia/confinia-core/issues/167) + [#169](https://github.com/confinia/confinia-core/issues/169) | The page implied a boundary change at every event, and never said what changed | **CLOSED.** A name-only change draws no boundary panel; a sentence says what changed instead. Verified on both measured cases |
| [#185](https://github.com/confinia/confinia-core/issues/185) | Serve MapLibre once from a shared directory, same-origin | **CLOSED** |
| [#113](https://github.com/confinia/confinia-core/issues/113) | Staging needs its own stack and its own database | **CLOSED.** `confinia-staging_api_1` writes to `confinia_staging`, which is not production's `confinia` |
| [#123](https://github.com/confinia/confinia-core/issues/123) | Colour port publisher dies | **CLOSED.** A container created by a CI job is a child of that job and died with it → Quadlet units, so it belongs to systemd |
| [#114](https://github.com/confinia/confinia-core/issues/114) | **SECURITY** — CI runner ran as `debian`, passwordless root | **CLOSED.** Runs as `confinia`; `deploy-staging` asserts `sudo -n` fails every run |
| [#111](https://github.com/confinia/confinia-core/issues/111) | Sandbox needs its own working directory | **CLOSED** |
| [#99](https://github.com/confinia/confinia-core/issues/99) | Move the stack to its own Unix user | **CLOSED.** ~23 min downtime, every count matched first time |

## Live in production, verified

| # | What | Verified in production |
|---|---|---|
| [#96](https://github.com/confinia/confinia-core/issues/96) | Neighbouring communes on the report and page | `api.confinia.io/v1/communes/01187/history?geometry=true&neighbours=true` → **16 neighbours** |
| [#91](https://github.com/confinia/confinia-core/issues/91) | Italian comune lineage | `api.confinia.io/v1/units/024044/history?country=IT` → **ends 2024-01-22, children `[024128]`** |
| [#109](https://github.com/confinia/confinia-core/issues/109) | CI/CD: merge deploys staging, promotion is manual and reviewed | first CI promotion 2026-08-03 |
| [#105](https://github.com/confinia/confinia-core/issues/105) | Demo self-hosts MapLibre instead of a floating CDN major | https://confinia.github.io/ |
| [#118](https://github.com/confinia/confinia-core/issues/118) | `make demo-publish` no longer deletes the GIF, the README and another product's directory | (the fix that made the publish above safe) |

## Founder-only, not GitHub work

- **Promote to production** — `promote-production`, manual, requires your review.
  Five commits wait on it (table above).
- **Purge the 53 self-inflicted bounces** — `python3 deploy/mailcheck.py --purge`
  on the VM. Deliberate and manual by design; that mailbox's quota is
  deliberately tiny and it holds 64 messages today.
- **Another system's CI mails `alert@confinia.io`** — 10 messages, most recent
  2026-08-18. Not ours to fix, and it puts foreign noise in our one alerting
  detector.
- **2FA on GitHub** — load-bearing since the self-hosted runner exists (#114).
- **Off-VM backups** — dumps exist, on the same VM, which is not a backup.
