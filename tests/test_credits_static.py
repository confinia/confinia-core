"""Credits we owe to real people must survive refactors and branch merges.

Why this exists: the COGugaison acknowledgement was added by PR #23, then
silently clobbered by PR #20 (branched off an older README, merged after it).
Nothing failed, nothing conflicted, and the credit was simply gone for days —
while we told Kim Antunez in writing that it was in place. Crediting her work
was the ONE thing she asked for in return for her answers.

A credit given to a person is a commitment, not documentation. It gets a test.
"""
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_readme_credits_cogugaison():
    readme = _read("README.md")
    assert "## Acknowledgements" in readme, "the Acknowledgements section is gone"
    assert "COGugaison" in readme and "Kim Antunez" in readme
    # the link lets a reader reach the original work, which is the point
    assert "github.com/antuki/COGugaison" in readme


def test_passage_api_credits_cogugaison():
    # The other public claim we made by email: the passage-table response says
    # whose method it follows. It is user-visible, so it is a commitment too.
    src = _read("api", "main.py")
    assert "Method follows COGugaison (Kim Antunez)." in src, \
        "the /v1/passage response no longer credits COGugaison"
