"""order_concentration loader reproduces the existing table exactly.

Exports the real table to a temporary CSV (real DB opened read-only), loads it with
ingest/load_order_concentration.py into a temporary DuckDB file, and asserts the two
tables are identical in schema and content. Never writes to data/signaltrace.duckdb.
"""
import os
import sys

import duckdb
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ingest"))
REAL_DB = os.path.join(ROOT, "data", "signaltrace.duckdb")

import load_order_concentration as loader  # noqa: E402


@pytest.mark.skipif(not os.path.exists(REAL_DB), reason="real database not present")
def test_loader_reproduces_existing_table(tmp_path):
    csv = str(tmp_path / "oc.csv")
    tmp_db = str(tmp_path / "tmp.duckdb")
    real = duckdb.connect(REAL_DB, read_only=True)
    real.execute(f"COPY (SELECT * FROM order_concentration ORDER BY date) TO '{csv}' (HEADER, DELIMITER ',')")
    real_schema = real.execute("DESCRIBE order_concentration").fetchall()
    real_rows = real.execute("SELECT * FROM order_concentration ORDER BY date").fetchall()
    real.close()

    n = loader.load(csv, tmp_db)

    new = duckdb.connect(tmp_db, read_only=True)
    new_schema = new.execute("DESCRIBE order_concentration").fetchall()
    new_rows = new.execute("SELECT * FROM order_concentration ORDER BY date").fetchall()
    new.close()

    assert n == len(real_rows) > 0
    assert [(r[0], r[1]) for r in new_schema] == [(r[0], r[1]) for r in real_schema]
    assert new_rows == real_rows
