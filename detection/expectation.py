"""Seasonal-naive expectation model.

Baseline = trailing median of the SAME WEEKDAY over the prior N weeks.

Design notes (from notes/baseline.md):
- Median not mean: the mean baseline showed a systematic -11.3% residual
  offset because right-skew from occasional whale days drags it upward.
- No trend term: monthly revenue oscillates 98K-159K with no direction
  over the full 12 months, so a trend component would fit noise.
- Weekday-aware is mandatory: Sat indexes 0.27 and Sun 0.35 against
  weekday 1.18-1.38. A non-weekday baseline is unusable here.
- Strictly backward-looking (shift by 7*weeks): no point may use its own
  value or anything after it. This is the temporal-leakage guarantee.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WEEKS = 4                      # trailing same-weekday window
EXCLUDE_DATES = {"2016-08-03"}  # data gap: 2890 sessions, 0 txns, 0 revenue


def seasonal_naive_baseline(s: pd.Series, weeks: int = WEEKS) -> pd.Series:
    """Trailing same-weekday median. Index must be a complete daily DatetimeIndex.

    For each date t, uses t-7, t-14, ..., t-7*weeks. Never t itself.
    """
    if not isinstance(s.index, pd.DatetimeIndex):
        raise TypeError("series must have a DatetimeIndex")
    lags = pd.concat([s.shift(7 * k) for k in range(1, weeks + 1)], axis=1)
    return lags.median(axis=1, skipna=False)


def robust_z(resid: pd.Series) -> tuple[pd.Series, float, float]:
    """Robust z via median/MAD. Returns (z, center, scale).

    MAD not std: an incident inflates its own threshold under std, which is
    exactly the wrong behaviour for anomaly detection.
    """
    r = resid.dropna()
    center = r.median()
    mad = np.median(np.abs(r - center))
    scale = 1.4826 * mad
    if scale == 0 or np.isnan(scale):
        return pd.Series(np.nan, index=resid.index), center, np.nan
    return (resid - center) / scale, center, scale


def build_expectation(df: pd.DataFrame, metric: str,
                      weeks: int = WEEKS) -> pd.DataFrame:
    """Per-metric expectation frame: actual, expected, residual, pct_dev, z.

    Excluded dates are masked BEFORE the baseline is computed so a known bad
    day cannot contaminate the expectation of the four weeks that follow it.
    """
    s = df.set_index("date")[metric].astype(float).sort_index()
    s.index = pd.DatetimeIndex(s.index)

    excl = pd.DatetimeIndex([d for d in EXCLUDE_DATES if d in s.index.astype(str).values])
    clean = s.copy()
    clean.loc[excl] = np.nan

    expected = seasonal_naive_baseline(clean, weeks)
    resid = s - expected
    z, center, scale = robust_z(resid)

    out = pd.DataFrame({
        "date": s.index,
        "metric": metric,
        "actual": s.values,
        "expected": expected.values,
        "residual": resid.values,
        "pct_dev": np.where(
            (expected.values != 0) & ~pd.isna(expected.values),
            resid.values / np.where(expected.values == 0, np.nan, expected.values),
            np.nan),
        "robust_z": z.values,
    })
    out["excluded"] = out["date"].isin(excl)
    out.attrs["center"] = center
    out.attrs["scale"] = scale
    return out


if __name__ == "__main__":
    import os
    import duckdb

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    con = duckdb.connect(os.path.join(root, "data", "signaltrace.duckdb"))
    df = con.execute("SELECT * FROM metrics_daily ORDER BY date").df()
    df["date"] = pd.to_datetime(df["date"])
    con.close()

    print(f"{'metric':16s} {'usable':>7s} {'scale':>10s} "
          f"{'p05':>8s} {'p50':>8s} {'p95':>8s} {'|z|>3':>6s} {'|z|>4':>6s}")
    print("-" * 74)
    for m in ["sessions", "transactions", "revenue", "conversion_rate", "aov"]:
        e = build_expectation(df, m)
        v = e.dropna(subset=["robust_z"])
        v = v[~v["excluded"]]
        print(f"{m:16s} {len(v):7d} {e.attrs['scale']:10.4f} "
              f"{v['pct_dev'].quantile(.05):+7.1%} {v['pct_dev'].quantile(.50):+7.1%} "
              f"{v['pct_dev'].quantile(.95):+7.1%} "
              f"{(v['robust_z'].abs() > 3).sum():6d} {(v['robust_z'].abs() > 4).sum():6d}")
