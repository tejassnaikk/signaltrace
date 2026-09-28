"""Per-segment overdispersion measurement for `_poisson_p` (validator.py).

Session counts are far more overdispersed than a pure Poisson model assumes
(measured, PLAN.md Section 5.3: pooled Var/mean ratio ~85x, not 1x - this is
why C1's sessions false-strong rate was 70.4%). But that ratio varies hugely
by segment (measured 3.4x to 475x across real segments) - a single pooled
constant would be wrong for most segments, so dispersion (`phi`) is measured
PER SEGMENT, from a trailing window strictly before the test date (`asof`),
never a fixed historical window - the same leak-free discipline already
established for eligibility (investigation/hypotheses.eligible_segments;
notes/baseline.md, "the fix above leaked future data").

**Hard tier cutoff, OWN_MIN_N=84 (revised 2026-09-27, twice).** First attempt
used a hard cutoff at n>=120: simple, but a segment with n_own just under
120 (e.g. 96-106) got ZERO weight on its own history and 100% weight on the
dimension pool, even though its own estimate (phi~20-40) was already far
better than that pool's (phi~150-200, dominated by noisier channels like
Social/Direct). This overcorrected two genuinely-strong I7 dates to
q=0.66-0.86.

Second attempt replaced the cutoff with empirical-Bayes shrinkage
(phi = (n_own*phi_own + k*phi_base)/(n_own+k), k=120). This helped the two
overcorrected dates (q down to 0.53-0.83) but NOT enough to flip their
verdict, and it introduced NEW regressions on dates that were already fine
under the n>=120 hard cutoff (I1's 2017-03-16, I7's 2017-06-28 both flipped
from strong to failing) - k=120 pulls even a solid n_own~150-250 estimate
substantially toward the much-larger pooled prior, which is a worse
trade for THIS dataset's magnitudes than simply trusting a somewhat noisier
own estimate once one exists.

**What shipped: lowering the hard cutoff to n_own>=84** - reusing the SAME
12-week window already established elsewhere in this codebase
(COMPONENTS["sessions"]["weeks"], the eligibility window) rather than a
separately-tuned number. Bootstrap-measured reliability at n=84 is worse
than at n=120 (median CV 18.2% vs 13.4%, p90 38.3% vs 28.4%) - genuinely
noisier - but a noisy OWN estimate beats a confidently-wrong POOLED one
dominated by unrelated, far-more-volatile segments.

**This is NOT a win on every axis - be precise about what was actually
compared.** I1 and I7 were used to DISCOVER the n>=120 defect and were also
the data used to CHOOSE n=84 over the alternatives - they are selection
data, not an independent test, and their pass counts (5/5, 5/6) should be
read as "consistent with the fix working," not as proof of it. **C1 is the
only independent check here** (it was never used to pick which cutoff to
use), and on C1, n>=84 is not the best-calibrated option:

    n>=120 hard cutoff   3/54 (5.6%)
    shrinkage k=120      0/54 (0.0%)
    n>=84 hard cutoff    4/54 (7.4%)   <- shipped

n>=84 has the WORST false-strong rate of the three on C1's material-tested
sessions pairs, not the best. At n=54, 90%-CI binomial ranges for these three
counts are [1.5%,13.7%], [0.0%,5.4%], [2.6%,16.2%] respectively - heavily
overlapping, so this three-way difference is within sampling noise at this
sample size and should not be read as "n=84 recalibrates worse," only as
"C1 alone cannot distinguish these three designs." The real basis for
choosing n=84 is the mechanism-level argument above (an own estimate that
exists, even noisy, is more informative than a pooled one built almost
entirely from different, far more volatile segments), not the C1 count.

Three-tier fallback:
    own segment    n >= OWN_MIN_N (84)  - bootstrap median CV 18.2%, p90 38.3%
    dimension pool n >= DIM_MIN_N (300) - bootstrap median CV 18.2%, p75 21.9%
    global pool    whatever's available - always sufficient in practice by
                   the time any of this project's benchmark dates occur
    (poisson)      fallback of last resort if even the global pool is thin -
                   phi=1.0, i.e. the original, uncorrected test
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from detection.config import COMPONENTS
from metrics.dependency_graph import DIMENSIONS
from metrics.segments import segment_expectation, segment_series

OWN_MIN_N = 84      # matches COMPONENTS["sessions"]["weeks"] * 7 - see module docstring
DIM_MIN_N = 300
SESSIONS_WEEKS = COMPONENTS["sessions"]["weeks"]


def _std_resid_history(piv: pd.DataFrame, exp: pd.DataFrame,
                       asof: pd.Timestamp, excluded: set) -> pd.DataFrame:
    """(actual-expected)/sqrt(expected) per segment, every date with a valid
    expectation, STRICTLY before asof, excluding known real-incident days
    (genuine anomalies, not normal noise - leaving them in would inflate the
    dispersion estimate with the exact variance we're trying to measure
    around, not through)."""
    mask = (piv.index < asof) & (~piv.index.isin(excluded))
    a, e = piv.loc[mask], exp.loc[mask]
    ok = e.notna() & (e > 0) & a.notna()
    return ((a - e) / np.sqrt(e)).where(ok)


def measure_phi_map(dimension: str, segments, asof, con,
                    excluded: set | None = None) -> dict[str, tuple[float, str]]:
    """{segment: (phi, tier)} for every segment in `segments`. `tier` is
    "own" | "dimension" | "global" | "poisson", logged into the ledger's
    detail field so it's visible which segments leaned on their own history
    vs. a pooled fallback.

    Dispersion is measured on SESSIONS' own residuals (not transactions')
    even when scoring a `transactions` hypothesis - both are counts sharing
    the same `_poisson_p` test and the same underlying noise source
    (sessions arrivals), and sessions has the longer, more complete history.
    """
    asof = pd.Timestamp(asof)
    excluded = excluded or set()

    seg_df = segment_series(dimension, con)
    piv, exp = segment_expectation(seg_df, "sessions", SESSIONS_WEEKS)
    resid = _std_resid_history(piv, exp, asof, excluded)

    dim_vals = resid.to_numpy().ravel()
    dim_vals = dim_vals[~np.isnan(dim_vals)]

    global_parts = [dim_vals]
    for other_dim in DIMENSIONS:
        if other_dim == dimension:
            continue
        other_seg_df = segment_series(other_dim, con)
        other_piv, other_exp = segment_expectation(other_seg_df, "sessions", SESSIONS_WEEKS)
        other_resid = _std_resid_history(other_piv, other_exp, asof, excluded)
        v = other_resid.to_numpy().ravel()
        global_parts.append(v[~np.isnan(v)])
    global_vals = np.concatenate(global_parts)

    dim_phi = max(float(np.var(dim_vals)), 1.0) if len(dim_vals) >= 2 else 1.0
    global_phi = max(float(np.var(global_vals)), 1.0) if len(global_vals) >= 2 else 1.0

    out: dict[str, tuple[float, str]] = {}
    for seg in segments:
        own_vals = (resid[seg].dropna().to_numpy()
                   if seg in resid.columns else np.array([]))
        if len(own_vals) >= OWN_MIN_N:
            out[seg] = (max(float(np.var(own_vals)), 1.0), "own")
        elif len(dim_vals) >= DIM_MIN_N:
            out[seg] = (dim_phi, "dimension")
        elif len(global_vals) >= DIM_MIN_N:
            out[seg] = (global_phi, "global")
        else:
            out[seg] = (1.0, "poisson")
    return out
