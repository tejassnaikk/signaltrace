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

            # render_test_dimension collapses immaterial rows to a note entry
            material_rows = [r for r in rows if "entry_id" in r]
            note_rows = [r for r in rows if "note" in r]
            expected = by_key.get((metric, dimension), [])
            expected_material = [e for e in expected if e.verdict != "immaterial"]
            n_immaterial = len(expected) - len(expected_material)

            assert len(material_rows) == len(expected_material), (
                f"{iid} {date} ({metric},{dimension}): "
                f"got {len(material_rows)} material rows, expected {len(expected_material)}"
            )
            # at most one note entry, present iff any rows were omitted
            assert len(note_rows) == (1 if n_immaterial else 0), \
                f"{iid} {date} ({metric},{dimension}): expected {'1 note' if n_immaterial else 'no note'}"

            for i, (row, e) in enumerate(zip(material_rows, expected_material)):
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

# ---------------------------------------------------------------------------
# (h) render compaction: immaterial rows collapsed, ledger summary compact
# ---------------------------------------------------------------------------

def test_h_immaterial_rows_collapsed() -> None:
    """render_test_dimension collapses immaterial rows to a single note entry."""
    from agent.render import render_test_dimension
    rows = [
        {"segment": "A", "verdict": "strong", "entry_id": "E1"},
        {"segment": "B", "verdict": "immaterial", "entry_id": "E2"},
        {"segment": "C", "verdict": "immaterial", "entry_id": "E3"},
    ]
    rendered = render_test_dimension(rows)
    material = [r for r in rendered if "entry_id" in r]
    notes = [r for r in rendered if "note" in r]
    assert len(material) == 1
    assert material[0]["segment"] == "A"
    assert len(notes) == 1
    assert "2" in notes[0]["note"]  # "2 immaterial row(s) omitted"


def test_h_no_note_when_no_immaterial() -> None:
    """No note entry when all rows are material."""
    from agent.render import render_test_dimension
    rows = [
        {"segment": "A", "verdict": "strong", "entry_id": "E1"},
        {"segment": "B", "verdict": "plausible", "entry_id": "E2"},
    ]
    rendered = render_test_dimension(rows)
    assert len(rendered) == 2
    assert all("entry_id" in r for r in rendered)


def test_h_ledger_summary_compact() -> None:
    """render_ledger_summary returns only entry_id + verdict, no immaterial."""
    from agent.render import render_ledger_summary

    class FakeEntry:
        def __init__(self, eid, verdict, actual=1.0, expected=2.0):
            self.evidence_id = eid
            self.verdict = verdict
            self.actual = actual
            self.expected = expected

    entries = [
        FakeEntry("E1", "strong"),
        FakeEntry("E2", "immaterial"),
        FakeEntry("E3", "plausible"),
    ]
    rendered = render_ledger_summary(entries)
    assert len(rendered) == 2
    ids = [r["entry_id"] for r in rendered]
    assert "E1" in ids and "E3" in ids and "E2" not in ids
    for r in rendered:
        assert set(r.keys()) == {"entry_id", "verdict"}


def test_h_context_guard_fires(tmp_path) -> None:
    """run_agent returns failed_context when conversation exceeds context_limit."""
    import json
    from agent.loop import run_agent
    from agent.llm import FakeClient
    from evaluation.injector import build_incident_con
    # Force the guard to fire after the first client.chat call
    # by using a very small context_limit and a large fake response
    con = build_incident_con("I1", "2017-01-26")
    from agent.tools import ToolSession
    session = ToolSession("2017-01-26", con)

    big_content = "x" * 9000  # 3000 fake tokens of content
    scripted = [{"content": big_content, "tool_calls": []}]
    result = run_agent(session, FakeClient(scripted), context_limit=100)
    assert result["status"] == "failed_context"


def test_h_exhaustive_transcript_under_7000_tokens() -> None:
    """All 41 dev dates: exhaustive transcript (gfd+9td+gls) fits in 7000 tokens."""
    import json
    from agent.prompt import SYSTEM_PROMPT
    from evaluation.injector import build_incident_con
    from agent.tools import ToolSession
    from evaluation.arm_neutral import load_ground_truth

    INCIDENTS_DEV = ["I1", "I2", "I3", "I3b", "I4", "I5", "I6", "I7", "C1"]
    for iid in INCIDENTS_DEV:
        gt = load_ground_truth(iid)
        for date in gt["dates"]:
            con = build_incident_con(iid, str(date))
            sess = ToolSession(str(date), con)
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Investigate {date}."},
            ]
            gfd = sess.get_factor_decomposition()
            messages.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_factor_decomposition", "arguments": {}}}]})
            messages.append({"role": "tool", "content": json.dumps(gfd)})
            from metrics.dependency_graph import DIMENSIONS, REVENUE_FACTORS
            for metric in REVENUE_FACTORS:
                for dim in DIMENSIONS:
                    rows = sess.test_dimension(metric, dim)
                    messages.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "test_dimension", "arguments": {"metric": metric, "dimension": dim}}}]})
                    messages.append({"role": "tool", "content": json.dumps(rows)})
            gls = sess.get_ledger_summary()
            messages.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_ledger_summary", "arguments": {}}}]})
            messages.append({"role": "tool", "content": json.dumps(gls)})
            total = sum(len(json.dumps(m, default=str)) for m in messages)
            tokens = total // 3
            assert tokens < 7500, (
                f"{iid} {date}: exhaustive transcript {tokens} tokens >= 7500"
            )
            con.close()


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
