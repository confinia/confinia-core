# Consuming this API from another product

Written for **EcoBuilding**, which composes its own building report and must
carry Confinia's provenance into it. Everything here is measured against
production, not intended.

The rule that shapes the whole document: **a consumer that repeats our numbers
without our caveats states more than we do.** Two fields exist to prevent that,
and dropping them is the one integration mistake worth calling out in advance —
see *Fields you must not discard*.

## Base URL and versioning

```
https://api.confinia.io/v1/…        OpenAPI: https://api.confinia.io/docs
```

`/v1` is the compatibility promise: fields get added, never removed or retyped
under the same path.

## The three calls

### 1. Address → commune

Confinia does not geocode. EcoBuilding already resolves addresses through the
Base Adresse Nationale, so pass the coordinates:

```
GET /v1/communes?lat=43.583127&lon=1.345887&at=2026-01-01
→ {"type":"Feature", "properties":{"code":"31557","nom":"Tournefeuille", …}}
```

`at` is **required** and is a civil validity date: it selects the commune as it
stood on that day, which is the point of this API. For "today", pass today.

### 2. A dead code → its living successor

The case an old deed produces, and the one nobody else answers:

```
GET /v1/communes/31298/history
→ versions[-1].properties.children == ["31471"], valid_to "2019-01-01"
```

*Lez* ceased to exist on 1 January 2019 and its successor is *Saint-Béat-Lez*.
Route the code, do not guess from the name.

### 3. The facts, with their provenance

```
GET /v1/communes/{code}/facts?country=FR&lang=fr
```

Returns what the Confinia report states, as JSON, from the same bundle the
report renders — so the two cannot disagree. Keys: `unit`, `as_known_on`,
`summary`, `facts`, `declined`, `limitations`, `versions`, `events`,
`population`, `sources`, `attribution`.

Geometry is deliberately absent: it is heavy, `/v1/communes` already serves it,
and each version says whether one exists via `has_geometry`.

## Fields you must not discard

**`declined`** is a list, not an absence. Receiving no `rank` cannot tell you
"Confinia never computes rank" from "this rank could not be established", and
that difference is the product. Each entry carries a stable machine `reason` and
a `text` in the requested language.

**Rephrasing is expected; dropping is not.** The `reason` exists precisely so
you can write your own sentence — in your own voice, your own date format, your
own document. What must survive is the fact that something was declined and
why. Reprint our wording, or replace it entirely; just never let the absence
pass silently.

**`limitations`** are the boundaries of what the stated facts support: an
outline approximated from a later edition, periods with no boundary at all,
predecessors named but not drawn, a population recomputed for a later
territory, and the data cut-off. They are counted from the record in hand, never
generic. Same rule: rewrite them freely, drop them never.

**On dates.** Sentences in `summary`, `limitations` and `events[].detail` are
written for a human and spell their dates out. Fields — `as_known_on`,
`versions[].valid_from`, `events[].date`, `sources[].vintages` — are ISO 8601.
If your document wants a different form, compose from the fields rather than
reprinting our sentences; that is what the fields are for.

**`as_known_on`** is the data cut-off. Without it a reader cannot tell a missing
event from a not-yet-published one, and silence reads as completeness.

## Attribution is mandatory

The data is open, not unattributed. Every response that reaches a human owes the
credit:

```
GET /v1/attributions
```

`facts` carries the subset actually used by that record in `attribution`, and
`sources` names the **vintage we read** — never "latest", because a reader
verifying next year must land on what we read, not on what replaced it.

## Limits and keys

- **No API key** is required for the lookup calls.
- **20 requests/second, 400/minute, per IP.** Browser-side that is per end user;
  server-side it is shared across your fleet.
- The **report and facts** endpoints consume one *distinct-town* premium unit.
  Re-fetching a town already seen is free. EcoBuilding holds a `partner` key:
  unlimited, and still recorded — the usage is how we learn whether this data
  earns its place in a building report.

Send the key as `X-API-Key: …`.

## Two shapes to expect

`?lat&lon` and `?code` return a bare **Feature**; `?dept=` returns a
**FeatureCollection**. This trips people, so it is written down rather than
discovered.

## What to do when we are wrong

Open an issue on `confinia/confinia-core` with the code, the `at` date, and what
you expected. A fact stated wrongly is a bug of the highest order here: the
product is provenance, so a confident error costs more than a missing field.
