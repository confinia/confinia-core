# ISSUES.md — where every open issue actually stands

One line per open issue, kept current **every time work touches an issue or a
PR** (RULES 14). GitHub says whether an issue is open; it does not say whether
the work is deployed, and "merged" has been mistaken for "live" here before.

**Where can I try it** uses the four answers of RULES 12: **nowhere** ·
**sandbox** · **staging** · **production**.

Last updated: **2026-08-15**, `main` at `13109d2`.
Active colour: **blue** on `:8091` (production) · passive: **green** on `:8402`
(staging stack on `:8403`, sandbox on `:8089`).

## Open, most worth doing first

The order is a judgement, not a queue: it goes *what is breaking now* → *what is
nearly finished* → *what earns money* → *what is merely unfinished*. Where an
issue is blocked, the blocker is named, because "blocked" without a blocker is
how something stays at the top of a list for a month.

| Rank | # | Issue | Why here | Where can I try it |
|---|---|---|---|---|
| **1** | [#123](https://github.com/confinia/confinia-core/issues/123) | Containers started by a CI job are killed with it | **The only one breaking something daily.** Every `deploy-staging` leaves the passive colour dead ~2 min later — `exit=-1`, a kill, not a crash. The same command over ssh survives 9 h. Cause identified 2026-08-15: containers are children of the job. Fix is Quadlet / `podman generate systemd` so a colour is a unit the CI restarts. **Also affects other products on this VM** | staging, when the self-repair has run |
| **2** | [#132](https://github.com/confinia/confinia-core/issues/132) | Transactional email | **Nearly done, and it unblocks talking to our 11 users** — today we collect an address we never use. Alerts deliver, the French registration mail arrives, `verifyEmail` is on for the sandbox. Remaining: see the mail a *real* signup produces (what was tested is the admin-initiated template), then the same two settings on production | production (alerts) · sandbox (registration) |
| **3** | [#121](https://github.com/confinia/confinia-core/issues/121) | Commune report: a document an expert office would sign | **The only open issue that moves toward revenue.** Everything else this week was infrastructure. Best done *after* one conversation with a surveyor or notaire — five minutes will name conventions no amount of design reasoning produces | nowhere |
| **4** | [#113](https://github.com/confinia/confinia-core/issues/113) | Staging: own stack and database | API half **DONE** — own stack `:8403`, own db `confinia_staging`, sandbox realm, Polar test; proven that exercising staging leaves production's `api_usage`/`premium_seen` untouched. Remaining: the sandbox's own API deployment per PR | staging |
| **5** | [#91](https://github.com/confinia/confinia-core/issues/91) | Italy: historical demography | Lineage **delivered and live** (1865-2024, 2 128 dead codes routed). Demography is the second half, and the argument for it is stronger now that the temporal model exists | production (lineage) |
| **6** | [#90](https://github.com/confinia/confinia-core/issues/90) | Report: traceability annex | Not started. Folded into #121's structure — do it *as* an annex of that document rather than separately | nowhere |
| **7** | [#127](https://github.com/confinia/confinia-core/issues/127) | Report: colour what a boundary gained and lost | **Blocked on data, not rendering.** On the flagship commune, 3 of 4 absorbed communes have no geometry and the 1943 outline is `geometry_approx`; drawing it would state something false. Needs historical geometry first | nowhere |
| **8** | [#115](https://github.com/confinia/confinia-core/issues/115) | Adopt STACK_template.md | Umbrella. Its two security items are done (#99, #114); what remains is real but not urgent: image digests, versioned migrations, Postgres RLS, secrets management, off-VM backups | nowhere |

**Closed since this file was last written:** [#99](https://github.com/confinia/confinia-core/issues/99) (migration to the `confinia` user), [#114](https://github.com/confinia/confinia-core/issues/114) (CI runner no longer root), [#111](https://github.com/confinia/confinia-core/issues/111) (each environment serves its own static files).

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
