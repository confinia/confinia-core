# SOURCES.md — data sources: integrated, candidates, licence rules

Two things live here:

1. **What Confinia already serves** — the authoritative registry is the
   `data_source` table (`ingestion/sources.sql`), which drives the per-response
   attribution and the report footer. The table below mirrors it for readers.
2. **Candidate datasets** — commune-keyed series we could join to the temporal
   boundary model, with their licence and depth, so the licence question is
   settled *before* any ingestion work starts.

## The selection filter

A dataset is worth integrating when it meets all three:

1. it is keyed by a **commune code** (joinable),
2. it is a **time series** — so it *breaks* at every merge, split or rename, which
   is precisely what Confinia repairs,
3. someone already pays for it in some form (a demand signal).

A dataset that fails (2) turns Confinia into a generic data portal — a maintenance
treadmill with no advantage. The value is not hosting the data; it is serving it
**per version, with the events that explain each break**, and reconciling series
across boundary changes (see the passage tables, issue #21).

Deliberate pace: **at most one new source per quarter.** Maintenance, not
ingestion, is the real cost, and the product must keep running unattended.

## Integrated (registry: `ingestion/sources.sql`)

| Source key | Provider | Licence | Coverage |
|---|---|---|---|
| `insee-cog` | INSEE, Code officiel géographique | Licence Ouverte 2.0 | FR temporal model, dated events since 1943 |
| `ign-admin-express` | IGN, Admin Express COG | Licence Ouverte 2.0 | FR geometries, editions 2017–2026 |
| `trf-gis` | Victor Gay, TRF-GIS | CC BY 4.0 | FR annual commune nomenclature 1870–1940 |
| `eurostat-nuts` | EuroGeographics / GISCO | © EuroGeographics | 7 NUTS versions 2003–2024 |
| `eurostat-lau` | EuroGeographics / GISCO | © EuroGeographics | LAU editions 2016–2023 |
| `bkg-vg250` | GeoBasis-DE / BKG | dl-de/by-2-0 | DE Gemeinden, VG250 2016–2025 |
| `cbs-pdok` | CBS / Kadaster | CC BY 4.0 | NL Gemeenten 2016–2026 |
| `statsnz` | Stats NZ | CC BY 4.0 | NZ Territorial Authorities 2010–2026 |
| `banatic` | BANATIC, Ministère de l'Intérieur | Licence Ouverte 2.0 | FR EPCI, current perimeter |
| `ons-chd` | ONS, Code History Database | OGL v3 | UK GSS code history (in progress) |
| `dbip-country-lite` | DB-IP | CC BY 4.0 | Call-country GeoIP (observability); never the IP |

## Candidates (FR open data, not integrated)

| Dataset | Provider | Licence | Depth | Temporal fit |
|---|---|---|---|---|
| **Historical population** | INSEE, census series | Licence Ouverte | **1876→** | ★★★ |
| **Property prices (DVF)** | DGFiP via data.gouv.fr | Licence Ouverte | 2014→ | ★★★ |
| Facilities (schools, health, shops) | INSEE BPE | Licence Ouverte | ~2007→ annual | ★★ |
| Elections | Ministère de l'Intérieur | Licence Ouverte | 2002→ | ★★ |
| Municipal budgets | DGFiP, comptes des communes | Licence Ouverte | ~2000→ | ★★ |
| Events, Wikipedia links | **Wikidata** | **CC0** | — | ★★ (narrative) |
| Radio masts (install dates) | ANFR / Cartoradio | Licence Ouverte | dated installs | ★★ |
| Deaths, demography | INSEE | Licence Ouverte | 1970→ | ★★ |
| Road accidents | BAAC / ONISR | Licence Ouverte | 2005→ | ★ |
| Power plants | ODRE (RTE) | Licence Ouverte | commissioning dates | ★ |
| Water levels / flows | Hub'Eau | Licence Ouverte | long | ★ |
| Weather | Météo-France open data | Licence Ouverte | long | ★ (station ≠ commune) |

First candidate under way: **historical population** — issue #88.

Every entry above must be **re-verified before ingestion** (licences, URLs and
publication formats move); this table records the state at the time of writing,
not a guarantee.

## Licence rules (read before integrating anything)

- **Licence Ouverte 2.0 / CC BY 4.0 / OGL v3 / dl-de/by-2-0** — attribution only,
  commercial use allowed. Compatible with a paid API. Register the attribution in
  `data_source`; the response and report footers pick it up automatically.
- **CC0 (Wikidata)** — no obligation at all. Safe.
- ⚠️ **ODbL (OpenStreetMap)** — share-alike, and it propagates to a derived
  database. Do **not** ingest into our tables; link out or render only.
- ⚠️ **CC BY-SA (Wikipedia article text)** — share-alike. Link to articles, never
  copy the prose. Wikidata (CC0) is the safe way to get the same facts.
- The registry already carries `commercial_use` and `share_alike` columns: any new
  source must set them honestly, so that filtering by terms of use stays a `WHERE`.

## Privacy

Aggregates only. Individual civil-status records (births, marriages) are personal
data and are never ingested, regardless of availability. Commune-level counts are
fine. GeoIP is used for call-country statistics only, and the IP itself is never
stored (see `dbip-country-lite`).
