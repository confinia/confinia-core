# ISSUES.md — where every open issue actually stands

One line per open issue, kept current **every time work touches an issue or a
PR** (RULES 14). GitHub says whether an issue is open; it does not say whether
the work is deployed, and "merged" has been mistaken for "live" here before.

**Where can I try it** uses the four answers of RULES 12: **nowhere** ·
**sandbox** · **staging** · **production**.

Last updated: **2026-08-20**, `main` at `2574ef9`.
Active colour: **green** on `:11220`/`:11230` (production, version 0.7.0) ·
passive: **blue** on `:11120`/`:11130`.
No alert firing. Backup timer active, next run Fri 2026-08-21 04:12 UTC.

## Not promoted yet — merged, green on staging, waiting on you

Production is at `40ef653`.

| Commit | What |
|---|---|
| `e844ab9` | **The quadlet units were never installed.** Committed, reviewed and merged for months, and nothing ever copied them to the machine — they had been installed by hand once, so the repo copies were documentation that drifted. Found because identity landed in the unit, the promotion succeeded, and production still said `identity: off` |
| `2574ef9` | **Every promotion has handed users a cold colour.** The passive colour's pages are on disk, not in PostgreSQL's cache: the same export took 37.2 s there and 0.015 s on the active colour, on identical data. The staged colour is warmed now, before it is smoked or promoted |

⚠️ **Sign-in is not live in production until this promotion runs.** The active
colour (green) still carries the pre-fix unit and reports `identity: off`; the
passive colour (blue) has the corrected one and reports `ok`. The switch is what
delivers it.

## Open, most worth doing first

| Rank | # | Issue | Stage | Where can I try it |
|---|---|---|---|---|
| 1 | [#205](https://github.com/confinia/confinia-core/issues/205) | Commune report: make it a document an expert office would sign | **substance is done, form is not.** The facts block, the two locator insets and the annex all landed this week. What remains is typography, page furniture, and the overall impression a professional signs their name under — and it is the one thing I cannot judge for you | **production** |
| 2 | [#193](https://github.com/confinia/confinia-core/issues/193) | The report is thin: what should it contain? | **first list delivered**, all six items, verified on both acceptance communes. Left open for the second list, which needs data we do not hold: named historical context (the Marcellin fusions, the *communes nouvelles*), links out (Wikipedia, OpenHistoricalMap), and Italy's population series (#91) | **production** |
| 3 | [#127](https://github.com/confinia/confinia-core/issues/127) | Report: colour what a boundary gained and lost | **gained delivered**, orange/light-blue, refusing to draw when the data cannot support it. Open because the *lost* side is still the naive `ST_Difference` that measured 97 unusable pieces | **production** (partly) |
| 4 | [#91](https://github.com/confinia/confinia-core/issues/91) | Italy: historical demography | **half delivered** — lineage live; demography not started. Visible to anyone comparing a French report (population curve, density) with an Italian one (neither) | **production** |
| 5 | [#115](https://github.com/confinia/confinia-core/issues/115) | Adopt STACK_template.md: close the documented gaps | umbrella. Its migration gap bit for real on 2026-08-18: `CREATE TABLE IF NOT EXISTS` never adds a constraint to a pre-existing table, so a Creem webhook hit a unique index that exists in no environment | nowhere |

## Closed this week

| # | What | Verified |
|---|---|---|
| [#132](https://github.com/confinia/confinia-core/issues/132) | Transactional email | **CLOSED 2026-08-20.** Proven in production by registering through the real public form: mail arrives, link points at `www.confinia.io`, following it gives *«Votre courriel a été vérifié»* and `emailVerified=true`. Four defects found only by reading the mail — loopback links, a 300 s expiry, Keycloak's default text naming the realm, and (in my replacement) `{1}` rendering as `720` with every apostrophe eaten by Java `MessageFormat` |
| [#233](https://github.com/confinia/confinia-core/issues/233) | The nightly ops backup wrote 20 bytes for 8 nights and reported success | **CLOSED.** Wrong Unix user (rootless podman is per-user) plus a pipeline whose status is its last command. The prune ran unconditionally, so failing nights ate good dumps |
| [#223](https://github.com/confinia/confinia-core/issues/223) | The bounce detector drowned in noise it made itself | **CLOSED.** 1:63 signal to noise; the one real bounce was invisible |
| [#229](https://github.com/confinia/confinia-core/issues/229) | 1169 lineage pairs where a predecessor outlives its successor | **CLOSED AS INVALID — I was wrong.** I sorted by overlap descending, read the top five rows, and generalised the extreme tail to 1169. The dates are real (762 distinct end dates, including mid-year ones no default produces). The defect was in the report: formation and later absorption wore one label |
| [#213](https://github.com/confinia/confinia-core/issues/213) · [#208](https://github.com/confinia/confinia-core/issues/208) | Creem webhook and tier ladder · e2e subscribe-and-pay | **CLOSED.** Payment → signed webhook 200 → key flipped free→t2 |
| [#90](https://github.com/confinia/confinia-core/issues/90) · [#167](https://github.com/confinia/confinia-core/issues/167) · [#169](https://github.com/confinia/confinia-core/issues/169) · [#185](https://github.com/confinia/confinia-core/issues/185) · [#113](https://github.com/confinia/confinia-core/issues/113) | Annex · name-only changes · what changed, in words · shared MapLibre · staging isolation | **CLOSED** |

## Delivered without an issue, this week

Work that came out of other work, each with a test pinning it:

- **Sign-in wired to the API** (#236, #238). Staging had carried `KC_ISSUER` for
  weeks while its container could not reach Keycloak at all — every token
  rejected, in silence, because a signed-in user whose token fails is
  indistinguishable from an anonymous one. Three causes: the wrong network,
  `KC_DISCOVERY` defaulting to an unreachable public URL, and a discovery
  document that advertises a `jwks_uri` built from `frontendUrl`. `/healthz`
  now reports identity as **off / unreachable / ok** with the reason.
- **The dumps were protected by one bit** (#235). `pg_dumpall` of the ops
  instance carries `public.api_key`, Keycloak password hashes and 27 real
  e-mail addresses, and every copy was 0664 in a 0775 directory. Ours survived
  only because `/home/confinia` is `drwx------`. The platform session confirmed
  it was readable from another tenant's account — not theoretical.
- **`pg_dumpall` pinned** (#237). Our backups restore *by luck*: a cluster dump
  carries the globals by construction. Overwatch found every dump they held was
  unrestorable for want of roles. The script now refuses a dump with no
  `CREATE ROLE`, so "optimising" to `pg_dump` fails loudly.

## Founder-only, not GitHub work

- **Promote to production** — two commits wait on it, and sign-in arrives with them.
- **Off-VM backups** — the one open technical decision. Three parts, and they
  belong to one decision: somewhere off this VM, **encryption at rest** (the
  payload is credentials and personal data), and a **rehearsed restore** — we
  hold dumps nobody has ever restored, which makes them a belief rather than a
  backup. Note the permanent gap: 2026-08-12 → 08-19 was never captured, and
  our first proven end-to-end payment (08-18) is inside it.
- **A second copy exists outside this tenant** — the platform session keeps the
  7 pre-gap dumps under its own user, deliberately, as redundancy against a job
  that deletes good backups while reporting success. Sound reasoning; it means
  any retention decision covers two places.
- **2FA on GitHub** — load-bearing since the self-hosted runner exists (#114).
