"""Distinct-town report quota (issue #83): a "report" is a town, counted ONCE
per caller — re-downloading the same town is free; a new town consumes one unit.
Each test uses a fresh API key so its free-tier allowance is isolated. The
fixture has Testville (99901) and Testville-B (99902), both with geometry."""
import uuid

import requests


def _key(base):
    email = f"quota-{uuid.uuid4().hex[:8]}@test.confinia.io"
    r = requests.post(f"{base}/v1/keys", json={"email": email, "note": "quota test"})
    assert r.status_code == 201, r.text
    return r.json()["key"]


def _quota(base, key, code="99901"):
    r = requests.get(f"{base}/v1/reports/quota",
                     params={"country": "FR", "code": code}, headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return r.json()


def _download(base, key, code="99901"):
    return requests.get(f"{base}/v1/communes/{code}/report.svg", headers={"X-API-Key": key})


def test_quota_endpoint_shape(base):
    q = _quota(base, _key(base))
    assert {"tier", "used", "limit", "remaining", "unlocked"} <= set(q)
    assert q["tier"] == "free" and q["limit"] == 10
    assert q["remaining"] == 10 and q["unlocked"] is False   # nothing consumed by a read


def test_distinct_town_counts_once(base):
    key = _key(base)
    # first download of 99901 consumes one
    r = _download(base, key)
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Premium-Remaining") == "9"
    q = _quota(base, key)
    assert q["remaining"] == 9 and q["unlocked"] is True

    # re-downloading the SAME town is free — remaining unchanged
    r = _download(base, key)
    assert r.status_code == 200
    assert r.headers.get("X-Premium-Remaining") == "9"
    assert _quota(base, key)["remaining"] == 9

    # a DIFFERENT town consumes another unit
    r = _download(base, key, "99902")
    assert r.status_code == 200
    assert _quota(base, key, "99902")["remaining"] == 8


def test_quota_unlocked_flag_is_per_town(base):
    key = _key(base)
    _download(base, key, "99901")
    assert _quota(base, key, "99901")["unlocked"] is True    # opened
    assert _quota(base, key, "99902")["unlocked"] is False   # not opened
