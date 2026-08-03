"""Italian lineage parsing (issue #91).

Pure-parser tests: no database. What they guard is the reasoning in the loader,
not the SQL — a wrong `unit_type` or a mangled encoding produces a plausible
result and a silently disconnected dataset.
"""
import datetime
import importlib.util
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
spec = importlib.util.spec_from_file_location(
    "ingest_istat", os.path.join(ROOT, "ingestion", "ingest_istat.py"))
istat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(istat)


def ev(code, when, succ, scorporo=False, name="X"):
    return istat.Event(code, name, when, succ, None, scorporo)


def d(s):
    return datetime.date.fromisoformat(s)


def test_it_targets_the_unit_type_the_data_actually_uses():
    # Italian comuni arrive via Eurostat LAU as unit_type 'lau'. Using 'comune'
    # matched zero rows and would have built a parallel, disconnected set that
    # no lookup would ever reach.
    assert istat.UNIT_TYPE == "lau"
    assert istat.COUNTRY == "IT"


def test_the_source_file_is_cp1252_not_utf8():
    # Decoding it as latin-1 "works" and mangles every accented name.
    assert istat.ENCODING == "cp1252"


def test_a_missing_day_falls_back_to_january_first():
    assert istat.parse_date("", "1927") == d("1927-01-01")
    assert istat.parse_date("22/01/2024", "2024") == d("2024-01-22")
    assert istat.parse_date("", "") is None


def test_a_comune_split_between_two_successors_ends_once_at_the_earliest_date():
    closures, _ = istat.plan([
        ev("048804", d("1865-08-13"), "048017"),
        ev("048804", d("1870-01-01"), "048043"),
    ])
    end, succs = closures["048804"]
    assert end == d("1865-08-13"), "a comune cannot end twice; take the earliest"
    assert succs == {"048017", "048043"}, "both successors must be recorded"


def test_a_scorporo_does_not_kill_its_predecessor():
    # Territory split off: the predecessor usually survives, so closing it would
    # delete a living comune from the timeline.
    closures, births = istat.plan([ev("012345", d("2000-01-01"), "012999", scorporo=True)])
    assert "012345" not in closures
    assert births["012999"][d("2000-01-01")] == {"012345"}


def test_absorbing_a_comune_is_recorded_as_a_candidate_birth_only():
    # plan() proposes; the loader applies it only when the successor's own
    # record starts at that date. Otherwise we would claim a comune was born on
    # the day it merely grew.
    _, births = istat.plan([ev("024044", d("2024-01-22"), "024128")])
    assert births["024128"][d("2024-01-22")] == {"024044"}
