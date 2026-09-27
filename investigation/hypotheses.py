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
from detection.config import SEGMENT_GATES
from metrics.dependency_graph import DIMENSIONS, REVENUE_FACTORS
from metrics.segments import eligibility, segment_series

MIN_ELIGIBLE_FRACTION = 0.50  # segment must clear the gate on >=50% of history


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


def eligible_segments(dimension: str, metric: str, con=None) -> list[str]:
    """Segments where `metric` can be meaningfully tested.

    Ratio metrics need transaction volume; session counts only need sessions.
    """
    df = eligibility(segment_series(dimension, con))
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
            for seg in eligible_segments(dim, m, con):
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

    print("=== ratio-eligible segments (>=50% of days) ===")
    for dim in DIMENSIONS:
        print(f"{dim:10s} conversion/aov : "
              f"{eligible_segments(dim, 'conversion_rate', con)}")
        print(f"{dim:10s} sessions       : "
              f"{eligible_segments(dim, 'sessions', con)}")

    print("\n=== example: sessions + conversion_rate both fired ===")
    hyps = generate(["sessions", "conversion_rate"], pd.Timestamp("2016-12-26"), con)
    for h in hyps:
        print(" ", h)
    print(f"\ntotal hypotheses: {len(hyps)}")
    from collections import Counter
    print("by kind:", dict(Counter(h.kind for h in hyps)))
    con.close()
