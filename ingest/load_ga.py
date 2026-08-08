"""Load the GA360 daily export into DuckDB and build the metric series."""
import os
import sys
import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(ROOT, "data", "raw", "ga_sessions_daily.csv")
DB = os.path.join(ROOT, "data", "signaltrace.duckdb")

if not os.path.exists(CSV):
    sys.exit(
        f"Source CSV not found at {CSV}\n"
        "data/raw is a symlink to the external SSD - check that it is mounted."
    )

con = duckdb.connect(DB)

# Grain: date x channel x device x country. This is the atomic fact table.
con.execute("DROP TABLE IF EXISTS fact_sessions")
con.execute(f"""
    CREATE TABLE fact_sessions AS
    SELECT
        CAST(date AS DATE)      AS date,
        channel,
        device,
        country,
        CAST(sessions AS BIGINT)     AS sessions,
        CAST(transactions AS BIGINT) AS transactions,
        CAST(revenue AS DOUBLE)      AS revenue
    FROM read_csv_auto('{CSV}', header=true)
""")

# Top-of-tree daily series. Conversion and AOV are DERIVED here, never stored
# upstream, so the identity Revenue = Sessions x CR x AOV holds by construction.
con.execute("DROP TABLE IF EXISTS metrics_daily")
con.execute("""
    CREATE TABLE metrics_daily AS
    SELECT
        date,
        SUM(sessions)     AS sessions,
        SUM(transactions) AS transactions,
        SUM(revenue)      AS revenue,
        CASE WHEN SUM(sessions) > 0
             THEN SUM(transactions) * 1.0 / SUM(sessions) END AS conversion_rate,
        CASE WHEN SUM(transactions) > 0
             THEN SUM(revenue) * 1.0 / SUM(transactions) END AS aov
    FROM fact_sessions
    GROUP BY date
    ORDER BY date
""")

fact_rows = con.execute("SELECT COUNT(*) FROM fact_sessions").fetchone()[0]
days, d0, d1 = con.execute(
    "SELECT COUNT(*), MIN(date), MAX(date) FROM metrics_daily"
).fetchone()

# The identity must hold exactly, or every contribution number downstream is wrong.
max_err = con.execute("""
    SELECT MAX(ABS(revenue - sessions * conversion_rate * aov))
    FROM metrics_daily WHERE transactions > 0
""").fetchone()[0]

zero_txn_days = con.execute(
    "SELECT COUNT(*) FROM metrics_daily WHERE transactions = 0"
).fetchone()[0]

print(f"fact_sessions rows : {fact_rows}")
print(f"metrics_daily days : {days}  ({d0} to {d1})")
print(f"expected calendar  : 366")
print(f"identity max error : {max_err:.9f}")
print(f"days w/ 0 txns     : {zero_txn_days}")
con.close()
