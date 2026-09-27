"""Hypothesis generation, gated by segment eligibility.

A hypothesis is only generated if the data can actually TEST it. Emitting a
conversion hypothesis for a segment with 2 transactions a day produces a
p-value that means nothing, and an LLM downstream will happily narrate it as a
finding. Gating at generation time is the cheapest place to prevent that.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from detection.config import EXCLUDE_DATES, SEGMENT_GATES
from metrics.dependency_graph import DIMENSIONS, REVENUE_FACTORS
from metrics.segments import eligibility, segment_series

MIN_ELIGIBLE_FRACTION = 0.50   # segment must clear the volume gate on >=50%
                               # of the eligibility window
ELIGIBILITY_WINDOW_DAYS = 84   # trailing window an `asof` date is judged
                               # against (see eligible_segments)


@dataclass
class Hypothesis:
    """A testable claim. `kind` decides which validator handles it."""
    hid: str
    kind: str                    # factor | segment
    metric: str
    dimension: str | None = None
    segment: str | None = None
    rationale: str = ""
    meta: dict = field(default_factory=dict)

    def __str__(self) -> str:
        loc = f" [{self.dimension}={self.segment}]" if self.segment else ""
        return f"{self.hid:6s} {self.kind:14s} {self.metric}{loc}"


def eligible_segments(dimension: str, metric: str, con=None, *,
                      asof: pd.Timestamp | str,
                      window_days: int = ELIGIBILITY_WINDOW_DAYS) -> list[str]:
    """Segments where `metric` can be meaningfully tested.

    Ratio metrics need transaction volume; session counts only need sessions.

    `asof` is REQUIRED, keyword-only, and has no full-history fallback: it
    restricts the eligibility calculation to the trailing `window_days` days
    strictly BEFORE that date (excluding `asof` itself and any
    EXCLUDE_DATES), so an incident on date T is judged only on segment volume
    T could actually have observed. There is deliberately no way to call this
    scanning the full history - that was a temporal leak (a segment's future
    volume cannot inform whether it was testable on a past date), found and
    removed; see notes/baseline.md, "the fix above leaked future data".
    """
    df = eligibility(segment_series(dimension, con))
    asof = pd.Timestamp(asof)
    start = asof - pd.Timedelta(days=window_days)
    df = df[(df["date"] >= start) & (df["date"] < asof)]
    excl = pd.to_datetime(sorted(EXCLUDE_DATES))
    df = df[~df["date"].isin(excl)]
    col = "ok_ratios" if metric in ("conversion_rate", "aov") else "ok_sessions"
    frac = df.groupby("segment")[col].mean()
    return sorted(frac[frac >= MIN_ELIGIBLE_FRACTION].index.tolist())


def generate(anomalous_metrics: list[str], date: pd.Timestamp,
             con=None) -> list[Hypothesis]:
    """Enumerate candidate explanations for the metrics that fired.

    Mechanical, not clever. Completeness matters more than ranking here -
    ranking is the validator's job once evidence exists.
    """
    hyps: list[Hypothesis] = []
    n = 0

    def nid() -> str:
        nonlocal n
        n += 1
        return f"H{n:03d}"

    # 1. Which multiplicative factor moved? Always testable at the top level.
    for m in REVENUE_FACTORS:
        hyps.append(Hypothesis(
            nid(), "factor", m,
            rationale=f"{m} deviated from expectation and drove revenue"))

    # 2. Is the movement localised to a segment? Only where testable.
    for m in anomalous_metrics:
        for dim in DIMENSIONS:
            for seg in eligible_segments(dim, m, con, asof=date):
                hyps.append(Hypothesis(
                    nid(), "segment", m, dim, seg,
                    rationale=f"{m} movement localised to {dim}={seg}"))

    # No seasonal hypothesis: that module was cut (notes/baseline.md, "Scope
    # decision"). No concentration hypothesis: single-order AOV days are
    # reclassified at detection time (detection/anomaly.py), not tested here.
    return hyps


if __name__ == "__main__":
    import duckdb
    con = duckdb.connect(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "signaltrace.duckdb"))

    demo_date = pd.Timestamp("2016-12-26")
    print(f"=== ratio-eligible segments as of {demo_date.date()} "
          f"(trailing {ELIGIBILITY_WINDOW_DAYS}d) ===")
    for dim in DIMENSIONS:
        print(f"{dim:10s} conversion/aov : "
              f"{eligible_segments(dim, 'conversion_rate', con, asof=demo_date)}")
        print(f"{dim:10s} sessions       : "
              f"{eligible_segments(dim, 'sessions', con, asof=demo_date)}")

    print("\n=== example: sessions + conversion_rate both fired ===")
    hyps = generate(["sessions", "conversion_rate"], demo_date, con)
    for h in hyps:
        print(" ", h)
    print(f"\ntotal hypotheses: {len(hyps)}")
    from collections import Counter
    print("by kind:", dict(Counter(h.kind for h in hyps)))
    con.close()
