"""The demo publish target must not be able to delete what it does not own.

The Pages repo `confinia/confinia.github.io` is shared: besides our demo it
holds `valserhone.gif` (linked in the outreach we asked people to open), its own
README, and `overwatch/` — a different product's demo, maintained by someone
else.

`rsync -a --delete demo/ <pages-root>/` removes all three, silently, on the next
publish. That line shipped in #106 and is what this test exists to prevent
(issue #118).
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _target():
    src = open(os.path.join(ROOT, "Makefile"), encoding="utf-8").read()
    m = re.search(r"^demo-publish:.*?(?=^\S|\Z)", src, re.S | re.M)
    assert m, "the demo-publish target is gone"
    return m.group(0)


def test_delete_is_confined_to_the_directory_we_own():
    for line in _target().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):        # the comment describes the bad pattern
            continue
        if "rsync" not in line or "--delete" not in line:
            continue
        # A --delete rsync is only acceptable when its destination is lib/,
        # the one directory whose whole contents belong to this repo.
        assert re.search(r"--delete\s+\S*demo/lib/\s+\S*/lib/", line), \
            f"--delete must target lib/ only, got: {line.strip()}"


def test_the_pages_root_is_never_a_delete_destination():
    tgt = _target()
    assert not re.search(r"--delete[^\n]*\$\(PAGES_DIR\)/\s*$", tgt, re.M), \
        "deleting at the Pages root erases valserhone.gif, README.md and overwatch/"


def test_the_vendored_library_still_reaches_pages():
    # The original bug this target fixed: publishing index.html alone leaves the
    # vendored MapLibre behind and the map dies on Pages (issue #105).
    tgt = _target()
    assert "demo/lib/" in tgt, "the vendored library must still be published"
    assert "demo/index.html" in tgt


def test_the_checkout_does_not_land_in_tmp():
    # /tmp is a RAM-backed tmpfs on the VM; staging a clone there eats memory.
    assert "/tmp/" not in _target(), "clone somewhere other than /tmp"
