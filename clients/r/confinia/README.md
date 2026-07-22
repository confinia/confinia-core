# confinia (R client)

Thin client for the [Confinia](https://www.confinia.io) API, drop-in for the
COGugaison-style workflow.

```r
# install.packages("remotes"); remotes::install_github("confinia/confinia-core", subdir = "clients/r/confinia")
library(confinia)
c <- confinia()                                   # keyless during the beta
unit_at(c, lat = 48.85, lon = 2.35, at = "2020-06-01")
history(c, "75056")                               # versions + events
passage(c, "01091", date_from = "2018-06-01", date_to = "2020-06-01")
```

Returns data.frames, or `sf` objects when the `sf` package is installed. Set a
key with `confinia(api_key = "...")` or `CONFINIA_API_KEY`. Attribution is
required (`attributions(c)`).
