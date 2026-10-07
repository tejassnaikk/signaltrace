"""pytest suite for agent/tools.py (M6 task 2).

All tests run on the 41 dev dates via build_incident_con.
The test file may import from evaluation/; agent/tools.py may not.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tools import ToolSession
from evaluation.arm_neutral import Answer, Claim, check_answer, engine_answer
from evaluation.injector import build_incident_con, load_ground_truth
from metrics.dependency_graph import DIMENSIONS, REVENUE_FACTORS

INCIDENTS = ["I1", "I2", "I3", "I3b", "I4", "I5", "I6", "I7", "C1"]
FORCED = list(REVENUE_FACTORS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_dev_cases() -> list[tuple[str, str]]:
    cases = []
    for iid in INCIDENTS:
        gt = load_ground_truth(iid)
        for date in gt["dates"]:
            cases.append((iid, date))
    return cases


DEV_CASES = _all_dev_cases()


def _floats_equal(a, b) -> bool:
    """None/NaN-aware float equality within 1e-9."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) < 1e-9


def _claim_signature(c: Claim) -> tuple:
    return (c.metric, c.dimension, c.segment, c.kind, c.direction, c.strength)


def _answers_equivalent(a1: Answer, a2: Answer) -> bool:
    """Structural equality ignoring evidence_ids (which differ between ledgers)."""
    if a1.pattern != a2.pattern:
        return False
    if sorted(_claim_signature(c) for c in a1.claims) != sorted(_claim_signature(c) for c in a2.claims):
        return False
    return [_claim_signature(c) for c in a1.primary] == [_claim_signature(c) for c in a2.primary]


# ---------------------------------------------------------------------------
# (a) Equivalence: test_dimension reproduces investigate() ledger, row for row
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("iid,date", DEV_CASES)
def test_a_equivalence(iid: str, date: str) -> None:
    """For all 9 (metric, dimension) pairs, test_dimension matches the engine."""
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    # Index engine ledger by (metric, dimension)
    by_key: dict[tuple, list] = {}
    for e in session._engine_result["ledger"].entries:
        if e.dimension is not None:
            by_key.setdefault((e.metric, e.dimension), []).append(e)

    for metric in REVENUE_FACTORS:
        for dimension in DIMENSIONS:
            rows = session.test_dimension(metric, dimension)
            assert isinstance(rows, list), \
                f"{iid} {date} ({metric},{dimension}): expected list"

            expected = by_key.get((metric, dimension), [])
            assert len(rows) == len(expected), (
                f"{iid} {date} ({metric},{dimension}): "
                f"got {len(rows)} rows, expected {len(expected)}"
            )

            for i, (row, e) in enumerate(zip(rows, expected)):
                ctx = f"{iid} {date} ({metric},{dimension}) row {i}"
                assert row["segment"] == e.segment, f"{ctx}: segment"
                assert row["kind"] == e.kind, f"{ctx}: kind"
                assert row["verdict"] == e.verdict, f"{ctx}: verdict"
                assert row["basis"] == e.basis, f"{ctx}: basis"
                assert row["eligible"] == (e.basis != "ineligible_segment"), \
                    f"{ctx}: eligible"
                assert _floats_equal(row["q_value"], e.q_value), \
                    f"{ctx}: q_value {row['q_value']!r} != {e.q_value!r}"
                assert _floats_equal(row["contribution"], e.contribution), \
                    f"{ctx}: contribution"
                assert "entry_id" in row, f"{ctx}: missing entry_id"

    con.close()


# ---------------------------------------------------------------------------
# (b) engine_answer from harness (all 9 calls) equals engine_answer directly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("iid,date", DEV_CASES)
def test_b_engine_answer_equivalence(iid: str, date: str) -> None:
    """After all 9 test_dimension calls, harness answer equals direct answer."""
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    for metric in REVENUE_FACTORS:
        for dimension in DIMENSIONS:
            session.test_dimension(metric, dimension)

    harness_answer = engine_answer({
        "ledger": session._harness_ledger(),
        "factors": session._engine_result["factors"],
    })
    direct_answer = engine_answer(session._engine_result)

    assert _answers_equivalent(harness_answer, direct_answer), (
        f"{iid} {date}: pattern harness={harness_answer.pattern!r} "
        f"direct={direct_answer.pattern!r}; "
        f"claims harness={sorted(_claim_signature(c) for c in harness_answer.claims)[:3]} "
        f"direct={sorted(_claim_signature(c) for c in direct_answer.claims)[:3]}"
    )

    con.close()


# ---------------------------------------------------------------------------
# (c) Ledger scoping: unrevealed entries not accepted by check_answer
# ---------------------------------------------------------------------------

def test_c_ledger_scoping() -> None:
    """Harness records only revealed rows; check_answer rejects a channel claim
    citing an engine entry that was never revealed to the harness."""
    iid, date = DEV_CASES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    session.test_dimension("conversion_rate", "device")
    harness = session._harness_ledger()

    # Only conversion_rate-device entries should be in the harness ledger.
    for e in harness.entries:
        assert e.metric == "conversion_rate", \
            f"unexpected metric {e.metric!r} in scoped harness"
        assert e.dimension == "device", \
            f"unexpected dimension {e.dimension!r} in scoped harness"

    # Get a conversion_rate-channel entry from the ENGINE (never revealed).
    engine_entries = session._engine_result["ledger"].entries
    channel_entry = next(
        e for e in engine_entries
        if e.metric == "conversion_rate" and e.dimension == "channel"
    )

    # Build a claim citing the engine entry's ID - that ID is NOT in the harness.
    fake_claim = Claim(
        metric="conversion_rate", dimension="channel",
        segment=channel_entry.segment, direction="down",
        kind="within" if channel_entry.kind == "segment" else channel_entry.kind,
        evidence_ids=[channel_entry.evidence_id],
        strength=channel_entry.verdict, basis=channel_entry.basis,
    )
    result = check_answer(
        Answer(pattern="localized", claims=[fake_claim], primary=[]),
        harness,
    )
    assert result["n_rejected"] > 0, \
        "check_answer should reject a claim citing an unrevealed channel entry"

    con.close()


# ---------------------------------------------------------------------------
# (d) check_answer rejects fabricated entry_id and mismatched verdict
# ---------------------------------------------------------------------------

def test_d_check_answer_rejects_bad_claims() -> None:
    """check_answer rejects (1) a fabricated entry_id and (2) a wrong verdict."""
    iid, date = DEV_CASES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    session.test_dimension("sessions", "channel")
    harness = session._harness_ledger()
    real_entry = harness.entries[0]

    # (1) Fabricated entry_id not present in the harness
    fabricated = Claim(
        metric="sessions", dimension="channel", segment="Organic Search",
        direction="down", kind="within", evidence_ids=["E999"],
        strength="strong", basis="statistical_test",
    )
    res = check_answer(
        Answer(pattern="localized", claims=[fabricated], primary=[]), harness
    )
    assert res["n_rejected"] > 0, "Fabricated entry_id must be rejected"

    # (2) Real entry_id but strength (verdict) differs from the recorded entry
    all_verdicts = [
        "strong", "plausible", "material_but_uncertain", "unsupported",
        "immaterial", "material_untested", "arithmetic",
    ]
    wrong_strength = next(v for v in all_verdicts if v != real_entry.verdict)
    wrong_verdict_claim = Claim(
        metric=real_entry.metric, dimension=real_entry.dimension,
        segment=real_entry.segment, direction="down",
        kind="within" if real_entry.kind == "segment" else real_entry.kind,
        evidence_ids=[real_entry.evidence_id],
        strength=wrong_strength, basis=real_entry.basis,
    )
    res2 = check_answer(
        Answer(pattern="localized", claims=[wrong_verdict_claim], primary=[]), harness
    )
    assert res2["n_rejected"] > 0, "Wrong verdict must be rejected"

    con.close()


# ---------------------------------------------------------------------------
# (e) Invalid inputs return error dict, never raise
# ---------------------------------------------------------------------------

def test_e_invalid_inputs_return_error_dict() -> None:
    """test_dimension with bad metric or dimension returns an error dict."""
    iid, date = DEV_CASES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    bad_metric = session.test_dimension("revenue", "channel")
    assert isinstance(bad_metric, dict) and "error" in bad_metric, \
        f"Expected error dict for bad metric, got {bad_metric!r}"

    bad_dim = session.test_dimension("sessions", "zipcode")
    assert isinstance(bad_dim, dict) and "error" in bad_dim, \
        f"Expected error dict for bad dimension, got {bad_dim!r}"

    con.close()


# ---------------------------------------------------------------------------
# (f) Repeat test_dimension calls do not duplicate ledger entries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("iid,date", DEV_CASES)
def test_f_no_duplicate_entries(iid: str, date: str) -> None:
    """Calling test_dimension twice for the same pair is idempotent."""
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    rows_first = session.test_dimension("sessions", "channel")
    n_before = len(session._harness_ledger().entries)

    rows_second = session.test_dimension("sessions", "channel")
    n_after = len(session._harness_ledger().entries)

    assert n_before == n_after, \
        f"{iid} {date}: ledger grew from {n_before} to {n_after} on repeat call"
    assert rows_first == rows_second, \
        f"{iid} {date}: repeat call returned different rows"

    con.close()


# ---------------------------------------------------------------------------
# (g) agent/tools.py imports nothing from evaluation/ and has no "heldout"
# ---------------------------------------------------------------------------

def test_g_no_evaluation_imports() -> None:
    """Grep-style check: agent/tools.py is evaluation/-clean."""
    tools_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "agent", "tools.py",
    )
    with open(tools_path) as f:
        source = f.read()

    assert not re.search(r"\bevaluation\b", source), \
        "agent/tools.py must not import from or reference evaluation/"
    assert "heldout" not in source, \
        "agent/tools.py must not contain the string 'heldout'"
