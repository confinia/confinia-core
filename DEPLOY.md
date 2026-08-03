# DEPLOY.md — how code reaches staging and production

Since 2026-08-03, **nothing is deployed by hand**. No rsync from a laptop, no
script run over SSH. Everything goes through GitHub Actions on a **self-hosted
runner** that lives on the VM (issue #109).

## Why a self-hosted runner

The runner **pulls** jobs, so no credential is stored in GitHub: no SSH key, no
token, nothing to leak. The secrets (`deploy/secrets.env`, `deploy/sandbox.env`)
stay on the VM and are read locally.

⚠️ **The repository is public.** A self-hosted runner on a public repository
would let anyone run code on the VM through a fork pull request, so the fork
policy is set to **`all_external_contributors`**: no external workflow runs
without an explicit approval. Do not relax it.

This reverses a property the security analysis used to list as a strength
("a compromised repository deploys nothing"). It was a deliberate trade, and it
makes **2FA on GitHub** materially more important than before.

## The flow

| Step | Trigger | What happens |
|---|---|---|
| **Staging** | push to `main` (or manual) | mirror synced to the commit, passive colour rebuilt, smoke suite run against the passive container |
| **Validation** | human | the founder exercises staging and decides (RULES 13) |
| **Production** | **manual only** | `promote-production`, gated by the `production` environment and its **required reviewer** |

A merge to `main` **never** reaches production on its own. Promotion is a
separate, approved action.

If the production smoke suite fails, the workflow **rolls back automatically**
to the previous colour and fails the run.

## What the smoke actually hits

**Staging smokes the passive container directly on the VM**
(`http://127.0.0.1:8091` or `:8092`, whichever colour is passive), not
`staging.api.confinia.io`. Two reasons: that vhost sits behind basic auth and
only the bcrypt **hash** lives on the VM — the password is the founder's and is
deliberately not stored — and going straight to the container proves the build
just staged is answering, rather than something an edge cached.

The suite runs from a venv at `/home/debian/.venvs/smoke`, created on first use.
It must be run **through pytest**: `smoke_prod.py` has no `__main__`, so
`python3 smoke_prod.py` executes zero tests and exits 0 — a deployment that
reports green having checked nothing.

## The deployment mirror

`/home/debian/projects/confinia` must be a **git checkout** driven by CI:
`git fetch` then `git reset --hard <sha>`. Untracked files survive by design,
which is what protects `deploy/*.env`, `data/` and `business/`.

⚠️ **One-time conversion, still to do.** Today that directory is a plain rsync
target with no `.git`, so `deploy-staging` cannot run yet. Converting it means
`git init`, adding the remote, fetching, and `git reset --hard main` — which
overwrites whatever the directory currently holds with what `main` holds.

It must therefore happen **after PRs #104 and #106 are merged**. Those are the
ones that vendored MapLibre locally; reset the mirror to a `main` that predates
them and the live demo silently reverts to the CDN build we just moved away
from. The only tracked file that currently differs from `main` is
`demo/index.html`, precisely because of #106.

**Never edit that directory by hand.** It is overwritten at every deployment,
and a local edit is silently lost, or worse, silently kept.

## What is NOT covered yet

**Sandbox deployment of PR branches** is not wired, on purpose. The sandbox
currently runs from the same directory as production, so deploying a branch
there would overwrite the static files served to www. It needs its own checkout
first: see issue #111.

## Rollback

`./deploy/deploy-api.sh rollback` on the VM switches back to the other colour.
The previous colour is never destroyed by a promotion, so this is always
available and takes seconds.
