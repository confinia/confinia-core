# A workflow waiting for a token that may touch workflows

GitHub refuses to accept a change under `.github/workflows/` from a token
without the `workflow` scope. agent-01 carries `repo` alone, so the new
`subscription-tests.yml` is staged HERE, one directory away from where it
belongs, and moved into place by an account that has the scope:

    git fetch origin
    git checkout -b agent/ci-workflow-only origin/agent/ci-proposed
    git mv .github/workflow-proposed/subscription-tests.yml .github/workflows/subscription-tests.yml
    git rm -r .github/workflow-proposed
    git commit -m "CI runs a test because it exists, not because someone remembered it (#115)"
    git push -u origin agent/ci-workflow-only

This directory should be empty after that. If it is not, a workflow change is
sitting where nothing will ever run it.
