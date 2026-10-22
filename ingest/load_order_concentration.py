"""Load the per-day order distribution CSV into the `order_concentration` table.

Input : data/raw/order_concentration.csv (second result set of sql/ga360_export.sql)
Output: table `order_concentration` in data/signaltrace.duckdb, one row per day with orders.

Run after ingest/load_ga.py, from the repo root:
    python ingest/load_order_concentration.py
Optional: --csv PATH --db PATH (used by tests to avoid touching the real database).
"""
import argparse
import os
import sys
import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CSV = os.path.join(ROOT, "data", "raw", "order_concentration.csv")
DEFAULT_DB = os.path.join(ROOT, "data", "signaltrace.duckdb")


def load(csv_path: str, db_path: str) -> int:
    con = duckdb.connect(db_path)
    con.execute("DROP TABLE IF EXISTS order_concentration")
    # Columns and types are those the engine reads (detection/anomaly.py,
    # investigation/orchestrator.py, evaluation/injector.py: SELECT * ... order_concentration).
    con.execute(f"""
        CREATE TABLE order_concentration AS
        SELECT
            CAST(date AS DATE)              AS date,
            CAST(order_count AS BIGINT)     AS order_count,
            CAST(total_revenue AS DOUBLE)   AS total_revenue,
            CAST(max_order AS DOUBLE)       AS max_order,
            CAST(median_order AS DOUBLE)    AS median_order,
            CAST(top_order_share AS DOUBLE) AS top_order_share
        FROM read_csv_auto('{csv_path}', header=true)
        ORDER BY date
    """)
    n = con.execute("SELECT COUNT(*) FROM order_concentration").fetchone()[0]
    d0, d1 = con.execute("SELECT MIN(date), MAX(date) FROM order_concentration").fetchone()
    con.close()
    print(f"order_concentration rows : {n}  ({d0} to {d1})")
    return n


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--db", default=DEFAULT_DB)
    a = ap.parse_args(argv)
    if not os.path.exists(a.csv):
        sys.exit(f"Source CSV not found at {a.csv}\n"
                 "Run sql/ga360_export.sql in BigQuery and save the second result set there.")
    load(a.csv, a.db)


if __name__ == "__main__":
    main()
