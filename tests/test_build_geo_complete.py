"""The double-ingestion builder must run every loader (issue #99).

Blue/green rests on one property: the colours are interchangeable. Rebuilding
green on 2026-08-12 produced 203 242 rows against blue's 205 370 -- the gap was
the Italian lineage, and the population table was missing entirely
(1 285 119 rows). Both loaders had simply never been added to build-geo.sh after
they shipped (#88 on 30 July, #91 on 3 August).

Promoting that colour would have removed the population curve and the dead-code
routing from production, with nothing failing.
"""
import glob
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_every_dsn_loader_is_in_the_builder():
    builder = open(os.path.join(ROOT, "deploy", "build-geo.sh"), encoding="utf-8").read()
    missing = []
    for path in sorted(glob.glob(os.path.join(ROOT, "ingestion", "ingest_*.py"))):
        name = os.path.basename(path)
        src = open(path, encoding="utf-8").read()
        if "--dsn" not in src:          # not a loader that populates the geo db
            continue
        if name not in builder:
            missing.append(name)
    assert not missing, (
        "these loaders write to the geo database but the builder never runs them, "
        f"so a rebuilt colour silently lacks their data: {', '.join(missing)}")


def test_the_builder_reports_what_it_loaded():
    # The final check is what makes a gap visible at all: it is how the missing
    # 2 128 Italian rows were noticed.
    builder = open(os.path.join(ROOT, "deploy", "build-geo.sh"), encoding="utf-8").read()
    assert "SELECT source, count(*) FROM commune_version" in builder, \
        "the builder must print per-source counts, or a partial build looks complete"
