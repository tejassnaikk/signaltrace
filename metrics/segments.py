"""Segment-level metric series with eligibility gating."""
from __future__ import annotations

import os
import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from detection.config import EXCLUDE_DATES, SEGMENT_GATES
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


def segment_expectation(seg: pd.DataFrame, col: str, weeks: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Trailing same-weekday median per segment. Same method as the top level,
    applied within each segment. Strictly backward-looking.

    Shared by investigation/orchestrator.py (segment validation) and
    investigation/dispersion.py (phi measurement) - moved here from
    orchestrator.py so both can use one implementation without a circular
    import between them."""
    piv = seg.pivot(index="date", columns="segment", values=col).sort_index()
    # Mask known bad days BEFORE taking lags, as build_expectation does at the
    # top level, so the 2016-08-03 data gap cannot enter any segment baseline.
    clean = piv.copy()
    clean.loc[clean.index.isin(pd.to_datetime(sorted(EXCLUDE_DATES)))] = np.nan
    # Median across the `weeks` same-weekday lags, computed per column.
    # (pandas 2.x removed groupby(axis=1), so stack the lags and take the
    # element-wise median directly.)
    lags = np.stack([clean.shift(7 * k).to_numpy(dtype=float)
                     for k in range(1, weeks + 1)])
    exp = pd.DataFrame(np.nanmedian(lags, axis=0),
                       index=piv.index, columns=piv.columns)
    # A column with fewer than `weeks` observed lags must stay NaN rather than
    # silently median over whatever happens to exist.
    exp = exp.mask(np.isnan(lags).any(axis=0))
    return piv, exp


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
