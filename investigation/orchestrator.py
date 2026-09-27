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
from detection.config import COMPONENTS, EXCLUDE_DATES
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


def _fmt_share(contribution: float) -> str:
    """Format a contribution as a share OF the movement, never as a signed
    change. `delta_segment / delta_total` is positive whenever the segment
    moved the same way as the total, so printing "+64.5%" beside a metric that
    FELL reads as though the segment rose. The direction belongs to the metric
    and is stated once in the header; each segment carries only its share."""
    if contribution is None or pd.isna(contribution):
        return "     n/a"
    c = contribution
    if c < 0:
        return f"{abs(c):6.1%} opp"   # moved AGAINST the metric, partly offsetting
    return f"{c:6.1%}    "


def _fmt_abs(value: float, metric: str) -> str:
    """Absolute movement, so a large share of a tiny segment cannot masquerade
    as business impact (Social is 64% of a session decline but 131 txns/yr)."""
    if value is None or pd.isna(value):
        return ""
    if metric in ("sessions", "transactions"):
        return f"{value:+,.0f}"
    if metric == "conversion_rate":
        return f"{value:+.4f}pp"
    return f"{value:+,.2f}"


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
            rev_a = float(np.prod([factor_tbl.loc[f, "actual"]
                                   for f in REVENUE_FACTORS]))
            rev_e = float(np.prod([factor_tbl.loc[f, "expected"]
                                   for f in REVENUE_FACTORS]))
            direction = "below" if rev_a < rev_e else "above"
            print(f"\nREVENUE {rev_a:,.0f} vs expected {rev_e:,.0f} "
                  f"({abs(rev_a/rev_e - 1):.1%} {direction})")
            offsetting = (factor_tbl["contribution"] < 0).any()
            for f in REVENUE_FACTORS:
                r = factor_tbl.loc[f]
                print(f"  {f:16s} {r['pct_change']:+8.1%}  "
                      f"share of move {_fmt_share(r['contribution'])} "
                      f"({r['abs_contribution']:+,.0f})")
            if offsetting:
                print("  note: factors moved in OPPOSITE directions, so shares "
                      "exceed 100% and are partly cancelled by the 'opp' rows")
        # Rank WITHIN each dimension, never pooled across dimensions.
        # Each dimension independently partitions the whole population, so its
        # contributions sum to ~100% on their own. Pooling channel + device +
        # country into one leaderboard double-counts: "desktop", "United
        # States" and "Referral" are largely the SAME transactions viewed three
        # ways, and a pooled list summed to ~287% on 2017-04-27.
        sup = [e for e in led.supported() if e.kind in ("segment", "mix")]
        print(f"\nEXPLANATIONS BY DIMENSION  ({len(led.entries)} tests run)")
        if not sup:
            print("  none reached the materiality floor")
        for metric in sorted({e.metric for e in sup}):
            mrow = fired[fired["metric"] == metric]
            move = (f"{mrow['pct_dev'].iloc[0]:+.1%}" if not mrow.empty else "")
            word = ("decline" if not mrow.empty and mrow["pct_dev"].iloc[0] < 0
                    else "increase")
            print(f"\n  {metric} {move}  (shares below are of this {word})")
            for dim in DIMENSIONS:
                rows = [e for e in sup if e.metric == metric and e.dimension == dim]
                if not rows:
                    continue
                rows.sort(key=lambda x: -abs(x.contribution or 0))
                print(f"    by {dim}:")
                for e in rows[:4]:
                    name = e.segment if e.segment else "(traffic mix)"
                    q = "n/a" if e.q_value is None else f"{e.q_value:.4f}"
                    print(f"      {name:22s} {_fmt_share(e.contribution)} "
                          f"{_fmt_abs(e.abs_contribution, metric):>12s}  "
                          f"q={q:>8s}  {e.verdict}")

        # When the top segment of EVERY dimension is material, the movement is
        # concentrated in one intersection rather than caused by three separate
        # things. When dimensions disagree, that is informative too.
        tops = {}
        for metric in {e.metric for e in sup}:
            for dim in DIMENSIONS:
                rows = [e for e in sup if e.metric == metric
                        and e.dimension == dim and e.segment]
                if rows:
                    top = max(rows, key=lambda x: abs(x.contribution or 0))
                    tops.setdefault(metric, []).append((dim, top))
        for metric, pairs in tops.items():
            if len(pairs) == len(DIMENSIONS) and all(
                    abs(t.contribution or 0) >= 0.40 for _, t in pairs):
                where = " + ".join(f"{t.segment}" for _, t in pairs)
                print(f"\n  CONCENTRATED: {metric} movement sits in "
                      f"{where} (dominant in every dimension)")
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
