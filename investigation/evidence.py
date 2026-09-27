"""Append-only evidence ledger.

Purpose: every claim in a report must trace to a recorded test. In Phase 2 this
becomes the constraint on the LLM - it may reference ledger entries and nothing
else, and any claim without an evidence_id is counted as an unsupported claim.

Two properties matter:

  NEGATIVE EVIDENCE IS RECORDED. Tests that came back immaterial or unsupported
  are kept, not discarded. "Conversion was checked and did not move" is a real
  finding, and without it the LLM cannot distinguish "not the cause" from
  "never looked at".

  BASIS IS EXPLICIT. An entry backed by an arithmetic identity is a different
  kind of claim from one backed by a p-value. The `basis` field keeps them
  distinguishable so a report can say "by decomposition" vs "by test".
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd


@dataclass
class EvidenceEntry:
    evidence_id: str
    incident_id: str
    date: str
    metric: str
    kind: str                    # segment | mix | factor | concentration
    dimension: str | None
    segment: str | None
    actual: float | None
    expected: float | None
    pct_change: float | None
    contribution: float | None
    abs_contribution: float | None
    p_value: float | None
    q_value: float | None
    verdict: str
    basis: str                   # identity | statistical_test | threshold |
                                  # decomposition_only | ineligible_segment
    detail: str = ""
    recorded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Ledger:
    """One ledger per investigation. Append-only by construction."""

    BASIS = {
        "mix": "identity",
        "factor": "identity",
        "segment": "statistical_test",
        "concentration": "threshold",
    }

    def __init__(self, incident_id: str, date):
        self.incident_id = incident_id
        self.date = str(pd.Timestamp(date).date())
        self._entries: list[EvidenceEntry] = []

    def _next_id(self) -> str:
        return f"E{len(self._entries) + 1:03d}"

    def record(self, result, *, metric: str = None) -> EvidenceEntry:
        """Record one validator Result. Nothing is filtered out here."""
        def num(x):
            return None if x is None or pd.isna(x) else float(x)

        e = EvidenceEntry(
            evidence_id=self._next_id(),
            incident_id=self.incident_id,
            date=self.date,
            metric=metric or result.metric,
            kind=result.kind,
            dimension=result.dimension,
            segment=result.segment,
            actual=num(result.actual),
            expected=num(result.expected),
            pct_change=num(result.pct_change),
            contribution=num(result.contribution),
            abs_contribution=num(result.abs_contribution),
            p_value=num(result.p_value),
            q_value=num(result.q_value),
            verdict=result.verdict,
            # Two distinct reasons a segment can go untested, kept separate in
            # `basis` even though they share the material_untested verdict:
            # `ineligible_segment` = a valid test exists but this segment's
            # volume fails the gate; `decomposition_only` = no valid test
            # exists for this hypothesis at all (e.g. AOV is a mean).
            basis=("ineligible_segment" if not getattr(result, "eligible", True)
                   else "decomposition_only" if not getattr(result, "testable", True)
                   else self.BASIS.get(result.kind, "statistical_test")),
            detail=getattr(result, "detail", ""),
        )
        self._entries.append(e)
        return e

    def record_many(self, results, *, metric: str = None) -> None:
        for r in results:
            self.record(r, metric=metric)

    @property
    def entries(self) -> list[EvidenceEntry]:
        return list(self._entries)

    def supported(self) -> list[EvidenceEntry]:
        return [e for e in self._entries
                if e.verdict in ("strong", "plausible",
                                 "material_but_uncertain", "arithmetic",
                                 "material_untested")]

    def negative(self) -> list[EvidenceEntry]:
        """Tests that ran and did NOT support a cause. Kept deliberately."""
        return [e for e in self._entries
                if e.verdict in ("immaterial", "unsupported")]

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(e) for e in self._entries])

    def to_json(self, path: str | None = None) -> str:
        payload = {
            "incident_id": self.incident_id,
            "date": self.date,
            "n_tests": len(self._entries),
            "n_supported": len(self.supported()),
            "n_negative": len(self.negative()),
            "entries": [asdict(e) for e in self._entries],
        }
        s = json.dumps(payload, indent=2, default=str)
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(s)
        return s

    def summary(self) -> str:
        from collections import Counter
        c = Counter(e.verdict for e in self._entries)
        return (f"{self.incident_id} ({self.date}): {len(self._entries)} tests, "
                + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from investigation.validator import validate_segments

    exp = pd.DataFrame({"sessions": [1500.0, 600.0, 300.0, 100.0]},
                       index=["Organic Search", "Direct", "Referral", "Social"])
    act = exp.copy()
    act.loc["Organic Search", "sessions"] = 900.0
    act.loc["Social", "sessions"] = 88.0

    led = Ledger("demo_001", "2017-03-15")
    led.record_many(validate_segments("sessions", "channel", act, exp))

    print(led.summary())
    print("\n--- supported ---")
    for e in led.supported():
        print(f"  {e.evidence_id} {e.metric}[{e.segment}] "
              f"contrib={e.contribution:+.3f} q={e.q_value} "
              f"basis={e.basis} -> {e.verdict}")
    print("\n--- negative (kept, not discarded) ---")
    for e in led.negative():
        print(f"  {e.evidence_id} {e.metric}[{e.segment}] "
              f"contrib={e.contribution:+.3f} -> {e.verdict}")
    print("\n--- json shape ---")
    print(led.to_json()[:420] + " ...")
