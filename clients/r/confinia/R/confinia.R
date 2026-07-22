# Confinia API client. Mirrors the Python client; returns data.frames, or sf
# objects when the sf package is available. Keyless during the beta; set a key
# with confinia(api_key = "...") or the CONFINIA_API_KEY environment variable.

#' Create a Confinia API client
#' @param api_key Optional API key (else CONFINIA_API_KEY env var).
#' @param base_url API base URL.
#' @return A client object (list) to pass to the verbs.
#' @export
confinia <- function(api_key = Sys.getenv("CONFINIA_API_KEY"),
                     base_url = "https://api.confinia.io") {
  structure(list(base_url = sub("/$", "", base_url),
                 api_key = if (nzchar(api_key)) api_key else NULL),
            class = "confinia")
}

.get <- function(client, path, query = list()) {
  query <- query[!vapply(query, is.null, logical(1))]
  req <- httr2::request(paste0(client$base_url, path))
  req <- httr2::req_url_query(req, !!!query)
  if (!is.null(client$api_key)) req <- httr2::req_headers(req, `X-API-Key` = client$api_key)
  req <- httr2::req_error(req, is_error = function(resp) FALSE)
  resp <- httr2::req_perform(req)
  body <- jsonlite::fromJSON(httr2::resp_body_string(resp), simplifyVector = FALSE)
  if (httr2::resp_status(resp) >= 400) {
    stop(sprintf("Confinia API error %d: %s", httr2::resp_status(resp),
                 body$detail %||% "error"), call. = FALSE)
  }
  body
}

`%||%` <- function(a, b) if (is.null(a)) b else a

.features_to_df <- function(fc) {
  feats <- if (!is.null(fc$features)) fc$features else list(fc)
  props <- lapply(feats, function(f) as.data.frame(f$properties, stringsAsFactors = FALSE))
  df <- do.call(rbind, props)
  if (requireNamespace("sf", quietly = TRUE) &&
      length(feats) && !is.null(feats[[1]]$geometry)) {
    geoms <- lapply(feats, function(f) {
      if (is.null(f$geometry)) return(sf::st_geometrycollection())
      sf::st_geometry(sf::read_sf(jsonlite::toJSON(f$geometry, auto_unbox = TRUE)))[[1]]
    })
    df <- sf::st_sf(df, geometry = sf::st_sfc(geoms, crs = 4326))
  }
  df
}

#' Administrative unit at a date, by point or by code.
#' @export
unit_at <- function(client, at, lat = NULL, lon = NULL, code = NULL, country = NULL) {
  .features_to_df(.get(client, "/v1/units",
    list(at = at, lat = lat, lon = lon, code = code, country = country)))
}

#' Every version of a unit, with dated events.
#' @export
history <- function(client, code, country = NULL, geometry = FALSE) {
  path <- if (is.null(country) || country == "FR")
    sprintf("/v1/communes/%s/history", code) else sprintf("/v1/units/%s/history", code)
  res <- .get(client, path, list(
    country = if (!is.null(country) && country != "FR") country else NULL,
    geometry = tolower(as.character(geometry))))
  list(versions = .features_to_df(list(features = res$versions)),
       events = do.call(rbind, lapply(res$events, as.data.frame, stringsAsFactors = FALSE)))
}

#' Premium: dated boundary changes in a bbox, fully sourced.
#' @param bbox Numeric vector c(west, south, east, north).
#' @export
changes <- function(client, bbox, date_from = NULL, date_to = NULL) {
  res <- .get(client, "/v1/changes", list(
    bbox = paste(bbox, collapse = ","), from = date_from, to = date_to))
  do.call(rbind, lapply(res$events, function(e) as.data.frame(e, stringsAsFactors = FALSE)))
}

#' Passage table: source unit (at a vintage) -> target codes with weights.
#' @export
passage <- function(client, code, date_from, date_to, country = "FR") {
  res <- .get(client, "/v1/passage",
    list(code = code, from = date_from, to = date_to, country = country))
  do.call(rbind, lapply(res$targets, as.data.frame, stringsAsFactors = FALSE))
}

#' The source registry (licence and attribution per source).
#' @export
attributions <- function(client) {
  res <- .get(client, "/v1/attributions")
  do.call(rbind, lapply(res$sources, as.data.frame, stringsAsFactors = FALSE))
}
