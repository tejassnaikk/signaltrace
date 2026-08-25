"""End-to-end deterministic investigation. No LLM.

Assembly only - every threshold and formula comes from modules already built
and validated. This is the Phase 1 baseline that the Phase 2 agent must beat.

Flow:
    1. detect which components fired on a date
    2. build segment-level expectations for each fired metric x dimension
    3. validate every eligible segment (contribution -> materiality -> test -> BH)
    4. record everything, including negatives, in the ledger
    5. rank by contribution and decompose revenue into factor shares
"""
from __future__ import annotations

import os
import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from detection.anomaly import annotate_concentration, detect_components
from detection.config import COMPONENTS
from investigation.attribution import multiplicative_contributions
from investigation.evidence import Ledger
from investigation.validator import to_frame, validate_segments
from metrics.dependency_graph import DIMENSIONS, REVENUE_FACTORS
from metrics.segments import segment_series

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "signaltrace.duckdb")


def _seg_expectation(seg: pd.DataFrame, col: str, weeks: int) -> pd.DataFrame:
    """Trailing same-weekday median per segment. Same method as the top level,
    applied within each segment. Strictly backward-looking."""
    piv = seg.pivot(index="date", columns="segment", values=col).sort_index()
    # Median across the `weeks` same-weekday lags, computed per column.
    # (pandas 2.x removed groupby(axis=1), so stack the lags and take the
    # element-wise median directly.)
    lags = np.stack([piv.shift(7 * k).to_numpy(dtype=float)
                     for k in range(1, weeks + 1)])
    exp = pd.DataFrame(np.nanmedian(lags, axis=0),
                       index=piv.index, columns=piv.columns)
    # A column with fewer than `weeks` observed lags must stay NaN rather than
    # silently median over whatever happens to exist.
    exp = exp.mask(np.isnan(lags).any(axis=0))
    return piv, exp


def investigate(date, con=None, verbose: bool = True) -> dict:
    date = pd.Timestamp(date)
    own = con is None
    con = con or duckdb.connect(DB)

    daily = con.execute("SELECT * FROM metrics_daily ORDER BY date").df()
    daily["date"] = pd.to_datetime(daily["date"])
    conc = con.execute("SELECT * FROM order_concentration ORDER BY date").df()
    conc["date"] = pd.to_datetime(conc["date"])

    # 1. what fired
    anoms = annotate_concentration(detect_components(daily), conc)
    today = anoms[anoms["date"] == date]
    fired = today[today["is_anomaly"] & ~today["single_order_driven"]]
    suppressed = today[today["is_anomaly"] & today["single_order_driven"]]

    incident_id = f"inc_{date:%Y%m%d}"
    led = Ledger(incident_id, date)

    if verbose:
        print(f"\n{'='*70}\nINVESTIGATION {incident_id}  ({date:%A %Y-%m-%d})\n{'='*70}")
        if fired.empty and suppressed.empty:
            print("no component fired on this date")
        for r in fired.itertuples():
            print(f"  FIRED      {r.metric:16s} {r.pct_dev:+7.1%}  z={r.robust_z:+.2f}")
        for r in suppressed.itertuples():
            print(f"  SUPPRESSED {r.metric:16s} {r.pct_dev:+7.1%}  "
                  f"single-order (share={r.top_order_share:.2f})")

    # concentration findings are evidence, not silent suppression
    for r in suppressed.itertuples():
        class _R:  # minimal shim matching validator.Result fields
            hid, kind, metric = f"C-{r.metric}", "concentration", r.metric
            dimension = segment = None
            actual, expected = r.actual, r.expected
            pct_change, contribution, abs_contribution = r.pct_dev, np.nan, np.nan
            p_value = q_value = np.nan
            verdict = "explained_single_order"
            detail = (f"top order = {r.top_order_share:.1%} of daily revenue, "
                      f"{r.max_over_median:.0f}x the day's median order")
        led.record(_R())

    # 2-4. segment investigation for each fired metric
    all_results = []
    for m in fired["metric"].unique():
        weeks = COMPONENTS[m]["weeks"]
        num, den = {"conversion_rate": ("transactions", "sessions"),
                    "aov": ("revenue", "transactions")}.get(m, (m, None))
        for dim in DIMENSIONS:
            seg = segment_series(dim, con)
            cols = [num] + ([den] if den else [])
            act_row, exp_row = {}, {}
            ok = True
            for c in cols:
                piv, exp = _seg_expectation(seg, c, weeks)
                if date not in piv.index or exp.loc[date].isna().all():
                    ok = False
                    break
                act_row[c], exp_row[c] = piv.loc[date], exp.loc[date]
            if not ok:
                continue
            act = pd.DataFrame(act_row).dropna()
            exp = pd.DataFrame(exp_row).reindex(act.index).dropna()
            act = act.reindex(exp.index)
            if act.empty:
                continue
            res = validate_segments(m, dim, act, exp, hid_base=f"{m[:4]}-{dim[:3]}")
            led.record_many(res, metric=m)
            all_results.extend(res)

    # 5. revenue factor decomposition
    row = daily[daily["date"] == date]
    factor_tbl = None
    if not row.empty:
        exp_map = {}
        for f in REVENUE_FACTORS:
            from detection.expectation import build_expectation
            e = build_expectation(daily, f, weeks=COMPONENTS[f]["weeks"])
            e = e[e["date"] == date]
            if not e.empty and not pd.isna(e["expected"].iloc[0]):
                exp_map[f] = float(e["expected"].iloc[0])
        if len(exp_map) == len(REVENUE_FACTORS):
            act_map = {f: float(row[f].iloc[0]) for f in REVENUE_FACTORS}
            factor_tbl = multiplicative_contributions(act_map, exp_map)

    if verbose:
        if factor_tbl is not None:
            print("\nREVENUE FACTOR DECOMPOSITION")
            print(factor_tbl[["actual", "expected", "pct_change",
                              "contribution", "abs_contribution"]]
                  .round(4).to_string())
        sup = [e for e in led.supported() if e.kind in ("segment", "mix")]
        print(f"\nRANKED EXPLANATIONS  ({len(led.entries)} tests run)")
        if not sup:
            print("  none reached the materiality floor")
        for e in sorted(sup, key=lambda x: -abs(x.contribution or 0))[:8]:
            loc = f"{e.dimension}={e.segment}" if e.segment else f"{e.dimension} mix"
            q = "n/a" if e.q_value is None else f"{e.q_value:.4f}"
            print(f"  {e.metric:16s} {loc:28s} contrib={e.contribution:+7.1%} "
                  f"q={q:>8s} {e.verdict} [{e.basis}]")
        print(f"\nNEGATIVE EVIDENCE: {len(led.negative())} tests ran and did "
              f"not support a cause")
        print(led.summary())

    if own:
        con.close()
    return {"incident_id": incident_id, "date": date, "fired": fired,
            "ledger": led, "factors": factor_tbl,
            "results": to_frame(all_results) if all_results else pd.DataFrame()}


if __name__ == "__main__":
    con = duckdb.connect(DB)
    targets = sys.argv[1:] or ["2016-12-26", "2017-03-04", "2017-04-27"]
    for d in targets:
        investigate(d, con)
    con.close()
