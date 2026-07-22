"""Fixtures partagées : l'API tourne déjà (lancée par le workflow ou en local),
BASE pointe dessus. Les tests parlent HTTP à la vraie application."""
import os

import pytest
import requests

BASE = os.environ.get("TEST_API_BASE", "http://127.0.0.1:8000")

# Secret et produits de TEST : mêmes valeurs que dans le workflow CI.
WEBHOOK_SECRET = os.environ.get(
    "POLAR_WEBHOOK_SECRET", "whsec_Y29uZmluaWEtY2ktd2ViaG9vay1zZWNyZXQtMDAwMQ==")
PRODUCT_PRO = os.environ.get("POLAR_PRODUCT_PRO", "prod-pro-test")
PRODUCT_ENTERPRISE = os.environ.get("POLAR_PRODUCT_ENTERPRISE", "prod-ent-test")


@pytest.fixture(scope="session")
def base():
    r = requests.get(f"{BASE}/healthz", timeout=10)
    r.raise_for_status()
    return BASE
