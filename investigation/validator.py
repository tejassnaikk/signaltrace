"""Hypothesis validation: contribution-first, BH-adjusted significance second.

Two concepts kept deliberately separate:
    EFFECT      how much of the observed KPI change does this explain?
    EVIDENCE    how confident are we the segment change is not noise?

Ranking is by EFFECT. Significance qualifies the result; it does not order it.
A segment explaining 40% of a decline outranks one explaining 1%, however
significant the latter's local change is.

Verdicts (seven), checked in this order:
    immaterial               contribution < floor  (dropped before testing)
    arithmetic               mix term: exact identity, no null hypothesis
    material_untested        contribution >= floor, no valid test exists (AOV)
    strong                   contribution >= floor AND q < 0.05
    plausible                contribution >= floor AND q < 0.10
    material_but_uncertain   contribution >= high_contribution AND q >= 0.10
    unsupported              contribution >= floor, tested, not significant

An eighth label, explained_single_order, is NOT produced here. It is written
to the ledger by the orchestrator for AOV anomalies the detection-time
concentration check reclassified (detection/anomaly.py).
"""
from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from detection.config import VALIDATOR
from investigation.attribution import additive_contributions, ratio_decomposition


@dataclass
class Result:
    hid: str
    kind: str
    metric: str
    dimension: str | None
    segment: str | None
    actual: float
    expected: float
    pct_change: float
    contribution: float
    abs_contribution: float
    p_value: float
    q_value: float
    verdict: str
    detail: str = ""
    testable: bool = True   # False when no valid statistical test exists (AOV)
    eligible: bool = True   # False when segment volume fails the eligibility
                            # gate - a DIFFERENT reason for going untested than
                            # `testable`: here a valid test exists in principle,
                            # there just isn't enough data in THIS segment
    phi: float = 1.0        # dispersion used by _poisson_p for this row (1.0 =
                            # plain Poisson; >1.0 = negative-binomial-corrected,
                            # see investigation/dispersion.py). Always 1.0 for
                            # ratio metrics (conversion_rate/aov) - _prop_p is
                            # not implicated (PLAN.md Section 5.3).
    phi_tier: str = "poisson"  # "own" | "dimension" | "global" | "poisson"


def benjamini_hochberg(p: np.ndarray) -> np.ndarray:
    """Step-up FDR control. Returns q-values in the input order."""
    p = np.asarray(p, dtype=float)
    ok = ~np.isnan(p)
    q = np.full(p.shape, np.nan)
    if ok.sum() == 0:
        return q
    pv = p[ok]
    n = len(pv)
    order = np.argsort(pv)
    ranked = pv[order] * n / np.arange(1, n + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.clip(ranked, 0, 1)
    q[ok] = out
    return q


def _verdict(contribution: float, q: float, kind: str = "segment",
             testable: bool = True, eligible: bool = True) -> str:
    c = abs(contribution) if not pd.isna(contribution) else 0.0
    if c < VALIDATOR["materiality_floor"]:
        return "immaterial"
    # Mix is an exact algebraic identity, not a sampled estimate: the
    # decomposition residual is ~1e-19. There is no null hypothesis to test,
    # so it is never "uncertain" and must not be scored as if it were.
    if kind == "mix":
        return "arithmetic"
    # Untested for either of two DIFFERENT reasons, both landing on the same
    # verdict: no valid test exists for this hypothesis at all (`testable`,
    # see _MEAN_TEST_UNAVAILABLE), or a valid test exists but this segment's
    # own volume is below the eligibility gate (`eligible`). Either way, a
    # missing test is NOT weak evidence - it is the absence of evidence, and
    # must not be laundered into "material_but_uncertain". The ledger's
    # `basis` field keeps the two reasons distinguishable even though the
    # verdict does not.
    if not testable or not eligible:
        return "material_untested"
    if not pd.isna(q) and q < VALIDATOR["q_strong"]:
        return "strong"
    if not pd.isna(q) and q < VALIDATOR["q_plausible"]:
        return "plausible"
    if c >= VALIDATOR["high_contribution"]:
        return "material_but_uncertain"
    return "unsupported"


def _poisson_p(actual: float, expected: float, phi: float = 1.0) -> float:
    """Two-sided tail for count metrics (sessions, transactions).

    `phi` is the measured dispersion (Var(actual) ~= phi * expected;
    investigation/dispersion.py). phi=1.0 (the default) is the original pure
    Poisson test, kept exactly - callers that don't pass phi see no change.
    phi>1.0 uses a quasi-Poisson-parameterized Negative Binomial instead:
    same mean, inflated variance. This was measured to matter a great deal -
    real segment-level session counts are overdispersed by 3x to 475x
    depending on the segment (notes/PLAN.md Section 5.3), and the plain
    Poisson test above was producing a 70.4% false-strong rate on pure noise
    (C1) before this was added.
    """
    if pd.isna(actual) or pd.isna(expected) or expected <= 0:
        return np.nan
    k, lam = int(round(actual)), float(expected)
    if phi <= 1.0000001:
        p = (stats.poisson.cdf(k, lam) if k < lam
             else stats.poisson.sf(k - 1, lam))
        return float(min(1.0, 2 * p))
    # NB1 (quasi-Poisson) parameterization: mean=lam, Var=phi*lam.
    # scipy's nbinom is parameterized by (n, p) with mean = n*(1-p)/p; solving
    # for that mean/variance target gives p=1/phi, n=lam*p/(1-p).
    p_param = 1.0 / phi
    n_param = lam * p_param / (1 - p_param)
    p = (stats.nbinom.cdf(k, n_param, p_param) if k < lam
         else stats.nbinom.sf(k - 1, n_param, p_param))
    return float(min(1.0, 2 * p))


# AOV is revenue/transactions - a MEAN of order values, not a proportion.
# Applying a two-proportion z-test to it is invalid: the pooled estimate can
# exceed 1, pool*(1-pool) goes negative, and the sqrt returns NaN. That was
# silently producing NaN p-values that fell through to "material_but_uncertain".
#
# A correct test (Welch's t) needs the VARIANCE of order values within each
# segment. The daily export carries only SUM(revenue) and SUM(transactions) per
# cell, so that variance does not exist in our data. Rather than fabricate a
# test, AOV segment hypotheses are reported as decomposition only.
_MEAN_TEST_UNAVAILABLE = ("AOV is a mean; a valid test needs within-segment "
                          "order variance, which the daily export lacks")


def _prop_p(num_a: float, den_a: float, num_e: float, den_e: float) -> float:
    """Two-proportion z-test. VALID ONLY for conversion_rate (a true
    proportion: transactions per session, both bounded and countable)."""
    if min(den_a, den_e) <= 0 or pd.isna(num_a) or pd.isna(num_e):
        return np.nan
    p1, p2 = num_a / den_a, num_e / den_e
    pool = (num_a + num_e) / (den_a + den_e)
    if not (0 < pool < 1):        # not a proportion -> test does not apply
        return np.nan
    se = np.sqrt(pool * (1 - pool) * (1 / den_a + 1 / den_e))
    if se == 0 or np.isnan(se):
        return np.nan
    return float(2 * stats.norm.sf(abs(p1 - p2) / se))


def validate_segments(metric: str, dimension: str,
                      act: pd.DataFrame, exp: pd.DataFrame,
                      hid_base: str = "H",
                      eligible: set[str] | None = None,
                      phi_lookup: dict[str, tuple[float, str]] | None = None
                      ) -> list[Result]:
    """Test every segment of one dimension for one metric.

    act/exp are indexed by segment with columns sessions, transactions, revenue.

    `eligible`, if given, marks which segments have enough volume to be
    SIGNIFICANCE-TESTED (PLAN.md Section 2 / notes/baseline.md segment
    viability). An ineligible segment's contribution is still computed and
    kept - its share of the parent delta is real, and dropping it would make
    the eligible segments' own reported shares look more complete than they
    are - but it is excluded from the BH family and never receives a
    q-value; its verdict is material_untested (contribution >= floor) or
    immaterial (below floor), the same two outcomes available to a
    hypothesis with no valid test at all (`testable=False`), for the same
    reason: a missing test must not be scored as if it were weak evidence.
    Mix/within/interaction are unaffected either way - computed from the FULL
    act/exp partition regardless, because the mix identity
    (within_total + mix_total + interaction_total = blend_delta) only holds
    across the whole partition. NOTE: per-segment shares summing to ~100% is
    an exact identity for the additive metrics (sessions, transactions) but
    NOT generally true for the ratio metrics here - a ratio segment's
    `contribution` is `within_share` alone, excluding both mix and the
    never-surfaced interaction term, so the visible shares can be far from
    ~100% on an ordinary day where blend_delta is small (found and corrected
    2026-09-27, PLAN.md Section 2).

    `phi_lookup`, if given, maps segment -> (phi, tier) for `_poisson_p`'s
    dispersion correction (investigation/dispersion.py). Only used for
    additive metrics (sessions, transactions) - ratio metrics use `_prop_p`,
    which is not implicated (PLAN.md Section 5.3) and always gets phi=1.0.
    """
    results: list[Result] = []
    is_ratio = metric in ("conversion_rate", "aov")

    if is_ratio:
        num, den = (("transactions", "sessions") if metric == "conversion_rate"
                    else ("revenue", "transactions"))
        d = ratio_decomposition(act[num], act[den], exp[num], exp[den])
        ps = d["per_segment"]
        # The mix term is its own explanation, not attributable to a segment.
        results.append(Result(
            f"{hid_base}-MIX", "mix", metric, dimension, None,
            d["blend_actual"], d["blend_expected"],
            (d["blend_actual"] / d["blend_expected"] - 1
             if d["blend_expected"] else np.nan),
            (d["mix_total"] / d["blend_delta"]
             if abs(d["blend_delta"]) > 1e-12 else np.nan),
            d["mix_total"], np.nan, np.nan, "pending",
            detail=(f"traffic mix shift across {dimension}; "
                    f"within={d['within_total']:+.5f} mix={d['mix_total']:+.5f}")))
        for seg in ps.index:
            r = ps.loc[seg]
            results.append(Result(
                f"{hid_base}-{seg}", "segment", metric, dimension, str(seg),
                r["rate_actual"], r["rate_expected"],
                (r["rate_actual"] / r["rate_expected"] - 1
                 if r["rate_expected"] else np.nan),
                r["within_share"], r["within_effect"],
                (_prop_p(act.loc[seg, num], act.loc[seg, den],
                         exp.loc[seg, num], exp.loc[seg, den])
                 if metric == "conversion_rate" else np.nan),
                np.nan, "pending",
                detail=("within-segment rate change (mix effect excluded)"
                        if metric == "conversion_rate"
                        else f"within-segment AOV change; {_MEAN_TEST_UNAVAILABLE}"),
                testable=(metric == "conversion_rate")))
    else:
        c = additive_contributions(act[metric], exp[metric])
        for seg in c.index:
            r = c.loc[seg]
            phi, tier = (phi_lookup.get(str(seg), (1.0, "poisson"))
                        if phi_lookup else (1.0, "poisson"))
            result = Result(
                f"{hid_base}-{seg}", "segment", metric, dimension, str(seg),
                r["actual"], r["expected"],
                (r["actual"] / r["expected"] - 1 if r["expected"] else np.nan),
                r["contribution"], r["abs_contribution"],
                _poisson_p(r["actual"], r["expected"], phi), np.nan, "pending",
                detail=f"dispersion-corrected count test; phi={phi:.1f} tier={tier}")
            result.phi, result.phi_tier = phi, tier
            results.append(result)

    # Mark ineligible segments (kept, not dropped: their contribution stays
    # visible rather than silently making the ELIGIBLE segments' own shares
    # look more complete than they are). NOT "so shares sum to ~100%" - that
    # only holds exactly for additive metrics; for ratio metrics the visible
    # shares (within + mix) omit interaction and can be far from ~100% on an
    # ordinary noisy day, regardless of eligibility marking (PLAN.md Section 2).
    if eligible is not None:
        for r in results:
            if r.kind == "segment" and r.segment not in eligible:
                r.eligible = False

    # Materiality gate BEFORE multiple-comparison correction: immaterial
    # hypotheses are not part of the tested family and must not inflate it.
    # Mix carries no p-value, so it is excluded from the BH family - including
    # it would shrink the family size and weaken every other correction.
    # Ineligible segments are excluded the same way: a segment too thin to
    # test validly must not enter the family either.
    material = [r for r in results
                if r.kind != "mix" and r.testable and r.eligible
                and not pd.isna(r.contribution)
                and abs(r.contribution) >= VALIDATOR["materiality_floor"]]
    if material:
        qs = benjamini_hochberg(np.array([r.p_value for r in material]))
        for r, q in zip(material, qs):
            r.q_value = float(q) if not np.isnan(q) else np.nan
    for r in results:
        r.verdict = _verdict(r.contribution, r.q_value, r.kind, r.testable, r.eligible)

    return sorted(results,
                  key=lambda r: -abs(r.contribution if not pd.isna(r.contribution) else 0))


def to_frame(results: list[Result]) -> pd.DataFrame:
    return pd.DataFrame([asdict(r) for r in results])


if __name__ == "__main__":
    # Case 1: one segment genuinely collapses.
    exp = pd.DataFrame({"sessions": [1500.0, 600.0, 300.0, 100.0]},
                       index=["Organic Search", "Direct", "Referral", "Social"])
    act = exp.copy()
    act.loc["Organic Search", "sessions"] = 900.0   # -40%
    act.loc["Social", "sessions"] = 88.0            # small, noisy

    print("=== sessions: Organic Search collapses ===")
    r = validate_segments("sessions", "channel", act, exp)
    print(to_frame(r)[["segment", "pct_change", "contribution",
                       "p_value", "q_value", "verdict"]].round(4).to_string(index=False))

    # Case 2: pure mix shift, no segment rate changes.
    exp2 = pd.DataFrame({"transactions": [100.0, 10.0], "sessions": [5000.0, 2000.0]},
                        index=["desktop", "mobile"])
    act2 = pd.DataFrame({"transactions": [80.0, 15.0], "sessions": [4000.0, 3000.0]},
                        index=["desktop", "mobile"])
    print("\n=== conversion_rate: pure mix shift toward mobile ===")
    r2 = validate_segments("conversion_rate", "device", act2, exp2)
    print(to_frame(r2)[["kind", "segment", "contribution", "q_value",
                        "verdict", "detail"]].round(4).to_string(index=False))
