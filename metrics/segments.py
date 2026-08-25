"""Segment-level metric series with eligibility gating."""
from __future__ import annotations

import os
import sys

import duckdb
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from detection.config import SEGMENT_GATES
from metrics.dependency_graph import DIMENSIONS

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "signaltrace.duckdb")


def segment_series(dimension: str, con=None) -> pd.DataFrame:
    """Daily metrics for one dimension. Ratios come from SUMS, never from
    averaging cell-level ratios."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}")
    own = con is None
    con = con or duckdb.connect(DB)
    df = con.execute(f"""
        SELECT date, {dimension} AS segment,
               SUM(sessions) AS sessions,
               SUM(transactions) AS transactions,
               SUM(revenue) AS revenue,
               CASE WHEN SUM(sessions) > 0
                    THEN SUM(transactions)*1.0/SUM(sessions) END AS conversion_rate,
               CASE WHEN SUM(transactions) > 0
                    THEN SUM(revenue)*1.0/SUM(transactions) END AS aov
        FROM fact_sessions GROUP BY date, {dimension} ORDER BY date, {dimension}
    """).df()
    if own:
        con.close()
    df["date"] = pd.to_datetime(df["date"])
    return df


def eligibility(df: pd.DataFrame) -> pd.DataFrame:
    """Mark which metrics each segment-day may legitimately be tested on.

    Social has 226K sessions but 131 txns/year; Affiliates 9. Those support
    session-level anomalies ONLY. A ratio without a volume floor lies on small
    denominators - same failure mode as the concentration check on thin days.
    """
    df = df.copy()
    df["ok_sessions"] = df["sessions"] >= SEGMENT_GATES["min_sessions_per_day"]
    df["ok_ratios"] = df["transactions"] >= SEGMENT_GATES["min_txns_per_day"]
    return df


if __name__ == "__main__":
    con = duckdb.connect(DB)
    for dim in DIMENSIONS:
        df = eligibility(segment_series(dim, con))
        print(f"\n=== {dim}  ({df['segment'].nunique()} segments, "
              f"{len(df)} segment-days) ===")
        agg = (df.groupby("segment")
                 .agg(days=("date", "nunique"), sessions=("sessions", "sum"),
                      txns=("transactions", "sum"), revenue=("revenue", "sum"),
                      pct_ok_sess=("ok_sessions", "mean"),
                      pct_ok_ratio=("ok_ratios", "mean"))
                 .sort_values("sessions", ascending=False))
        agg["revenue"] = agg["revenue"].round(0)
        agg["pct_ok_sess"] = (agg["pct_ok_sess"] * 100).round(0)
        agg["pct_ok_ratio"] = (agg["pct_ok_ratio"] * 100).round(0)
        print(agg.to_string())
    con.close()
