# TEST_SUBSCRIPTION — the new-user signup journey

This document describes HOW the "a stranger signs up and consumes the API"
journey is validated, and carries the LATEST results of the automated
validation. The test is a true end-to-end run: the real FastAPI
application against an ephemeral PostGIS loaded with a test dataset (two
communes merging in 2019), with no production secret involved.

## The journey covered

1. **Health**: `GET /healthz` answers `ok` and sees the data.
2. **Signup**: `POST /v1/keys {email}` creates an **active** UUID key on
   the **free** tier (an invalid email is rejected with 422).
3. **Metering**: a `/v1/units` request carrying the key is counted in
   `/v1/keys/{key}/usage` (this was the bug fixed in v0.3.0: keys were
   written to the wrong database and never counted).
4. **Temporal model**: the history of a test commune exposes the
   `merged_into` event dated 2019-01-01.
5. **Premium quota**: 9 `/v1/changes` reports pass with a decreasing
   counter (8, 7, … 0) then the **10th request returns 402** with the
   `/pricing` pointer (model: 9 included, paid afterwards).
6. **Reports**: `report.svg` starts with `<svg`, `report.pdf` with `%PDF`.

## Where and when it runs

- **GitHub Actions CI**: workflow
  [`subscription-tests`](.github/workflows/subscription-tests.yml),
  "Signup journey" step. Triggers: every push to `main`, every pull
  request, a weekly run (Monday 05:17 UTC) to catch drift without
  commits, and on demand (`workflow_dispatch`).
- **Files**: [`tests/test_subscription.py`](tests/test_subscription.py),
  dataset [`tests/fixture.sql`](tests/fixture.sql).
- **Locally (VM, podman)**: start a throwaway PostGIS, load the fixture,
  start the API with `PG_DSN`/`OPS_DSN` pointing at it, then
  `pytest tests/test_subscription.py` (same variables as the workflow).

## Latest results

| Date (UTC) | Trigger | Result | Detail |
|---|---|---|---|
| 2026-07-22 10:16 | pull request #24 (first run) | ✅ **7/7 passed** (0.16 s) | [run 29911277810](https://github.com/confinia/confinia-core/actions/runs/29911277810): health, signup, invalid email rejected, metering, merge event, 9-then-402 quota, SVG/PDF reports |

Full history: repository Actions tab, `subscription-tests` workflow.
