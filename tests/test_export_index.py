"""A limit=1 export must not read the table (found by a deploy smoke timeout).

/v1/export/ohm filters on (country, unit_type) and orders by (code, valid_from).
With only an ordering index, PostgreSQL satisfies the ORDER BY and then throws
rows away one at a time. Measured on the passive colour:

    Index Scan using idx_cv_code_validity
      Filter: country = 'FR' AND unit_type = 'epci'
      Rows Removed by Filter: 73 582
      Buffers: shared hit=41111 read=11965        (~414 MB)
      Execution Time: 3134 ms                     -- and that is WARM

Cold it took 71 s and timed out the smoke at 30 s. Warming the colour before
the smoke had hidden it once; the cache emptied faster than the warm-up filled
it, which is the signal that the plan was the problem all along.

With a composite index the filter becomes an index condition: 5 pages read.
"""
import os

SQL = open(os.path.join(os.path.dirname(__file__), "..", "ingestion", "ingest_cog.py"),
           encoding="utf-8").read()


def test_an_index_serves_both_the_filter_and_the_order():
    assert "idx_cv_country_type_code_vf" in SQL
    cols = SQL.split("idx_cv_country_type_code_vf ON commune_version (")[1].split(")")[0]
    assert cols.replace(" ", "") == "country,unit_type,code,valid_from", \
        f"filter columns first, then the sort: got {cols}"


def test_the_ordering_index_is_kept():
    """Other queries look a commune up by code across time; removing it would
    trade one slow path for another."""
    assert "idx_cv_code_validity ON commune_version (code, valid_from, valid_to)" in SQL
