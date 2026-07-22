# confinia (Python client)

Thin client for the [Confinia](https://www.confinia.io) API: which
administrative unit existed at a given date, with full lineage and
per-source provenance.

```python
from confinia import Confinia

c = Confinia()                                   # keyless during the beta
c.unit_at(lat=48.85, lon=2.35, at="2020-06-01")  # the commune there in 2020
c.history("75056")                               # every version + events
c.changes(bbox=(2.2, 48.7, 2.5, 48.95), date_from="2015-01-01")  # premium
```

Set a key with `Confinia(api_key="...")` or the `CONFINIA_API_KEY` env var.
Optional extras: `pip install "confinia[geo]"` for a GeoDataFrame via
`c.units_frame(...)`. Data attribution is required (see `c.attributions()`).
