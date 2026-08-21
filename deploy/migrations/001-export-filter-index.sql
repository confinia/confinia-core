-- The export endpoint filters on (country, unit_type) and orders by
-- (code, valid_from). With only the ordering index, PostgreSQL satisfied the
-- ORDER BY and discarded rows one at a time: 73 582 removed by filter and
-- 53 076 buffer pages read to return 2 rows -- 3.1 s warm, 71 s cold.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cv_country_type_code_vf
  ON commune_version (country, unit_type, code, valid_from);
