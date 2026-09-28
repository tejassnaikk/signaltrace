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

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from detection.config import EXCLUDE_DATES, RESIDUAL_SCALE

WEEKS = 4                      # trailing same-weekday window


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
                      weeks: int = WEEKS,
                      residual_scale: str | None = None,
                      weeks_abs: int | None = None) -> pd.DataFrame:
    """Per-metric expectation frame: actual, expected, residual, pct_dev, z.

    Excluded dates are masked BEFORE the baseline is computed so a known bad
    day cannot contaminate the expectation of the four weeks that follow it.

    `residual_scale` ("absolute" | "log" | "hybrid", default detection.
    config.RESIDUAL_SCALE) picks which residual feeds `robust_z` -
    PLAN.md 3.11 (M2b).

    "absolute": residual = actual - expected, unchanged from the original
    design.

    "log": residual = ln(actual) - ln(expected), computed by taking the
    same trailing same-weekday median in log space rather than logging an
    absolute-space median. Fixes the asymmetry where a decline is bounded at
    -100% but a rise is not - but measured to cost sensitivity to increases
    symmetrically, including 3 of 4 known AOV whale days (PLAN.md 3.11).

    "hybrid" (adopted): declines use the log residual (`weeks` = the log
    window), increases use the absolute residual (`weeks_abs` = the
    absolute window, defaults to `weeks` if not given). Each tail gets its
    OWN robust_z computed from its OWN history - the two residuals are never
    pooled into one MAD (their units differ), only the resulting z-scores
    are combined, one per day, by whichever tail that day's log-residual
    sign says it belongs to. Derived from the mechanism (an absolute
    decline is bounded at -100%, log compresses the upper tail), not from
    picking whichever table looked best.

    `expected`/`pct_dev` are always reported in the metric's own units
    (exponentiated back for the log tail), so callers see no schema change
    regardless of scale.
    """
    residual_scale = residual_scale or RESIDUAL_SCALE
    s = df.set_index("date")[metric].astype(float).sort_index()
    s.index = pd.DatetimeIndex(s.index)

    excl = pd.DatetimeIndex([d for d in EXCLUDE_DATES if d in s.index.astype(str).values])
    clean = s.copy()
    clean.loc[excl] = np.nan

    if residual_scale == "absolute":
        expected = seasonal_naive_baseline(clean, weeks)
        resid = s - expected
        z, center, scale = robust_z(resid)
        pct_dev = np.where(
            (expected.values != 0) & ~pd.isna(expected.values),
            resid.values / np.where(expected.values == 0, np.nan, expected.values),
            np.nan)

    elif residual_scale == "log":
        # median-of-lags taken IN LOG SPACE, not logged after the fact - the
        # two differ (median commutes with a monotonic transform, but the
        # trailing window's NaN-masking of excluded/missing days does not
        # commute the same way once zeros/negatives are involved), and only
        # the log-space median matches what was actually swept and frozen
        # (PLAN.md 3.11).
        log_clean = np.log(clean.where(clean > 0))
        log_s = np.log(s.where(s > 0))
        expected_log = seasonal_naive_baseline(log_clean, weeks)
        expected = np.exp(expected_log)
        resid = log_s - expected_log
        z, center, scale = robust_z(resid)
        pct_dev = np.exp(resid.values) - 1.0   # ln(actual/expected) -> actual/expected - 1

    elif residual_scale == "hybrid":
        weeks_abs = weeks_abs or weeks
        log_clean = np.log(clean.where(clean > 0))
        log_s = np.log(s.where(s > 0))
        expected_log = seasonal_naive_baseline(log_clean, weeks)
        resid_log = log_s - expected_log
        z_log, center_log, scale_log = robust_z(resid_log)

        expected_abs = seasonal_naive_baseline(clean, weeks_abs)
        resid_abs = s - expected_abs
        z_abs, center_abs, scale_abs = robust_z(resid_abs)

        # Direction decided by the log tail's own sign (always well-defined:
        # ln(actual) vs ln(expected_log) agrees with actual vs expected_log
        # exactly, since ln is monotonic). A decline uses the log z/expected/
        # residual for that day; an increase uses the absolute z/expected/
        # residual - never a value blended from both.
        is_decline = resid_log < 0
        z = z_log.where(is_decline, z_abs)
        expected = np.exp(expected_log).where(is_decline, expected_abs)
        resid = resid_log.where(is_decline, resid_abs)  # mixed units - sign/display only, not re-scored
        pct_dev = np.where(is_decline.to_numpy(),
                           np.exp(resid_log.to_numpy()) - 1.0,
                           np.where((expected_abs.to_numpy() != 0) & ~pd.isna(expected_abs.to_numpy()),
                                    resid_abs.to_numpy() / np.where(expected_abs.to_numpy() == 0, np.nan,
                                                                    expected_abs.to_numpy()),
                                    np.nan))
        center, scale = (center_log, center_abs), (scale_log, scale_abs)  # no single pooled pair in hybrid mode
    else:
        raise ValueError(f"unknown residual_scale {residual_scale!r}")

    out = pd.DataFrame({
        "date": s.index,
        "metric": metric,
        "actual": s.values,
        "expected": expected.values if hasattr(expected, "values") else expected,
        "residual": resid.values if hasattr(resid, "values") else resid,
        "pct_dev": pct_dev,
        "robust_z": z.values if hasattr(z, "values") else z,
    })
    out["excluded"] = out["date"].isin(excl)
    out.attrs["center"] = center
    out.attrs["scale"] = scale
    out.attrs["residual_scale"] = residual_scale
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
