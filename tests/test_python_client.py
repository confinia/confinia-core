"""The Python client (issue #22) against the same fixture API as the other
journeys: construction, core lookups, error mapping."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients", "python"))
from confinia import Confinia, ConfiniaError  # noqa: E402

BASE = os.environ.get("TEST_API_BASE", "http://127.0.0.1:8000")


@pytest.fixture(scope="module")
def client():
    return Confinia(base_url=BASE)


def test_unit_at_point(client):
    u = client.unit_at(lat=46.005, lon=5.005, at="2020-06-01")
    assert u["properties"]["nom"] == "Testville"


def test_history_has_merge_event(client):
    h = client.history("99902")
    assert any(e["type"] == "merged_into" for e in h["events"])


def test_changes_returns_events(client):
    d = client.changes(bbox=(4.99, 45.99, 5.03, 46.02), date_from="2015-01-01")
    assert d["events"]


def test_error_is_typed(client):
    with pytest.raises(ConfiniaError) as ei:
        client.history("00000")          # unknown code -> 404
    assert ei.value.status == 404


def test_attributions(client):
    a = client.attributions()
    assert any(s["source"] == "insee-cog" for s in a["sources"])
