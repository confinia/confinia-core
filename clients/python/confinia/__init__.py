"""Confinia — thin Python client for the temporal administrative-boundaries API.

    from confinia import Confinia
    c = Confinia()                       # keyless during the beta
    unit = c.unit_at(lat=48.85, lon=2.35, at="2020-06-01")
    hist = c.history("75056")            # every version of a commune
    changes = c.changes(bbox=(2.2, 48.7, 2.5, 48.95), date_from="2015-01-01")

Set an API key with Confinia(api_key="...") or the CONFINIA_API_KEY env var.
"""
from __future__ import annotations

import os
from typing import Any

import requests

__version__ = "0.1.0"
DEFAULT_BASE = "https://api.confinia.io"


class ConfiniaError(RuntimeError):
    """API returned a non-success status (carries .status and .detail)."""

    def __init__(self, status: int, detail: Any):
        super().__init__(f"Confinia API error {status}: {detail}")
        self.status = status
        self.detail = detail


class Confinia:
    def __init__(self, api_key: str | None = None, base_url: str = DEFAULT_BASE,
                 timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("CONFINIA_API_KEY")
        self.timeout = timeout
        self._s = requests.Session()
        if self.api_key:
            self._s.headers["X-API-Key"] = self.api_key

    def _get(self, path: str, **params) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        r = self._s.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        if not r.ok:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise ConfiniaError(r.status_code, detail)
        return r.json()

    # --- core lookups -----------------------------------------------------
    def unit_at(self, at: str, lat: float | None = None, lon: float | None = None,
                code: str | None = None, country: str | None = None) -> dict:
        """The administrative unit at a date, by point or by code."""
        return self._get("/v1/units", at=at, lat=lat, lon=lon, code=code,
                         country=country)

    def history(self, code: str, country: str | None = None,
                geometry: bool = False) -> dict:
        """Every version of a unit, with dated events (mergers, splits…)."""
        path = f"/v1/communes/{code}/history" if country in (None, "FR") \
            else f"/v1/units/{code}/history"
        return self._get(path, country=None if country in (None, "FR") else country,
                         geometry=str(geometry).lower())

    def changes(self, bbox: tuple[float, float, float, float],
                date_from: str | None = None, date_to: str | None = None) -> dict:
        """Premium: all dated boundary changes in a bbox, fully sourced."""
        w, s, e, n = bbox
        return self._get("/v1/changes", bbox=f"{w},{s},{e},{n}",
                         **{"from": date_from, "to": date_to})

    def attributions(self) -> dict:
        """The source registry (licence and attribution per source)."""
        return self._get("/v1/attributions")

    # --- pandas / geopandas helpers (optional deps) -----------------------
    def units_frame(self, features: dict):
        """FeatureCollection -> GeoDataFrame if geopandas is available, else
        a plain DataFrame of the properties."""
        feats = features.get("features", [features]) \
            if features.get("type") != "FeatureCollection" else features["features"]
        try:
            import geopandas as gpd
            from shapely.geometry import shape
            rows = [{**f["properties"],
                     "geometry": shape(f["geometry"]) if f.get("geometry") else None}
                    for f in feats]
            return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
        except ImportError:
            import pandas as pd
            return pd.DataFrame([f["properties"] for f in feats])
