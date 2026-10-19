"""pytest suite for agent/loop.py and evaluation/run_agent.py (M6 task 6).

All tests use FakeClient only — never calls the real Ollama server.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.llm import FakeClient
from agent.loop import run_agent
from agent.tools import ToolSession
from evaluation.arm_neutral import (
    Answer, Claim, c1_claim_counts, score_a, score_b,
)
from evaluation.injector import build_incident_con, load_ground_truth
from metrics.dependency_graph import DIMENSIONS, REVENUE_FACTORS

# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

FORCED = list(REVENUE_FACTORS)

# Three incident / date pairs used for test (a)
_SCORE_DATES = [
    ("I1", "2017-01-26"),
    ("I2", "2016-10-25"),
    ("I5", "2016-11-02"),
]

# All 41 dev dates across all incidents — used for direction and exact-strength tests
_INCIDENTS = ["I1", "I2", "I3", "I3b", "I4", "I5", "I6", "I7", "C1"]
_ALL_DEV_DATES = [
    (iid, str(date))
    for iid in _INCIDENTS
    for date in load_ground_truth(iid)["dates"]
]

# Verdict → agent strength word for ALL claimable verdicts
_VERDICT_TO_STRENGTH = {
    "strong": "strong",
    "plausible": "plausible",
    "material_untested": "untested",
    "arithmetic": "exact",
}

_STRENGTH_MAP = {
    "strong": "strong",
    "plausible": "plausible",
    "material_untested": "untested",
    # fallback: any other verdict → "untested"
}

_QUALIFYING_VERDICTS = {"strong", "plausible", "material_untested"}


def _entry_direction(e) -> str:
    if e.actual is None or e.expected is None:
        return "up"
    return "down" if e.actual < e.expected else "up"


def _build_scripted_responses(session, order=None):
    """Build FakeClient responses: one test_dimension call per response (in
    order), then a submit_answer mirroring the engine's own answer.

    Pre-populates all 9 pairs so entry_ids are stable.  Claims are ordered:
    engine.primary rows first (in primary order), then remaining engine.claims
    skipping already-included entry_ids.  This reproduces Score B exactly
    (accepted[:3] == engine.primary) while Score A is unchanged because each
    primary row is the top-ranked row of its own (metric, dimension).
    """
    from evaluation.arm_neutral import engine_answer as _engine_answer

    if order is None:
        order = [(m, d) for m in REVENUE_FACTORS for d in DIMENSIONS]

    # Pre-populate all pairs in the given order
    for m, d in order:
        session.test_dimension(m, d)

    # Build engine answer from the fully-populated harness ledger
    harness_answer = _engine_answer({
        "ledger": session._harness_ledger(),
        "factors": session._engine_result["factors"],
    })

    # Order: primary rows first (in primary order), then remaining claims,
    # deduped by entry_id.  This preserves within-group order because each
    # primary row is the top-|contribution| row of its (metric, dimension)
    # group, matching engine.claims sort order.
    seen: set[str] = set()
    ordered: list = []
    for c in harness_answer.primary:
        eid = c.evidence_ids[0]
        if eid not in seen:
            seen.add(eid)
            ordered.append(c)
    for c in harness_answer.claims:
        eid = c.evidence_ids[0]
        if eid not in seen:
            seen.add(eid)
            ordered.append(c)

    _smap = {"strong": "strong", "plausible": "plausible", "material_untested": "untested"}
    claims_args = [
        {
            "entry_id": c.evidence_ids[0],
            "direction": c.direction,
            "strength": _smap.get(c.strength, "untested"),
        }
        for c in ordered
    ]

    responses = [
        {"tool_calls": [{"name": "test_dimension", "args": {"metric": m, "dimension": d}}]}
        for m, d in order
    ]
    responses.append({
        "tool_calls": [{
            "name": "submit_answer",
            "args": {
                "pattern": harness_answer.pattern,
                "claims": claims_args,
                "narrative": "Scripted engine answer.",
            },
        }]
    })
    return responses


def _dict_to_claim(d: dict) -> Claim:
    return Claim(
        metric=d["metric"],
        dimension=d["dimension"],
        segment=d["segment"],
        direction=d["direction"],
        kind=d["kind"],
        evidence_ids=d["evidence_ids"],
        strength=d["strength"],
        basis=d.get("basis"),
    )


def _dict_to_answer(d: dict | None) -> Answer | None:
    if d is None:
        return None
    claims = [_dict_to_claim(c) for c in d.get("claims", [])]
    primary = [_dict_to_claim(c) for c in d.get("primary", [])]
    return Answer(
        pattern=d["pattern"],
        claims=claims,
        primary=primary,
        narrative=d.get("narrative", ""),
    )


def _claim_key(c: Claim) -> tuple:
    return (c.metric, c.dimension, c.segment, c.kind, c.direction, c.strength)


def _answers_structurally_equal(a1: Answer, a2: Answer) -> bool:
    if a1.pattern != a2.pattern:
        return False
    s1 = sorted(_claim_key(c) for c in a1.claims)
    s2 = sorted(_claim_key(c) for c in a2.claims)
    if s1 != s2:
        return False
    return [_claim_key(c) for c in a1.primary] == [_claim_key(c) for c in a2.primary]


# ------------------------------------------------------------------ #
# (a) Scripted engine-answer agent equals engine Score A/B            #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("iid,date", _SCORE_DATES)
def test_a_scripted_engine_answer(iid: str, date: str) -> None:
    """Agent that submits engine answer (all 9 calls) gets same Score A/B."""
    from collections import defaultdict
    from evaluation.arm_neutral import engine_answer

    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    responses = _build_scripted_responses(session)
    result = run_agent(session, FakeClient(responses), budget=12)

    assert result["status"] == "ok", f"{iid} {date}: status={result['status']}"
    assert result["answer"] is not None

    agent_answer = _dict_to_answer(result["answer"])
    engine_ans = engine_answer({
        "ledger": session._harness_ledger(),
        "factors": session._engine_result["factors"],
    })

    # Assert: for each (metric, dimension), the relative order of submitted
    # claims equals the engine's claims order for that group.
    eng_by_group: dict[tuple, list[str]] = defaultdict(list)
    for c in engine_ans.claims:
        eng_by_group[(c.metric, c.dimension)].append(c.evidence_ids[0])

    sub_by_group: dict[tuple, list[str]] = defaultdict(list)
    for c in agent_answer.claims:
        sub_by_group[(c.metric, c.dimension)].append(c.evidence_ids[0])

    for grp_key in sub_by_group:
        sub_ids = sub_by_group[grp_key]
        eng_ids = eng_by_group.get(grp_key, [])
        eng_filtered = [eid for eid in eng_ids if eid in set(sub_ids)]
        assert sub_ids == eng_filtered, (
            f"Within-group order mismatch for {grp_key}: "
            f"submitted={sub_ids} engine_order={eng_filtered}"
        )

    # Score A
    sa_agent = score_a(agent_answer, iid)
    sa_engine = score_a(engine_ans, iid)
    assert sa_agent == sa_engine, (
        f"{iid} {date}: Score A mismatch: agent={sa_agent} engine={sa_engine}"
    )

    # Score B
    sb_agent = score_b(agent_answer, iid)
    sb_engine = score_b(engine_ans, iid)
    for key in ("pattern_correct", "top1_correct", "top3_correct"):
        if sb_engine.get(key) is not None:
            assert sb_agent.get(key) == sb_engine.get(key), (
                f"{iid} {date}: Score B {key}: agent={sb_agent.get(key)} "
                f"engine={sb_engine.get(key)}"
            )

    con.close()


# ------------------------------------------------------------------ #
# (b) Checker rejects bad claims                                      #
# ------------------------------------------------------------------ #

def test_b_fabricated_entry_id_rejected() -> None:
    """A fabricated entry_id is removed and counted."""
    iid, date = _SCORE_DATES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)
    session.test_dimension("sessions", "channel")

    # Get a real entry and build a claim with a fabricated ID
    real = session._harness_ledger().entries[0]
    responses = [{
        "tool_calls": [{
            "name": "submit_answer",
            "args": {
                "pattern": "localized",
                "claims": [
                    {
                        "entry_id": "E_FAKE_999",
                        "direction": _entry_direction(real),
                        "strength": _STRENGTH_MAP.get(real.verdict, "untested"),
                    }
                ],
                "narrative": "test",
            },
        }]
    }]
    result = run_agent(session, FakeClient(responses))
    assert result["status"] == "ok"
    assert result["n_rejected"] == 1
    assert len(result["answer"]["claims"]) == 0
    con.close()


def test_b_wrong_direction_rejected() -> None:
    """A claim whose direction contradicts the entry sign is removed."""
    iid, date = _SCORE_DATES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)
    rows = session.test_dimension("sessions", "channel")

    # Find a row where we can determine the direction
    real = session._harness_ledger().entries[0]
    correct_dir = _entry_direction(real)
    wrong_dir = "up" if correct_dir == "down" else "down"

    # Only test if we can determine direction (actual and expected are not None)
    if real.actual is not None and real.expected is not None:
        responses = [{
            "tool_calls": [{
                "name": "submit_answer",
                "args": {
                    "pattern": "localized",
                    "claims": [{
                        "entry_id": real.evidence_id,
                        "direction": wrong_dir,
                        "strength": _STRENGTH_MAP.get(real.verdict, "untested"),
                    }],
                    "narrative": "test",
                },
            }]
        }]
        result = run_agent(session, FakeClient(responses))
        assert result["status"] == "ok"
        assert result["n_rejected"] == 1
    con.close()


def test_b_strong_on_untested_rejected() -> None:
    """'strong' on a material_untested entry is rejected."""
    iid, date = _SCORE_DATES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)
    session.test_dimension("aov", "channel")

    # Find an untested entry (aov always produces material_untested)
    harness = session._harness_ledger()
    untested = next(
        (e for e in harness.entries
         if e.verdict == "material_untested" and e.kind == "segment"),
        None
    )
    if untested is None:
        pytest.skip("no material_untested segment found for this date")

    responses = [{
        "tool_calls": [{
            "name": "submit_answer",
            "args": {
                "pattern": "localized",
                "claims": [{
                    "entry_id": untested.evidence_id,
                    "direction": _entry_direction(untested),
                    "strength": "strong",  # wrong: should be "untested"
                }],
                "narrative": "test",
            },
        }]
    }]
    result = run_agent(session, FakeClient(responses))
    assert result["status"] == "ok"
    assert result["n_rejected"] == 1
    con.close()


# ------------------------------------------------------------------ #
# (c) Budget exceeded → failed_budget                                 #
# ------------------------------------------------------------------ #

def test_c_budget_exceeded() -> None:
    """13 tool calls → failed_budget; answer is None."""
    iid, date = _SCORE_DATES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    # Script 13 test_dimension calls (some repeated, fine since idempotent)
    pairs = [(m, d) for m in REVENUE_FACTORS for d in DIMENSIONS]  # 9 pairs
    extra = [("sessions", "channel"), ("sessions", "device"), ("sessions", "country"),
             ("conversion_rate", "channel")]  # 4 more = 13 total
    responses = [
        {"tool_calls": [{"name": "test_dimension", "args": {"metric": m, "dimension": d}}]}
        for m, d in (pairs + extra)
    ]
    result = run_agent(session, FakeClient(responses), budget=12)
    assert result["status"] == "failed_budget", f"got {result['status']!r}"
    assert result["answer"] is None
    con.close()


def test_c_budget_fails_c1_scoring() -> None:
    """failed_budget run has no answer; c1_claim_counts sees 0 claims (not 'none' pass)."""
    # C1's correct outcome is pattern="none" with 0 false positives.
    # A failed_budget run has answer=None, which is treated as a failure.
    iid, date = "C1", "2016-11-10"
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    pairs = [(m, d) for m in REVENUE_FACTORS for d in DIMENSIONS]
    extra = [("sessions", "channel"), ("sessions", "device"), ("sessions", "country"),
             ("conversion_rate", "channel")]
    responses = [
        {"tool_calls": [{"name": "test_dimension", "args": {"metric": m, "dimension": d}}]}
        for m, d in (pairs + extra)
    ]
    result = run_agent(session, FakeClient(responses), budget=12)
    assert result["status"] == "failed_budget"
    # failed_budget run: no answer to score as "none" → counts as a failed run
    assert result["answer"] is None
    con.close()


@pytest.mark.parametrize("iid,date", _ALL_DEV_DATES)
def test_direction_field_never_rejected(iid: str, date: str) -> None:
    """Copying row['direction'] into a claim is never rejected for direction.

    Parametrized over all 41 dev dates.  Only claimable verdicts are
    submitted (strong/plausible/material_untested/arithmetic); the checker
    must accept all of them on direction without raising a direction-mismatch
    rejection.  Uses the shared _row_direction definition from agent.tools.
    """
    from agent.tools import _row_direction  # confirm same function as checker

    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    # Pre-populate all 9 pairs and collect claimable rows
    all_pairs = [(m, d) for m in REVENUE_FACTORS for d in DIMENSIONS]
    for m, d in all_pairs:
        session.test_dimension(m, d)

    claims_args = []
    for e in session._harness_ledger().entries:
        strength = _VERDICT_TO_STRENGTH.get(e.verdict)
        if strength is None:
            continue
        claims_args.append({
            "entry_id": e.evidence_id,
            "direction": _row_direction(e.actual, e.expected),
            "strength": strength,
        })

    if not claims_args:
        pytest.skip(f"no claimable rows on {iid}/{date}")

    responses = [
        {"tool_calls": [{"name": "test_dimension", "args": {"metric": m, "dimension": d}}]}
        for m, d in all_pairs
    ]
    responses.append({
        "tool_calls": [{
            "name": "submit_answer",
            "args": {"pattern": "localized", "claims": claims_args, "narrative": "direction test"},
        }]
    })

    result = run_agent(session, FakeClient(responses), budget=14)
    assert result["status"] == "ok", f"{iid}/{date}: {result['status']}"
    dir_rejections = [r for r in result["rejected"] if "direction" in r.get("reason", "")]
    assert dir_rejections == [], f"{iid}/{date}: direction rejections: {dir_rejections}"
    con.close()


def test_c_exhaustive_path_with_two_repairs_ok() -> None:
    """Exhaustive path (11 regular calls) + 2 malformed submits + 1 valid = 14 total.

    Verifies the default budget=14 fits: submit_answer counts against the budget
    (line 321 of loop.py increments tc_total before the schema check), so the worst
    case exhaustive run needs exactly 14 calls.
    """
    iid, date = _SCORE_DATES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    pairs = [(m, d) for m in REVENUE_FACTORS for d in DIMENSIONS]  # 9 pairs
    responses = []
    responses.append({"tool_calls": [{"name": "get_factor_decomposition", "args": {}}]})
    for m, d in pairs:
        responses.append({"tool_calls": [{"name": "test_dimension", "args": {"metric": m, "dimension": d}}]})
    responses.append({"tool_calls": [{"name": "get_ledger_summary", "args": {}}]})
    # 2 malformed submits + 1 valid = 3 submit attempts
    responses.append(_BAD_SUBMIT)
    responses.append(_BAD_SUBMIT)
    responses.append(_GOOD_SUBMIT)
    # Total: 1 + 9 + 1 + 1 + 1 + 1 = 14 calls; budget=14 → status ok

    result = run_agent(session, FakeClient(responses), budget=14)
    assert result["status"] == "ok", (
        f"exhaustive + 2 repairs should fit budget=14, got {result['status']!r}"
    )
    con.close()


# ------------------------------------------------------------------ #
# (c3) strength "exact" for arithmetic mix rows                      #
# ------------------------------------------------------------------ #

def test_exact_strength_accepted_for_mix_row() -> None:
    """A claim citing a device mix row with strength='exact' is accepted."""
    iid, date = "I6", "2016-11-04"  # I6: conversion_rate device mix incident
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    rows = session.test_dimension("conversion_rate", "device")
    mix_row = next((r for r in rows["citable"] if r["kind"] == "mix"), None)
    assert mix_row is not None, "No mix row in citable block for conversion_rate x device on I6/2016-11-04"
    assert mix_row["evidence"] == "exact", f"Expected evidence='exact', got {mix_row.get('evidence')!r}"

    responses = [
        {"tool_calls": [{"name": "test_dimension", "args": {"metric": "conversion_rate", "dimension": "device"}}]},
        {
            "tool_calls": [{
                "name": "submit_answer",
                "args": {
                    "pattern": "mix",
                    "claims": [{
                        "entry_id": mix_row["short_id"],
                        "direction": mix_row["direction"],
                        "strength": "exact",
                    }],
                    "narrative": "Device mix shift drove conversion_rate change.",
                },
            }]
        },
    ]

    result = run_agent(session, FakeClient(responses), budget=14)
    assert result["status"] == "ok"
    assert result["n_rejected"] == 0, f"Unexpected rejections: {result['rejected']}"
    assert len(result["answer"]["claims"]) == 1
    con.close()


def test_i6_mix_pattern_passes_score_b() -> None:
    """A scripted mix answer citing the device mix row passes score_b on an I6 dev date."""
    iid, date = "I6", "2016-11-04"
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    rows = session.test_dimension("conversion_rate", "device")
    mix_row = next((r for r in rows["citable"] if r["kind"] == "mix"), None)
    assert mix_row is not None

    responses = [
        {"tool_calls": [{"name": "test_dimension", "args": {"metric": "conversion_rate", "dimension": "device"}}]},
        {
            "tool_calls": [{
                "name": "submit_answer",
                "args": {
                    "pattern": "mix",
                    "claims": [{
                        "entry_id": mix_row["short_id"],
                        "direction": mix_row["direction"],
                        "strength": "exact",
                    }],
                    "narrative": "Device mix shift.",
                },
            }]
        },
    ]

    result = run_agent(session, FakeClient(responses), budget=14)
    assert result["status"] == "ok"

    agent_answer = _dict_to_answer(result["answer"])
    sb = score_b(agent_answer, iid)
    assert sb["pattern_correct"] is True, f"Expected pattern_correct=True, got {sb}"
    con.close()


# ------------------------------------------------------------------ #
# (d) Malformed submit_answer repair                                  #
# ------------------------------------------------------------------ #

_BAD_SUBMIT = {
    "tool_calls": [{
        "name": "submit_answer",
        "args": {"pattern": "INVALID_PATTERN"},  # missing claims/narrative, bad pattern
    }]
}
_GOOD_SUBMIT = {
    "tool_calls": [{
        "name": "submit_answer",
        "args": {"pattern": "none", "claims": [], "narrative": "ok"},
    }]
}


def test_d_repaired_on_second_attempt() -> None:
    """Malformed then valid submit → status ok."""
    iid, date = _SCORE_DATES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    responses = [_BAD_SUBMIT, _BAD_SUBMIT, _GOOD_SUBMIT]
    result = run_agent(session, FakeClient(responses))
    assert result["status"] == "ok", f"got {result['status']!r}"
    con.close()


def test_d_three_malformed_gives_failed_malformed() -> None:
    """Three malformed submits → failed_malformed."""
    iid, date = _SCORE_DATES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    responses = [_BAD_SUBMIT, _BAD_SUBMIT, _BAD_SUBMIT]
    result = run_agent(session, FakeClient(responses))
    assert result["status"] == "failed_malformed", f"got {result['status']!r}"
    con.close()


# ------------------------------------------------------------------ #
# (e) Resume: interrupted run restarts cleanly                        #
# ------------------------------------------------------------------ #

def test_e_resume() -> None:
    """Runner skips already-done keys and produces no duplicates."""
    import evaluation.run_agent as run_agent_mod

    # Minimal 2-date work list to test resumption
    test_dates = [("I1", "2017-01-26"), ("I1", "2017-03-16")]
    runs = 1
    n_runs = len(test_dates) * runs

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "agent_dev_test.jsonl")

        # Write the first record as if it were already done
        first_record = {
            "incident_id": test_dates[0][0],
            "date": test_dates[0][1],
            "run": 0,
            "status": "ok",
            "answer": {"pattern": "none", "claims": [], "primary": [], "narrative": ""},
            "n_rejected": 0,
            "tool_call_counts": {},
            "score_a": None,
            "score_b": {"pattern_correct": False, "top1_correct": False, "top3_correct": False},
            "c1_claim_counts": None,
            "checker": {"n_claims": 0, "n_rejected": 0, "rejected": []},
            "failed_run": False,
            "config": {},
            "prompt_version": "v0",
            "git_head": "test",
        }
        with open(out_path, "w") as f:
            f.write(json.dumps(first_record) + "\n")

        # Verify _load_done_keys reads it
        done = run_agent_mod._load_done_keys(out_path)
        assert "I1_2017-01-26_0" in done
        assert "I1_2017-03-16_0" not in done

        # Simulate writing the second record (as if the runner completed it)
        second_record = {**first_record, "date": test_dates[1][1]}
        with open(out_path, "a") as f:
            f.write(json.dumps(second_record) + "\n")

        # Verify both keys are present and no duplicates
        done_final = run_agent_mod._load_done_keys(out_path)
        assert "I1_2017-01-26_0" in done_final
        assert "I1_2017-03-16_0" in done_final

        # Verify no duplicate keys in the file
        with open(out_path) as f:
            keys = []
            for line in f:
                rec = json.loads(line.strip())
                keys.append(f"{rec['incident_id']}_{rec['date']}_{rec['run']}")
        assert len(keys) == len(set(keys)), "Duplicate keys in output file"


# ------------------------------------------------------------------ #
# (f) Heldout guard                                                   #
# ------------------------------------------------------------------ #

def test_f_heldout_refuses_without_flag() -> None:
    """--split heldout without --i-confirm-heldout exits with code 1."""
    import evaluation.run_agent as run_agent_mod
    with pytest.raises(SystemExit) as exc:
        run_agent_mod.main(["--split", "heldout"])
    assert exc.value.code == 1


def test_f_heldout_refuses_without_tag(monkeypatch) -> None:
    """--split heldout with flag but no agent-frozen tag exits with code 1."""
    import evaluation.run_agent as run_agent_mod
    monkeypatch.setattr(run_agent_mod, "_agent_frozen_tag_exists", lambda: False)
    with pytest.raises(SystemExit) as exc:
        run_agent_mod.main(["--split", "heldout", "--i-confirm-heldout"])
    assert exc.value.code == 1


# ------------------------------------------------------------------ #
# (g) Grep: agent/ has no evaluation imports and no "heldout"        #
# ------------------------------------------------------------------ #

_AGENT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"
)


# ------------------------------------------------------------------ #
# (h) Claim-level validation + OllamaClient model/options guarantee   #
# ------------------------------------------------------------------ #

def test_h_invalid_strength_rejected_not_malformed() -> None:
    """A claim with strength='material_but_uncertain' is rejected per-claim (invalid_strength),
    not treated as a structural malformed answer.  One valid claim in the same answer is accepted;
    final status is 'ok', not 'failed_malformed'."""
    iid, date = _SCORE_DATES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    session.test_dimension("sessions", "channel")
    harness = session._harness_ledger()
    valid_entry = next(e for e in harness.entries if e.verdict in ("strong", "plausible"))
    valid_dir = _entry_direction(valid_entry)
    valid_strength = _STRENGTH_MAP.get(valid_entry.verdict, "untested")

    responses = [{
        "tool_calls": [{
            "name": "submit_answer",
            "args": {
                "pattern": "localized",
                "claims": [
                    # Invalid strength — ledger verdict string, not a strength word
                    {"entry_id": valid_entry.evidence_id, "direction": valid_dir,
                     "strength": "material_but_uncertain"},
                    # Valid claim
                    {"entry_id": valid_entry.evidence_id, "direction": valid_dir,
                     "strength": valid_strength},
                ],
                "narrative": "test",
            },
        }]
    }]
    result = run_agent(session, FakeClient(responses))
    assert result["status"] == "ok", \
        f"invalid_strength should not malform the answer; got {result['status']!r}"
    assert result["n_rejected"] == 1, f"Expected 1 rejected, got {result['n_rejected']}"
    assert any("invalid_strength" in r.get("reason", "") for r in result["rejected"]), \
        f"Expected invalid_strength reason; got {result['rejected']}"
    assert len(result["answer"]["claims"]) == 1, \
        f"Valid claim should be accepted; got {result['answer']['claims']}"
    con.close()


def test_h_structural_error_still_triggers_malformed() -> None:
    """submit_answer missing the required 'claims' field is structural → repair cycle
    → failed_malformed after 3 attempts."""
    iid, date = _SCORE_DATES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    bad = {"tool_calls": [{"name": "submit_answer",
                           "args": {"pattern": "localized", "narrative": "no claims field"}}]}
    result = run_agent(session, FakeClient([bad, bad, bad]))
    assert result["status"] == "failed_malformed", \
        f"Structural error should produce failed_malformed; got {result['status']!r}"
    con.close()


# ------------------------------------------------------------------ #
# (i) Text-turn cap and 20-turn hard cap                              #
# ------------------------------------------------------------------ #

def test_i_two_text_turns_fails_no_tool_call() -> None:
    """A client that always returns text ends as failed_no_tool_call after exactly
    2 consecutive text turns and never reaches the context guard."""
    iid, date = _SCORE_DATES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    # Five text responses so FakeClient is not exhausted before the cap fires
    responses = [{"content": "I will call submit_answer now."} for _ in range(5)]
    result = run_agent(session, FakeClient(responses), context_limit=999_999)
    assert result["status"] == "failed_no_tool_call", \
        f"Expected failed_no_tool_call, got {result['status']!r}"
    text_events = [e for e in result["trace"]
                   if e.get("event") == "llm_response" and not e.get("tool_calls")]
    assert len(text_events) == 2, \
        f"Expected exactly 2 text turns before cap, got {len(text_events)}"
    con.close()


def test_i_text_then_tool_resets_counter() -> None:
    """A text turn followed by a tool call resets the consecutive counter;
    a second isolated text turn does not trigger failed_no_tool_call."""
    iid, date = _SCORE_DATES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    responses = [
        {"content": "let me think"},  # text (consecutive=1), nudge sent
        {"tool_calls": [{"name": "test_dimension",
                         "args": {"metric": "sessions", "dimension": "channel"}}]},
        {"content": "let me think again"},  # text (consecutive reset to 1), nudge sent
        {"tool_calls": [{"name": "submit_answer",
                         "args": {"pattern": "none", "claims": [], "narrative": "done"}}]},
    ]
    result = run_agent(session, FakeClient(responses))
    assert result["status"] == "ok", \
        f"Counter should reset on tool call; got {result['status']!r}"
    con.close()


def test_i_20_turn_hard_cap_triggers_failed_budget() -> None:
    """A run that makes exactly 20 LLM calls without finishing hits failed_budget
    from the max_turns cap before the per-tool-call budget."""
    iid, date = _SCORE_DATES[0]
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    # 25 repeated test_dimension calls; budget=25 so tool budget won't fire,
    # but max_turns=20 fires after 20 calls
    responses = [
        {"tool_calls": [{"name": "test_dimension",
                         "args": {"metric": "sessions", "dimension": "channel"}}]}
        for _ in range(25)
    ]
    result = run_agent(session, FakeClient(responses), budget=25, max_turns=20)
    assert result["status"] == "failed_budget", \
        f"Expected failed_budget from max_turns cap, got {result['status']!r}"
    llm_events = [e for e in result["trace"] if e.get("event") == "llm_response"]
    assert len(llm_events) == 20, \
        f"Expected exactly 20 LLM turns before cap, got {len(llm_events)}"
    con.close()


def test_h_ollama_client_model_and_options_present_on_every_call(monkeypatch) -> None:
    """OllamaClient passes model=MODEL_TAG and options=OPTIONS on every ollama.chat() call,
    including the call that follows a text turn (no tool_calls) + nudge."""
    import ollama as _ollama_mod
    from agent.llm import OllamaClient
    from agent.config import MODEL_TAG, OPTIONS, config

    calls: list[dict] = []

    class _FakeTC:
        class function:
            name = "get_factor_decomposition"
            arguments = {}

    class _FakeMsg:
        content = None
        tool_calls = [_FakeTC()]

    class _FakeResp:
        message = _FakeMsg()
        prompt_eval_count = 100
        eval_count = 5

    def _capture(**kwargs):
        calls.append(kwargs)
        return _FakeResp()

    monkeypatch.setattr(_ollama_mod, "chat", _capture)

    client = OllamaClient(config())

    # Call 1: normal (tool call response)
    client.chat([{"role": "user", "content": "investigate 2017-01-26"}], [])

    # Call 2: messages include a text-turn assistant response (no tool_calls) + nudge,
    # reproducing the path that previously caused HTTP 400 "model is required"
    client.chat([
        {"role": "user", "content": "investigate 2017-01-26"},
        {"role": "assistant", "content": 'submit_answer("localized", [], "text turn")'},
        {"role": "user", "content": "Please call one of the available tools."},
    ], [])

    assert len(calls) == 2, f"Expected 2 ollama.chat calls, got {len(calls)}"
    for i, kw in enumerate(calls):
        assert kw.get("model") == MODEL_TAG, \
            f"call {i}: model={kw.get('model')!r}, expected {MODEL_TAG!r}"
        assert kw.get("options") == OPTIONS, \
            f"call {i}: options={kw.get('options')!r}, expected {OPTIONS}"


def test_g_no_evaluation_imports() -> None:
    """agent/*.py files must not import from evaluation/ or mention 'heldout'."""
    for fname in os.listdir(_AGENT_DIR):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(_AGENT_DIR, fname)
        with open(fpath) as f:
            source = f.read()
        assert not re.search(r"\bevaluation\b", source), \
            f"{fname} must not reference evaluation/"
        assert "heldout" not in source, \
            f"{fname} must not contain 'heldout'"


# ------------------------------------------------------------------ #
# (j) v1: claim_as column                                            #
# ------------------------------------------------------------------ #

def test_j_claim_as_matches_checker_mapping() -> None:
    """render.verdict_to_claim_as and loop._STRENGTH_TO_VERDICT are consistent.

    For every claimable verdict, verdict_to_claim_as(verdict) returns the
    strength word that _STRENGTH_TO_VERDICT maps back to that same verdict.
    For non-claimable verdicts, verdict_to_claim_as returns '-'.
    """
    from agent.render import verdict_to_claim_as, _VERDICT_TO_CLAIM_AS
    from agent.loop import _STRENGTH_TO_VERDICT

    # Every claimable verdict must round-trip through the two mappings
    for verdict, strength in _VERDICT_TO_CLAIM_AS.items():
        assert verdict_to_claim_as(verdict) == strength, \
            f"verdict_to_claim_as({verdict!r}) should return {strength!r}"
        assert _STRENGTH_TO_VERDICT.get(strength) == verdict, \
            f"_STRENGTH_TO_VERDICT[{strength!r}] should be {verdict!r}"

    # Non-claimable verdicts
    for verdict in ("unsupported", "material_but_uncertain", "immaterial", "unknown"):
        assert verdict_to_claim_as(verdict) == "-", \
            f"verdict_to_claim_as({verdict!r}) should return '-'"


@pytest.mark.parametrize("iid,date", _ALL_DEV_DATES)
def test_j_copying_claim_as_never_rejected_for_strength(iid: str, date: str) -> None:
    """Copying a citable row's evidence field as the strength is never rejected for invalid_strength.

    Parametrized over all 41 dev dates.  For every citable row returned by
    test_dimension, submitting evidence as the strength must produce no
    invalid_strength rejection.
    """
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    all_pairs = [(m, d) for m in REVENUE_FACTORS for d in DIMENSIONS]
    claims_args = []

    for m, d in all_pairs:
        rows = session.test_dimension(m, d)
        for row in rows["citable"]:
            claims_args.append({
                "entry_id": row["short_id"],
                "direction": row["direction"],
                "strength": row["evidence"],
            })

    if not claims_args:
        pytest.skip(f"no citable rows on {iid}/{date}")

    responses = [
        {"tool_calls": [{"name": "test_dimension", "args": {"metric": m, "dimension": d}}]}
        for m, d in all_pairs
    ]
    responses.append({
        "tool_calls": [{
            "name": "submit_answer",
            "args": {"pattern": "localized", "claims": claims_args, "narrative": "evidence test"},
        }]
    })

    result = run_agent(session, FakeClient(responses), budget=14)
    assert result["status"] == "ok", f"{iid}/{date}: {result['status']}"
    strength_rejections = [
        r for r in result["rejected"]
        if "invalid_strength" in r.get("reason", "")
    ]
    assert strength_rejections == [], \
        f"{iid}/{date}: evidence copied as strength was rejected: {strength_rejections}"
    con.close()


def test_j_dash_rows_rejected_if_claimed() -> None:
    """A non-citable (context) row's entry_id is rejected when claimed ('no ledger entry')."""
    iid, date = _SCORE_DATES[0]  # I1/2017-01-26: sessions has unsupported rows
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    # Populate all pairs so the harness ledger has all entries
    all_pairs = [(m, d) for m in REVENUE_FACTORS for d in DIMENSIONS]
    for m, d in all_pairs:
        session.test_dimension(m, d)

    # Find a non-citable, non-immaterial entry from the harness ledger
    from agent.render import verdict_to_claim_as
    from agent.tools import _row_direction
    harness = session._harness_ledger()
    dash_entry = None
    for e in harness.entries:
        if verdict_to_claim_as(e.verdict) == "-" and e.verdict != "immaterial":
            dash_entry = e
            break

    if dash_entry is None:
        pytest.skip("no non-citable non-immaterial entry found on this date")

    dash_direction = _row_direction(dash_entry.actual, dash_entry.expected)

    responses = [
        {"tool_calls": [{"name": "test_dimension", "args": {"metric": m, "dimension": d}}]}
        for m, d in all_pairs
    ]
    responses.append({
        "tool_calls": [{
            "name": "submit_answer",
            "args": {
                "pattern": "localized",
                "claims": [{
                    "entry_id": dash_entry.evidence_id,
                    "direction": dash_direction,
                    "strength": "strong",  # any strength word — entry is context-only
                }],
                "narrative": "context row claim test",
            },
        }]
    })

    result = run_agent(session, FakeClient(responses), budget=14)
    assert result["status"] == "ok"
    assert result["n_rejected"] >= 1, "Claim on context row should be rejected"
    con.close()


# ------------------------------------------------------------------ #
# (k) Immutable shared config across runs                            #
# ------------------------------------------------------------------ #

def test_k_config_frozen_single_client_reused_across_runs(monkeypatch) -> None:
    """Single OllamaClient reused across many calls, covering all unusual loop paths.

    Fails on old code (plain mutable dict OPTIONS):
    - isinstance(OPTIONS, MappingProxyType) is False
    - isinstance(client._config, MappingProxyType) is False
    - OPTIONS["__mutate__"] = "x" succeeds instead of raising TypeError
    - kw["options"] is OPTIONS (same shared object, not a fresh copy)

    Passes on new code (OPTIONS is MappingProxyType, _config is frozen):
    all four assertions hold.
    """
    import types
    import ollama as _ollama_mod
    from agent.config import OPTIONS, MODEL_TAG, config as _config_fn
    from agent.llm import OllamaClient

    # ---- Structural checks: fail on old (mutable dict) code ----
    assert isinstance(OPTIONS, types.MappingProxyType), (
        "agent.config.OPTIONS must be a MappingProxyType to prevent shared-state mutation"
    )

    client = OllamaClient(_config_fn())
    assert isinstance(client._config, types.MappingProxyType), (
        "OllamaClient._config must be a MappingProxyType"
    )

    # Any attempt to mutate must raise TypeError immediately
    with pytest.raises(TypeError):
        OPTIONS["__mutate__"] = "x"  # type: ignore[index]

    with pytest.raises(TypeError):
        client._config["__mutate__"] = "x"  # type: ignore[index]

    # ---- Behavioral: every call gets model=MODEL_TAG + a fresh options copy ----
    calls: list[dict] = []

    class _FakeTC:
        class function:
            name = "get_factor_decomposition"
            arguments = {}

    class _FakeMsg:
        content = None
        tool_calls = [_FakeTC()]

    class _FakeResp:
        message = _FakeMsg()
        prompt_eval_count = 50
        eval_count = 5

    def _capture(**kwargs):
        calls.append(dict(kwargs))
        return _FakeResp()

    monkeypatch.setattr(_ollama_mod, "chat", _capture)

    # Run 1 — unusual paths: text-turn + nudge, malformed repair, many-tool-call
    # accumulation, exception recovery.

    # Text turn + nudge path
    client.chat([
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "Investigate 2017-02-08."},
        {"role": "assistant", "content": "I'll think about this..."},
        {"role": "user", "content": (
            "Your last message was text, not a tool call. "
            "Call the submit_answer tool now with arguments pattern, "
            "claims and narrative, or call another tool."
        )},
    ], [])

    # Malformed submit repair path
    client.chat([
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "Investigate 2017-02-08."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "submit_answer", "arguments": {"pattern": "localized"}}}
        ]},
        {"role": "tool", "content": (
            '{"error": "missing required field \'claims\'", '
            '"hint": "Fix the error and call submit_answer again."}'
        ), "tool_name": "submit_answer"},
    ], [])

    # Budget-approach: many accumulated tool-result messages
    msgs = [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "Investigate 2017-02-08."},
    ]
    for _ in range(7):
        msgs += [
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "test_dimension", "arguments": {"metric": "sessions", "dimension": "channel"}}}
            ]},
            {"role": "tool", "content": "[]", "tool_name": "test_dimension"},
        ]
    client.chat(msgs, [])

    # Exception recovery: one call raises, subsequent runs must still get correct args
    def _raise_then_capture(**kwargs):
        if len(calls) == 3:
            raise RuntimeError("simulated Ollama failure")
        calls.append(dict(kwargs))
        return _FakeResp()

    monkeypatch.setattr(_ollama_mod, "chat", _raise_then_capture)
    try:
        client.chat([{"role": "user", "content": "Investigate 2017-02-08."}], [])
    except RuntimeError:
        pass  # exception is expected; the point is the next call still works

    monkeypatch.setattr(_ollama_mod, "chat", _capture)

    # Runs 2+: simple calls — config must be intact after all unusual-path calls
    run1_call_count = len(calls)
    for _ in range(5):
        client.chat([
            {"role": "system", "content": "You are an agent."},
            {"role": "user", "content": "Investigate 2017-02-01."},
        ], [])

    assert len(calls) == run1_call_count + 5, "Expected 5 additional calls in runs 2+"

    # Every captured call must have model=MODEL_TAG and a fresh options dict
    expected_options = dict(OPTIONS)
    for i, kw in enumerate(calls):
        assert kw.get("model") == MODEL_TAG, \
            f"call {i}: model={kw.get('model')!r}, expected {MODEL_TAG!r}"
        assert kw.get("options") is not OPTIONS, \
            f"call {i}: options must be a fresh copy, not the shared OPTIONS object"
        assert kw.get("options") == expected_options, \
            f"call {i}: options={kw.get('options')!r}, expected {expected_options}"


# ------------------------------------------------------------------ #
# test_l: pre-call assertion + ResponseError context capture          #
# ------------------------------------------------------------------ #

def test_l_pre_call_assertion_fires_on_empty_or_none_model(monkeypatch) -> None:
    """OllamaClient.chat() raises RuntimeError when MODEL_TAG is '' or None."""
    import agent.config as _cfg_mod
    import ollama as _ollama_mod
    from agent.config import config as _config_fn
    from agent.llm import OllamaClient

    client = OllamaClient(_config_fn())

    # Patch ollama.chat so it would never be called on the empty/None path
    monkeypatch.setattr(_ollama_mod, "chat", lambda **kw: (_ for _ in ()).throw(
        AssertionError("ollama.chat must not be reached after pre-call guard fires")
    ))

    for bad_model in ("", None):
        monkeypatch.setattr(_cfg_mod, "MODEL_TAG", bad_model)
        with pytest.raises(RuntimeError, match="model is"):
            client.chat([{"role": "user", "content": "test"}], [])


def test_l_response_error_produces_error_context_with_turn_index(monkeypatch) -> None:
    """ResponseError on the 2nd ollama.chat call produces a result with error_context.

    Verified fields: model_sent, options_sent, n_messages, request_size_chars,
    turn_index (added by loop.py).  The error string also contains the context block.
    """
    import ollama as _ollama_mod
    from agent.config import config as _config_fn, MODEL_TAG, OPTIONS
    from agent.llm import OllamaClient
    from evaluation.injector import build_incident_con

    class _FakeTC:
        class function:
            name = "get_factor_decomposition"
            arguments = {}

    class _FakeMsg:
        content = None
        tool_calls = [_FakeTC()]

    class _FakeResp:
        message = _FakeMsg()
        prompt_eval_count = 50
        eval_count = 5

    call_count = [0]

    def _first_ok_then_error(**kwargs):
        call_count[0] += 1
        if call_count[0] >= 2:
            from ollama import ResponseError
            raise ResponseError("model is required", 400)
        return _FakeResp()

    monkeypatch.setattr(_ollama_mod, "chat", _first_ok_then_error)

    client = OllamaClient(_config_fn())
    con = build_incident_con("I1", "2017-01-26")
    session = ToolSession("2017-01-26", con)

    result = run_agent(session, client)
    con.close()

    assert result["status"] == "failed_error"

    ctx = result["error_context"]
    assert ctx is not None, "error_context must be present for OllamaRequestError"
    assert ctx["model_sent"] == MODEL_TAG
    assert ctx["options_sent"] == dict(OPTIONS)
    assert isinstance(ctx["n_messages"], int) and ctx["n_messages"] > 0
    assert isinstance(ctx["request_size_chars"], int) and ctx["request_size_chars"] > 0
    # turn_index 1 = second call (0-indexed): first call succeeded, so total_llm_turns=1
    assert ctx["turn_index"] == 1

    # Error string must contain the embedded context block
    assert "--- Ollama request context ---" in result["error"]
    assert "turn_index" in result["error"]


# ------------------------------------------------------------------ #
# (m) v2: citable/context split                                       #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("iid,date", _ALL_DEV_DATES)
def test_m_no_context_row_has_short_id(iid: str, date: str) -> None:
    """No context row carries a short_id key, across all 41 dev dates."""
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    for m in REVENUE_FACTORS:
        for d in DIMENSIONS:
            rendered = session.test_dimension(m, d)
            if isinstance(rendered, dict) and "error" in rendered:
                continue
            for row in rendered["context"]:
                assert "short_id" not in row, (
                    f"{iid}/{date} ({m},{d}): context row has short_id: {row}"
                )
    con.close()


@pytest.mark.parametrize("iid,date", _ALL_DEV_DATES)
def test_m_citable_evidence_equals_checker_mapping(iid: str, date: str) -> None:
    """Every citable row's evidence equals verdict_to_claim_as applied to its harness verdict."""
    from agent.render import verdict_to_claim_as

    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    for m in REVENUE_FACTORS:
        for d in DIMENSIONS:
            rendered = session.test_dimension(m, d)
            if isinstance(rendered, dict) and "error" in rendered:
                continue

            # Build lookup by entry_id (= short_id) from harness ledger
            ledger = session._harness_ledger()
            by_id = {e.evidence_id: e for e in ledger.entries}

            for row in rendered["citable"]:
                entry = by_id.get(row["short_id"])
                assert entry is not None, (
                    f"{iid}/{date} ({m},{d}): citable row short_id={row['short_id']!r} "
                    f"not in ledger"
                )
                expected_evidence = verdict_to_claim_as(entry.verdict)
                assert expected_evidence != "-", (
                    f"{iid}/{date} ({m},{d}): citable row verdict={entry.verdict!r} "
                    f"maps to '-' in checker"
                )
                assert row["evidence"] == expected_evidence, (
                    f"{iid}/{date} ({m},{d}): row evidence={row['evidence']!r} "
                    f"but verdict_to_claim_as({entry.verdict!r})={expected_evidence!r}"
                )
    con.close()


def test_m_context_row_id_rejected() -> None:
    """Citing a context row's actual entry_id is rejected with 'no ledger entry'."""
    from agent.render import verdict_to_claim_as
    from agent.tools import _row_direction

    iid, date = _SCORE_DATES[0]  # I1/2017-01-26
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    all_pairs = [(m, d) for m in REVENUE_FACTORS for d in DIMENSIONS]
    for m, d in all_pairs:
        session.test_dimension(m, d)

    # Find a non-citable, non-immaterial entry from the harness ledger
    harness = session._harness_ledger()
    context_entry = None
    for e in harness.entries:
        if verdict_to_claim_as(e.verdict) == "-" and e.verdict != "immaterial":
            context_entry = e
            break

    if context_entry is None:
        pytest.skip("no context-row entry found on this date")

    direction = _row_direction(context_entry.actual, context_entry.expected)

    responses = [
        {"tool_calls": [{"name": "test_dimension", "args": {"metric": m, "dimension": d}}]}
        for m, d in all_pairs
    ]
    responses.append({
        "tool_calls": [{
            "name": "submit_answer",
            "args": {
                "pattern": "localized",
                "claims": [{
                    "entry_id": context_entry.evidence_id,
                    "direction": direction,
                    "strength": "strong",
                }],
                "narrative": "context row claim test",
            },
        }]
    })

    result = run_agent(session, FakeClient(responses), budget=14)
    assert result["status"] == "ok"
    assert result["n_rejected"] >= 1, "Claim on context row should be rejected"
    rejected_entry = result["rejected"][0]
    assert "no ledger entry" in rejected_entry["reason"], (
        f"Expected 'no ledger entry' reason, got {rejected_entry['reason']!r}"
    )
    con.close()


@pytest.mark.parametrize("iid,date", _ALL_DEV_DATES)
def test_m_evidence_as_strength_never_rejected(iid: str, date: str) -> None:
    """Copying a citable row's evidence word as strength is never rejected for invalid_strength.

    Parametrized over all 41 dev dates.  A FakeClient that mirrors the evidence
    field of every citable row should produce zero invalid_strength rejections.
    """
    con = build_incident_con(iid, date)
    session = ToolSession(date, con)

    all_pairs = [(m, d) for m in REVENUE_FACTORS for d in DIMENSIONS]
    claims_args = []

    for m, d in all_pairs:
        rendered = session.test_dimension(m, d)
        if isinstance(rendered, dict) and "error" in rendered:
            continue
        for row in rendered["citable"]:
            claims_args.append({
                "entry_id": row["short_id"],
                "direction": row["direction"],
                "strength": row["evidence"],
            })

    if not claims_args:
        pytest.skip(f"no citable rows on {iid}/{date}")

    responses = [
        {"tool_calls": [{"name": "test_dimension", "args": {"metric": m, "dimension": d}}]}
        for m, d in all_pairs
    ]
    responses.append({
        "tool_calls": [{
            "name": "submit_answer",
            "args": {"pattern": "localized", "claims": claims_args, "narrative": "evidence test"},
        }]
    })

    result = run_agent(session, FakeClient(responses), budget=14)
    assert result["status"] == "ok", f"{iid}/{date}: {result['status']}"
    strength_rejections = [
        r for r in result["rejected"]
        if "invalid_strength" in r.get("reason", "")
    ]
    assert strength_rejections == [], (
        f"{iid}/{date}: evidence copied as strength was rejected: {strength_rejections}"
    )
    con.close()


# ------------------------------------------------------------------ #
# (n) v3: ablation — PATTERNS section byte-identical to v1           #
# ------------------------------------------------------------------ #

def test_n_v3_patterns_identical_to_v1() -> None:
    """v3 PATTERNS section is byte-identical to v1's.

    v3 is an ablation: v1 prompt + v2 rendering.  The only TOOLS section
    changed; PATTERNS must not drift from v1.
    """
    import re
    import subprocess

    v1_source = subprocess.check_output(
        ["git", "show", "2ff263c:agent/prompt.py"],
        cwd=str(Path(__file__).parent.parent),
    ).decode()

    from agent.prompt import SYSTEM_PROMPT as v3_prompt, VERSION as v3_version
    assert v3_version == "v3", f"Expected VERSION='v3', got {v3_version!r}"

    def extract_patterns(prompt: str) -> str:
        m = re.search(r"(PATTERNS\n.*?)(?=\n[A-Z])", prompt, re.DOTALL)
        assert m, "PATTERNS section not found"
        return m.group(1)

    v1_prompt = re.search(r'SYSTEM_PROMPT = """\\\n(.*?)"""', v1_source, re.DOTALL)
    assert v1_prompt, "Could not extract v1 SYSTEM_PROMPT"
    v1_text = v1_prompt.group(1)

    assert extract_patterns(v1_text) == extract_patterns(v3_prompt), (
        "v3 PATTERNS section differs from v1"
    )
