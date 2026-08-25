"""Contribution arithmetic. Two regimes, because the metrics differ in kind.

ADDITIVE (sessions, transactions)
    Segment shares sum to 1 by construction. Contribution is a plain share of
    the parent delta.

RATIO (conversion_rate, aov)
    The blended value is sum(numerator)/sum(denominator), NOT the average of
    segment ratios. A segment affects the blend two ways: its own rate moves,
    or its WEIGHT moves. Standard within/between decomposition:

        d_blend = SUM_s [ w_exp * (rate_act - rate_exp) ]   within
                + SUM_s [ rate_exp * (w_act - w_exp) ]      mix
                + SUM_s [ (rate_act-rate_exp)*(w_act-w_exp) ] interaction

    Without the mix term, a pure traffic mix shift toward a low-converting
    segment gets misattributed to whichever segment's RATE looks different.
    That is the mobile Simpson's paradox case in notes/baseline.md: mobile is
    24% of traffic and 3% of revenue, so a shift toward mobile depresses
    blended conversion with NO per-segment rate change at all.

MULTIPLICATIVE (revenue = sessions * cr * aov)
    Logarithmic decomposition, so factor contributions sum exactly to the total
    log change with no leftover interaction term.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EPS = 1e-12


def additive_contributions(actual: pd.Series, expected: pd.Series) -> pd.DataFrame:
    """Segment share of the parent delta. Index = segment."""
    delta = actual - expected
    total = delta.sum()
    return pd.DataFrame({
        "actual": actual, "expected": expected, "delta": delta,
        "contribution": delta / total if abs(total) > EPS else np.nan,
        "abs_contribution": delta,
    })


def ratio_decomposition(num_act: pd.Series, den_act: pd.Series,
                        num_exp: pd.Series, den_exp: pd.Series) -> dict:
    """Within / mix / interaction split of a blended ratio change.

    Returns per-segment effects plus the three aggregate terms. All effects are
    in RATIO UNITS (e.g. conversion points), so they sum to the blend delta.
    """
    rate_act = num_act / den_act.replace(0, np.nan)
    rate_exp = num_exp / den_exp.replace(0, np.nan)
    w_act = den_act / den_act.sum() if den_act.sum() else den_act * np.nan
    w_exp = den_exp / den_exp.sum() if den_exp.sum() else den_exp * np.nan

    blend_act = num_act.sum() / den_act.sum() if den_act.sum() else np.nan
    blend_exp = num_exp.sum() / den_exp.sum() if den_exp.sum() else np.nan
    blend_delta = blend_act - blend_exp

    within = (w_exp * (rate_act - rate_exp)).fillna(0)
    mix = (rate_exp * (w_act - w_exp)).fillna(0)
    inter = ((rate_act - rate_exp) * (w_act - w_exp)).fillna(0)

    per_segment = pd.DataFrame({
        "rate_expected": rate_exp, "rate_actual": rate_act,
        "weight_expected": w_exp, "weight_actual": w_act,
        "within_effect": within, "mix_effect": mix,
        "interaction_effect": inter,
        "total_effect": within + mix + inter,
    })
    if abs(blend_delta) > EPS:
        per_segment["contribution"] = per_segment["total_effect"] / blend_delta
        per_segment["within_share"] = within / blend_delta
        per_segment["mix_share"] = mix / blend_delta
    else:
        per_segment[["contribution", "within_share", "mix_share"]] = np.nan

    return {
        "blend_expected": blend_exp, "blend_actual": blend_act,
        "blend_delta": blend_delta,
        "within_total": within.sum(), "mix_total": mix.sum(),
        "interaction_total": inter.sum(),
        "mix_dominant": (abs(mix.sum()) > abs(within.sum())
                         if abs(blend_delta) > EPS else False),
        "per_segment": per_segment,
        "residual_check": blend_delta - (within.sum() + mix.sum() + inter.sum()),
    }


def multiplicative_contributions(actual: dict, expected: dict) -> pd.DataFrame:
    """Log decomposition of revenue = sessions * cr * aov.

    Factor shares sum to 1 exactly (no interaction remainder), which is why log
    space is used rather than the first-order product expansion.
    """
    rows = {}
    for k in actual:
        a, e = actual[k], expected[k]
        rows[k] = (np.log(a / e) if (a and e and a > 0 and e > 0) else np.nan)
    log_eff = pd.Series(rows)
    total_log = log_eff.sum()
    rev_act = np.prod([actual[k] for k in actual])
    rev_exp = np.prod([expected[k] for k in expected])
    return pd.DataFrame({
        "actual": pd.Series(actual), "expected": pd.Series(expected),
        "pct_change": pd.Series({k: actual[k] / expected[k] - 1 for k in actual}),
        "log_effect": log_eff,
        "contribution": log_eff / total_log if abs(total_log) > EPS else np.nan,
        "abs_contribution": (log_eff / total_log) * (rev_act - rev_exp)
                            if abs(total_log) > EPS else np.nan,
    })


if __name__ == "__main__":
    # Simpson's paradox check: NO segment changes its rate, only the mix moves.
    num_exp = pd.Series({"desktop": 100.0, "mobile": 10.0})
    den_exp = pd.Series({"desktop": 5000.0, "mobile": 2000.0})
    num_act = pd.Series({"desktop": 80.0, "mobile": 15.0})   # rates identical
    den_act = pd.Series({"desktop": 4000.0, "mobile": 3000.0})

    r = ratio_decomposition(num_act, den_act, num_exp, den_exp)
    print("=== pure mix shift (segment rates UNCHANGED) ===")
    print(f"blended expected : {r['blend_expected']:.5f}")
    print(f"blended actual   : {r['blend_actual']:.5f}")
    print(f"blend delta      : {r['blend_delta']:+.5f}")
    print(f"within total     : {r['within_total']:+.5f}   <- must be ~0")
    print(f"mix total        : {r['mix_total']:+.5f}   <- carries all of it")
    print(f"mix dominant     : {r['mix_dominant']}")
    print(f"residual check   : {r['residual_check']:+.2e}")

    print("\n=== revenue factor decomposition ===")
    m = multiplicative_contributions(
        {"sessions": 2000.0, "conversion_rate": 0.010, "aov": 100.0},
        {"sessions": 2500.0, "conversion_rate": 0.013, "aov": 105.0})
    print(m.round(4).to_string())
    print(f"contributions sum: {m['contribution'].sum():.6f}  (must be 1.0)")
