"""CI must run a test because it exists, not because someone remembered it.

`subscription-tests.yml` named every test file by hand. The list was maintained
by memory, and memory lost: on 2026-08-26, 39 files had drifted out of it —
including all eleven written that week. Running the suite for the first time
found three already failing, broken the day before by a merged change that
nobody could see break them.

A test nobody runs is not a test. It is a comment that costs CPU to write.
"""
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
WF = open(os.path.join(ROOT, ".github", "workflows", "subscription-tests.yml"),
          encoding="utf-8").read()


def test_the_suite_is_invoked_as_a_directory():
    """The property that makes drift impossible."""
    assert "pytest -v tests/ \\" in WF, "one invocation over the directory"


def test_the_exclusions_are_few_and_each_has_a_reason():
    """Every exclusion is a hole; three are justified, a growing list would not
    be. e2e drives a browser, smoke_prod talks to PRODUCTION, and keycloak has
    its own service container."""
    excluded = [l.split("=")[1].strip().rstrip("\\").strip()
                for l in WF.split("\n") if "--ignore=" in l]
    assert set(excluded) == {"tests/e2e", "tests/smoke_prod.py",
                             "tests/test_keycloak.py"}, excluded


def test_production_is_never_smoked_by_the_pull_request_job():
    """smoke_prod.py hits api.confinia.io. A pull request must not be able to
    fail — or wake anyone — because of the state of production."""
    e2e = WF.split("jobs:")[1].split("  r-client:")[0]
    assert "--ignore=tests/smoke_prod.py" in e2e
    assert "smoke_prod.py" not in e2e.replace("--ignore=tests/smoke_prod.py", "")


def test_every_test_file_is_reachable_by_that_invocation():
    """The assertion that would have caught the drift on any of the 39 days it
    was accumulating."""
    tests_dir = os.path.join(ROOT, "tests")
    files = [f for f in os.listdir(tests_dir)
             if f.startswith("test_") and f.endswith(".py")]
    assert len(files) > 30, "sanity: the suite is not empty"
    excluded = {"test_keycloak.py"}
    for f in files:
        if f in excluded:
            continue
        # reachable means: inside tests/, not named anywhere as an exception
        assert f"--ignore=tests/{f}" not in WF, f"{f} is silently excluded"
