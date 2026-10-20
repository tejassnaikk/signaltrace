"""Agent tool layer for M6 (PLAN.md Section 4.1, amended 2026-10-07).

ToolSession is date-bound: constructed with (date, con), it runs the frozen
investigate() once at construction time and reveals cached slices through
three tools.  The agent never holds a Ledger, Result, DataFrame, or DB
connection - only the plain JSON a tool call returns.

Does not import harness-side packages.
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Any

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from investigation.evidence import Ledger
from investigation.orchestrator import investigate
from metrics.dependency_graph import DIMENSIONS, REVENUE_FACTORS
from agent.render import render_test_dimension, render_ledger_summary

ALLOWED_METRICS = list(REVENUE_FACTORS)
ALLOWED_DIMENSIONS = list(DIMENSIONS)


def _row_direction(actual, expected) -> str:
    """'down' if actual < expected, 'up' otherwise (or 'up' when either is None).

    Shared definition used by both test_dimension rows and the loop checker
    so the two cannot drift apart.
    """
    if actual is None or expected is None:
        return "up"
    return "down" if actual < expected else "up"


def _safe_float(x) -> float | None:
    """Return a Python float, or None for NaN/None/non-numeric."""
    if x is None:
        return None
    try:
        f = float(x)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _assert_serializable(obj: Any) -> None:
    """Fail fast if obj is not JSON-serializable."""
    json.dumps(obj)


class _EntryProxy:
    """Adapter from EvidenceEntry to Ledger.record()-compatible object.

    Reconstructs eligible/testable from basis so the harness Ledger writes
    the same basis as the engine Ledger did for each entry.
    """

    def __init__(self, entry) -> None:
        self.metric = entry.metric
        self.kind = entry.kind
        self.dimension = entry.dimension
        self.segment = entry.segment
        self.actual = entry.actual
        self.expected = entry.expected
        self.pct_change = entry.pct_change
        self.contribution = entry.contribution
        self.abs_contribution = entry.abs_contribution
        self.p_value = entry.p_value
        self.q_value = entry.q_value
        self.verdict = entry.verdict
        self.detail = entry.detail
        # Ledger.record() derives basis from these two flags; round-trip
        # faithfully so harness entries carry the same basis as engine entries.
        self.eligible = entry.basis != "ineligible_segment"
        self.testable = entry.basis != "decomposition_only"


class ToolSession:
    """Date-bound investigation session for the M6 agent tool layer.

    Construction runs the frozen investigate() once with all three revenue
    metrics forced and caches the result.  Tools reveal cached slices through
    three date-bound methods (no date argument).  A fresh harness Ledger
    records only what has been revealed; the agent never receives it.
    """

    def __init__(self, date, con, *, render_fn=None, render_ledger_fn=None) -> None:
        """
        Parameters
        ----------
        date : date-like
            The investigation date.  Fixed for the lifetime of this session.
        con : duckdb.DuckDBPyConnection
            Read-only real connection or harness-injected in-memory connection.
            The session never opens a DB itself.
        render_fn : callable | None
            Override for render_test_dimension.  None → v4 default.
        render_ledger_fn : callable | None
            Override for render_ledger_summary.  None → v4 default.
        """
        self._date = pd.Timestamp(date)
        self._render_fn = render_fn or render_test_dimension
        self._render_ledger_fn = render_ledger_fn or render_ledger_summary

        self._engine_result = investigate(
            self._date, con=con, verbose=False,
            force=list(REVENUE_FACTORS),
        )

        # Index engine ledger entries by (metric, dimension), order preserved.
        self._entries_by_key: dict[tuple[str, str], list] = {}
        for e in self._engine_result["ledger"].entries:
            if e.dimension is not None:
                key = (e.metric, e.dimension)
                self._entries_by_key.setdefault(key, []).append(e)

        # Fresh harness Ledger - records only what test_dimension has revealed.
        self._ledger = Ledger(
            self._engine_result["incident_id"],
            self._date,
        )

        # Cache of rows already returned by test_dimension, keyed by pair.
        self._revealed: dict[tuple[str, str], list[dict]] = {}

        self._call_counts: dict[str, int] = {
            "get_factor_decomposition": 0,
            "test_dimension": 0,
            "get_ledger_summary": 0,
        }

    # ------------------------------------------------------------------ #
    # Agent-facing tools (public, date-bound, JSON-serializable returns)  #
    # ------------------------------------------------------------------ #

    def get_factor_decomposition(self) -> list[dict]:
        """Revenue factor rows: metric, actual, expected, pct_change, contribution."""
        self._call_counts["get_factor_decomposition"] += 1
        factors = self._engine_result["factors"]
        if factors is None or factors.empty:
            rows: list[dict] = []
        else:
            rows = [
                {
                    "metric": str(m),
                    "actual": _safe_float(row["actual"]),
                    "expected": _safe_float(row["expected"]),
                    "pct_change": _safe_float(row["pct_change"]),
                    "contribution": _safe_float(row["contribution"]),
                }
                for m, row in factors.iterrows()
            ]
        _assert_serializable(rows)
        return rows

    def test_dimension(self, metric: str, dimension: str) -> dict:
        """Every segment and mix row for (metric, dimension).

        Returns a dict with two blocks:
            citable: list of claimable rows (short_id, segment, kind, direction,
                     contribution, evidence, basis, eligible, q)
            context: list of non-claimable rows (no short_id) plus optional
                     immaterial-count note

        Records into the harness Ledger on first call; repeat calls return
        the same rows and entry_ids without re-recording.  Returns an error
        dict for invalid metric or dimension (never raises).
        """
        self._call_counts["test_dimension"] += 1

        if metric not in ALLOWED_METRICS:
            return {
                "error": f"unknown metric {metric!r}",
                "allowed_metrics": ALLOWED_METRICS,
            }
        if dimension not in ALLOWED_DIMENSIONS:
            return {
                "error": f"unknown dimension {dimension!r}",
                "allowed_dimensions": ALLOWED_DIMENSIONS,
            }

        key = (metric, dimension)
        if key in self._revealed:
            # render again from raw cache — repeat calls are idempotent
            rendered = self._render_fn(self._revealed[key])
            _assert_serializable(rendered)
            return rendered

        engine_entries = self._entries_by_key.get(key, [])
        rows = []
        for e in engine_entries:
            harness_entry = self._ledger.record(_EntryProxy(e))
            rows.append(
                {
                    "segment": e.segment,
                    "kind": e.kind,
                    "direction": _row_direction(e.actual, e.expected),
                    "contribution": _safe_float(e.contribution),
                    "verdict": e.verdict,
                    "basis": e.basis,
                    "eligible": e.basis != "ineligible_segment",
                    "q_value": _safe_float(e.q_value),
                    "entry_id": harness_entry.evidence_id,
                }
            )

        self._revealed[key] = rows  # raw rows, for checker and repeat-call cache
        rendered = self._render_fn(rows)
        _assert_serializable(rendered)
        return rendered

    def get_ledger_summary(self) -> list[dict]:
        """Compact list of non-immaterial harness Ledger entries recorded so far."""
        self._call_counts["get_ledger_summary"] += 1
        result = self._render_ledger_fn(self._ledger.entries)
        _assert_serializable(result)
        return result

    # ------------------------------------------------------------------ #
    # Harness-side only — never exposed to the agent                      #
    # ------------------------------------------------------------------ #

    def _harness_ledger(self) -> Ledger:
        """The harness Ledger, for checker use only."""
        return self._ledger

    def _call_counts_snapshot(self) -> dict[str, int]:
        """Per-tool call counts (efficiency metric)."""
        return dict(self._call_counts)
