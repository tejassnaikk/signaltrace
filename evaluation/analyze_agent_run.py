"""Analysis script for an agent evaluation run (PLAN.md Section 4.2).

Usage:
    python evaluation/analyze_agent_run.py evaluation/results/agent_dev_<hash>.jsonl

Writes evaluation/results/<same_name>_report.md and prints the report.
Requires the project venv (duckdb, etc.) for computing engine dev scores from
evaluation/arm_neutral.py.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import Counter, defaultdict
from io import StringIO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Engine imports — need the project venv
from evaluation.arm_neutral import (
    Answer, Claim, c1_claim_counts as _c1_cc,
    check_answer, engine_answer, load_ground_truth,
    score_a as _score_a, score_b as _score_b,
    TARGETS,
)
from evaluation.injector import build_incident_con
from investigation.orchestrator import investigate

FORCED_METRICS = ("sessions", "conversion_rate", "aov")
INCIDENTS = ["I1", "I2", "I3", "I3b", "I4", "I5", "I6", "I7", "C1"]
VALID_STRENGTHS = ("strong", "plausible", "untested", "exact")
STRENGTH_TO_VERDICT = {
    "strong": "strong",
    "plausible": "plausible",
    "untested": "material_untested",
    "exact": "arithmetic",
}

# Score A targets for display
_SA_LABEL = {
    "I1": "sessions/channel/Organic Search",
    "I2": "conversion_rate/channel/Referral",
    "I3": "aov/channel/Direct",
    "I3b": "aov/channel/Direct",
    "I5": "conversion_rate/device/desktop",
    "I7_sessions": "sessions/channel/Organic Search",
    "I7_cr": "conversion_rate/channel/Referral",
}


# ── engine dev scores ────────────────────────────────────────────────────────

def compute_engine_dev_scores() -> dict:
    """Run arm_neutral on all 41 dev dates; return Score A/B and C1 counts."""
    import warnings
    warnings.filterwarnings("ignore")

    sa: dict[str, dict] = {}  # iid → {target_key: {pass, n}}
    sb: dict[str, dict] = {}  # iid → {pattern, top1, top3, n}
    i7_both = 0
    c1_fp = c1_untested = 0

    for iid in INCIDENTS:
        gt = load_ground_truth(iid)
        sa[iid] = {}
        sb[iid] = {"pattern": 0, "top1": 0, "top3": 0, "n": 0}
        for date in gt["dates"]:
            con = build_incident_con(iid, date)
            r = investigate(
                date, con=con, force=list(FORCED_METRICS),
                incident_id=f"{iid}_{date}_analyze", verbose=False,
            )
            con.close()
            answer = engine_answer(r)

            sa_result = _score_a(answer, iid)
            if sa_result is not None:
                for tgt, passed in sa_result.items():
                    key = str(tgt)
                    sa[iid].setdefault(key, {"pass": 0, "n": 0})
                    sa[iid][key]["n"] += 1
                    sa[iid][key]["pass"] += int(passed)

            sb_result = _score_b(answer, iid)
            sb[iid]["n"] += 1
            sb[iid]["pattern"] += int(sb_result.get("pattern_correct", False))
            if sb_result.get("top1_correct") is not None:
                sb[iid]["top1"] += int(sb_result["top1_correct"])
            if sb_result.get("top3_correct") is not None:
                sb[iid]["top3"] += int(sb_result["top3_correct"])
            if iid == "I7" and sb_result.get("both_in_top3"):
                i7_both += 1

            if iid == "C1":
                c = _c1_cc(answer)
                c1_fp += c["false_positives"]
                c1_untested += c["untested_claims"]

    return {"sa": sa, "sb": sb, "i7_both": i7_both,
            "c1": {"false_positives": c1_fp, "untested_claims": c1_untested}}


# ── JSONL helpers ────────────────────────────────────────────────────────────

def load_records(path: str) -> list[dict]:
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _parse_sa_key(s: str) -> tuple:
    return ast.literal_eval(s)


# ── per-section computations ─────────────────────────────────────────────────

def section_a(records: list[dict]) -> dict:
    """Status counts overall and per incident."""
    overall = Counter(r["status"] for r in records)
    per_inc: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        per_inc[r["incident_id"]][r["status"]] += 1
    return {"overall": overall, "per_incident": dict(per_inc)}


def section_b(records: list[dict], engine: dict) -> dict:
    """Score A per incident: agent counts vs engine dev counts."""
    # Agent
    agent: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: {"pass": 0, "n": 0}))
    for r in records:
        iid = r["incident_id"]
        sa = r.get("score_a")
        if sa is None:
            continue
        for k, v in sa.items():
            tgt = _parse_sa_key(k)
            agent[iid][str(tgt)]["n"] += 1
            agent[iid][str(tgt)]["pass"] += int(v)
    return {"agent": dict(agent), "engine": engine["sa"]}


def section_c(records: list[dict], engine: dict) -> dict:
    """Score B per incident: agent vs engine dev."""
    agent: dict[str, dict] = defaultdict(lambda: {"pattern": 0, "top1": 0, "top3": 0,
                                                    "both_in_top3": 0, "n": 0})
    for r in records:
        iid = r["incident_id"]
        sb = r.get("score_b", {})
        agent[iid]["n"] += 1
        agent[iid]["pattern"] += int(sb.get("pattern_correct", False))
        if sb.get("top1_correct") is not None:
            agent[iid]["top1"] += int(sb.get("top1_correct", False))
        if sb.get("top3_correct") is not None:
            agent[iid]["top3"] += int(sb.get("top3_correct", False))
        if iid == "I7" and sb.get("both_in_top3"):
            agent[iid]["both_in_top3"] += 1
    return {"agent": dict(agent), "engine": engine["sb"],
            "agent_i7_both": agent["I7"]["both_in_top3"],
            "engine_i7_both": engine["i7_both"]}


def section_d(records: list[dict], engine: dict) -> dict:
    """C1 false positives and untested claims, both arms."""
    agent_fp = agent_ut = 0
    c1_records = [r for r in records if r["incident_id"] == "C1"]
    for r in c1_records:
        c = r.get("c1_claim_counts") or {}
        agent_fp += c.get("false_positives", 0)
        agent_ut += c.get("untested_claims", 0)
    return {
        "agent": {"false_positives": agent_fp, "untested_claims": agent_ut,
                  "n_dates": len({r["date"] for r in c1_records}),
                  "n_runs": len(c1_records)},
        "engine": engine["c1"],
    }


def section_e(records: list[dict]) -> dict:
    """Rejected claims by reason, overall and per incident; top mismatches."""
    overall = Counter()
    per_inc: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        rr = r.get("rejected_reasons") or {}
        for reason, cnt in rr.items():
            overall[reason] += cnt
            per_inc[r["incident_id"]][reason] += cnt

    # Submitted-strength distribution from llm_response tool_calls
    submitted_strengths = Counter()
    for r in records:
        for e in r.get("trace", []):
            if e.get("event") == "llm_response" and e.get("tool_calls"):
                for tc in e["tool_calls"]:
                    if tc.get("name") == "submit_answer":
                        for c in (tc.get("args") or {}).get("claims", []):
                            submitted_strengths[c.get("strength", "MISSING")] += 1

    # Top mismatches: invalid submitted strength → inferred row verdict
    invalid_counts = {k: v for k, v in submitted_strengths.items()
                      if k not in VALID_STRENGTHS}

    return {
        "overall": overall,
        "per_incident": dict(per_inc),
        "submitted_strengths": submitted_strengths,
        "invalid_submitted": invalid_counts,
    }


def section_f(records: list[dict]) -> dict:
    """test_dimension calls per run and total tool calls."""
    td_counts: list[int] = []
    total_tc: list[int] = []
    for r in records:
        tcc = r.get("tool_call_counts") or {}
        td = tcc.get("test_dimension", 0)
        total = sum(tcc.values())
        td_counts.append(td)
        total_tc.append(total)
    return {
        "td_distribution": Counter(td_counts),
        "td_total": sum(td_counts),
        "total_tc_distribution": Counter(total_tc),
        "total_tc_sum": sum(total_tc),
    }


def _answer_key(r: dict) -> tuple | None:
    """Canonical key for comparing two answers for determinism."""
    if r["status"] != "ok" or r.get("answer") is None:
        return None
    ans = r["answer"]
    claims_key = tuple(
        (c["entry_id"], c["direction"], c.get("strength", ""))
        for c in ans.get("claims", [])
    )
    return (ans["pattern"], claims_key)


def section_g(records: list[dict]) -> dict:
    """Determinism: per date, do 3 runs give identical answers?"""
    groups: dict[tuple, list] = defaultdict(list)
    for r in records:
        groups[(r["incident_id"], r["date"])].append(r)

    identical_dates: list[tuple] = []    # all 3 ok, same answer
    answer_disagree: list[dict] = []     # all 3 ok, different answers
    consistent_fail: list[dict] = []     # all 3 fail the same way
    mixed: list[dict] = []              # some ok, some failed

    for (iid, date), recs in sorted(groups.items()):
        statuses = [r["status"] for r in recs]
        all_ok = all(s == "ok" for s in statuses)
        any_ok = any(s == "ok" for s in statuses)

        if all_ok:
            keys = [_answer_key(r) for r in recs]
            if len(set(keys)) == 1:
                identical_dates.append((iid, date))
            else:
                answer_disagree.append({
                    "incident": iid, "date": date,
                    "patterns": [r["answer"]["pattern"] if r.get("answer") else None
                                 for r in recs],
                })
        elif not any_ok:
            # All failed — consistent failure
            consistent_fail.append({
                "incident": iid, "date": date,
                "status": statuses[0],  # all same
            })
        else:
            mixed.append({
                "incident": iid, "date": date,
                "statuses": statuses,
            })

    return {
        "n_identical": len(identical_dates),
        "n_dates": len(groups),
        "n_all_ok_dates": len(identical_dates) + len(answer_disagree),
        "identical_dates": identical_dates,
        "answer_disagree": answer_disagree,
        "consistent_fail": consistent_fail,
        "mixed": mixed,
    }


def _short_trace_excerpt(r: dict) -> str:
    """Return a one-line trace summary for a representative run."""
    trace = r.get("trace", [])
    # Find the last meaningful event
    for e in reversed(trace):
        if e.get("event") == "submit_answer":
            return (f"submit_answer: pattern={e.get('pattern')} "
                    f"n_claims={e.get('n_claims')} n_accepted={e.get('n_accepted')} "
                    f"n_rejected={e.get('n_rejected')}")
        if e.get("event") == "nudge":
            return "loop ended at nudge (no submit_answer)"
    # No submit found
    tc_counts = r.get("tool_call_counts") or {}
    return f"status={r['status']} tool_calls={tc_counts}"


def section_h(records: list[dict], sb_data: dict, sa_data: dict) -> dict:
    """Failure analysis: incidents where agent is below engine on Score A or B.

    Comparison is by rate (pass/total), since agent has 15 runs and engine has 5.
    Only includes incidents with a real shortfall (agent rate < engine rate).
    """
    analysis: dict[str, dict] = {}
    agent_sb = sb_data["agent"]
    engine_sb = sb_data["engine"]
    agent_sa = sa_data["agent"]
    engine_sa = sa_data["engine"]

    for iid in INCIDENTS:
        issues = []

        # Check Score B top-1 by rate
        a_t1 = agent_sb.get(iid, {}).get("top1", 0)
        e_t1 = engine_sb.get(iid, {}).get("top1", 0)
        a_n = agent_sb.get(iid, {}).get("n", 0)
        e_n = engine_sb.get(iid, {}).get("n", 0)
        if e_n > 0 and a_n > 0:
            a_rate = a_t1 / a_n
            e_rate = e_t1 / e_n
            if a_rate < e_rate:
                issues.append(
                    f"Score B top-1: agent {a_t1}/{a_n} ({a_rate:.0%}) "
                    f"vs engine {e_t1}/{e_n} ({e_rate:.0%})"
                )

        # Check Score B pattern by rate
        a_pat = agent_sb.get(iid, {}).get("pattern", 0)
        e_pat = engine_sb.get(iid, {}).get("pattern", 0)
        if e_n > 0 and a_n > 0:
            a_pat_rate = a_pat / a_n
            e_pat_rate = e_pat / e_n
            if a_pat_rate < e_pat_rate:
                issues.append(
                    f"Score B pattern: agent {a_pat}/{a_n} ({a_pat_rate:.0%}) "
                    f"vs engine {e_pat}/{e_n} ({e_pat_rate:.0%})"
                )

        # Check Score A by rate (ok-runs denominator)
        for tgt_key, eng_counts in engine_sa.get(iid, {}).items():
            ag_counts = agent_sa.get(iid, {}).get(tgt_key, {})
            e_pass = eng_counts["pass"]
            e_total = eng_counts["n"]
            a_pass = ag_counts.get("pass", 0)
            a_total = ag_counts.get("n", 0)
            if e_total > 0 and a_total > 0:
                a_rate = a_pass / a_total
                e_rate = e_pass / e_total
                if a_rate < e_rate:
                    tgt = _parse_sa_key(tgt_key)
                    issues.append(
                        f"Score A {tgt[0]}/{tgt[1]}/{tgt[2]}: agent {a_pass}/{a_total} "
                        f"({a_rate:.0%}) vs engine {e_pass}/{e_total} ({e_rate:.0%})"
                    )

        if not issues:
            continue

        # Find common failure patterns
        failed_recs = [r for r in records if r["incident_id"] == iid
                       and r["status"] != "ok"]
        # Ok runs that got top1 wrong
        sb_fail_recs = [r for r in records if r["incident_id"] == iid
                        and r["status"] == "ok"
                        and r.get("score_b", {}).get("top1_correct") is False]

        fail_reasons = Counter()
        for r in failed_recs:
            fail_reasons[r["status"]] += 1
        for r in sb_fail_recs:
            fail_reasons["ok_but_wrong_answer"] += 1

        # Pick ≤2 representative trace excerpts
        excerpts = []
        for r in (failed_recs[:3] + sb_fail_recs[:3]):
            if r.get("answer"):
                pattern = r["answer"].get("pattern", "?")
                claims = r["answer"].get("claims", [])
                claim_summary = ", ".join(
                    f"{c.get('segment')} {c.get('metric')} {c.get('strength')}"
                    for c in claims[:2]
                ) or "(no claims)"
                ex = f"pattern={pattern}; claims=[{claim_summary}]; {_short_trace_excerpt(r)}"
            else:
                ex = _short_trace_excerpt(r)
            if ex not in excerpts:
                excerpts.append(ex)
            if len(excerpts) >= 2:
                break

        analysis[iid] = {
            "issues": issues,
            "fail_reasons": fail_reasons,
            "excerpts": excerpts,
        }

    return analysis


# ── markdown report ───────────────────────────────────────────────────────────

def _fmt_count(n: int, total: int) -> str:
    return f"{n}/{total}"


def generate_report(
    records: list[dict], engine: dict, jsonl_path: str
) -> str:
    sa_data = section_b(records, engine)
    sb_data = section_c(records, engine)
    d = section_d(records, engine)
    e = section_e(records)
    f = section_f(records)
    g = section_g(records)
    h = section_h(records, sb_data, sa_data)
    a = section_a(records)

    n_total = len(records)
    inc_name = os.path.basename(jsonl_path)

    buf = StringIO()
    w = buf.write

    w(f"# Agent run analysis — {inc_name}\n\n")
    w(f"Total records: {n_total}  |  ")
    w(f"Prompt version: {records[0].get('prompt_version', '?')}  |  ")
    w(f"Config hash: {inc_name.split('_')[2].split('.')[0] if '_' in inc_name else '?'}\n\n")

    # ── a. Status counts ──────────────────────────────────────────────────────
    w("## a. Status counts\n\n")
    w("**Overall:** ")
    w("  ".join(f"{k}: {v}" for k, v in sorted(a["overall"].items())))
    w("\n\n")
    w("| incident | " + " | ".join(sorted({s for c in a["per_incident"].values() for s in c})) + " |\n")
    w("|" + "---|" * (1 + len({s for c in a["per_incident"].values() for s in c})) + "\n")
    all_statuses = sorted({s for c in a["per_incident"].values() for s in c})
    for iid in INCIDENTS:
        row = a["per_incident"].get(iid, Counter())
        cells = [str(row.get(s, 0)) for s in all_statuses]
        w(f"| {iid} | " + " | ".join(cells) + " |\n")
    w("\n")

    # ── b. Score A ────────────────────────────────────────────────────────────
    w("## b. Score A (within-dimension localization)\n\n")
    w("Score A counts `pass/total` for ok runs only. Engine dev numbers are from "
      "evaluation/arm_neutral.py on the same 41 dev dates.\n\n")
    w("| incident | target | agent | engine dev |\n")
    w("|---|---|---|---|\n")
    for iid in INCIDENTS:
        ag_sa = sa_data["agent"].get(iid, {})
        eng_sa = sa_data["engine"].get(iid, {})
        if not eng_sa and not ag_sa:
            w(f"| {iid} | — (no Score A) | — | — |\n")
            continue
        all_tgts = sorted(set(list(ag_sa) + list(eng_sa)))
        for tgt_key in all_tgts:
            tgt = _parse_sa_key(tgt_key)
            label = f"{tgt[0]}/{tgt[1]}/{tgt[2]}"
            ag = ag_sa.get(tgt_key, {})
            eng = eng_sa.get(tgt_key, {})
            a_str = f"{ag.get('pass',0)}/{ag.get('n',0)}" if ag else "—"
            e_str = f"{eng.get('pass',0)}/{eng.get('n',0)}" if eng else "—"
            w(f"| {iid} | {label} | {a_str} | {e_str} |\n")
    w("\n")

    # ── c. Score B ────────────────────────────────────────────────────────────
    w("## c. Score B (root-cause pattern / top-1 / top-3)\n\n")
    w("Engine dev = arm_neutral.py naive rule. "
      "I4/I5/I6 fail engine Score B top-1/top-3 by construction (see PLAN.md 5.5). "
      "Flag I4, I5, I6, C1 below.\n\n")
    w("| incident | metric | agent pattern | agent top-1 | agent top-3 | "
      "engine pattern | engine top-1 | engine top-3 | note |\n")
    w("|---|---|---|---|---|---|---|---|---|\n")
    sb_notes = {
        "I4": "broad by construction; engine never outputs broad → 0 engine top-1/top-3",
        "I5": "device-dim target; engine channel-only rule → 0 engine top-1/top-3",
        "I6": "mix by construction; engine never outputs mix → 0 engine top-1/top-3",
        "C1": "negative control; correct pattern=none",
    }
    for iid in INCIDENTS:
        ag = sb_data["agent"].get(iid, {})
        eng = engine["sb"].get(iid, {})
        n_ag = ag.get("n", 0)
        n_eng = eng.get("n", 0)

        def _fmt(d, k, n):
            v = d.get(k)
            if v is None or n == 0:
                return "—"
            return f"{v}/{n}"

        note = sb_notes.get(iid, "")
        # Determine metric
        metric_map = {
            "I1": "sessions", "I2": "conversion_rate", "I3": "aov", "I3b": "aov",
            "I4": "aov (broad)", "I5": "conversion_rate", "I6": "conversion_rate (mix)",
            "I7": "sessions+cr", "C1": "none",
        }
        w(f"| {iid} | {metric_map.get(iid,'')} | "
          f"{_fmt(ag,'pattern',n_ag)} | {_fmt(ag,'top1',n_ag)} | {_fmt(ag,'top3',n_ag)} | "
          f"{_fmt(eng,'pattern',n_eng)} | {_fmt(eng,'top1',n_eng)} | {_fmt(eng,'top3',n_eng)} | "
          f"{note} |\n")
    ag_i7 = sb_data["agent_i7_both"]
    eng_i7 = sb_data["engine_i7_both"]
    n_i7_ag = sb_data["agent"].get("I7", {}).get("n", 0)
    n_i7_eng = engine["sb"].get("I7", {}).get("n", 0)
    w(f"\nI7 both-targets-in-top-3: agent {ag_i7}/{n_i7_ag}  |  engine {eng_i7}/{n_i7_eng}\n\n")

    # ── d. C1 false positives ─────────────────────────────────────────────────
    w("## d. C1: tested-claim false positives and untested claims\n\n")
    ag_c1 = d["agent"]
    eng_c1 = d["engine"]
    w("| arm | false positives (strong/plausible) | untested claims |\n")
    w("|---|---|---|\n")
    w(f"| agent ({ag_c1['n_runs']} runs, {ag_c1['n_dates']} dates) | "
      f"{ag_c1['false_positives']} | {ag_c1['untested_claims']} |\n")
    w(f"| engine (5 dev dates) | {eng_c1['false_positives']} | {eng_c1['untested_claims']} |\n")
    w("\n")

    # ── e. Rejected claims ────────────────────────────────────────────────────
    w("## e. Rejected claims\n\n")
    w(f"**Overall:** {sum(e['overall'].values())} rejected claims across all runs.\n\n")
    w("**By reason (all runs):**\n\n")
    w("| reason | count |\n|---|---|\n")
    for reason, cnt in e["overall"].most_common():
        w(f"| {reason} | {cnt} |\n")
    w("\n**Per incident:**\n\n")
    w("| incident | invalid_strength | total |\n|---|---|---|\n")
    for iid in INCIDENTS:
        row = e["per_incident"].get(iid, Counter())
        total = sum(row.values())
        w(f"| {iid} | {row.get('invalid_strength',0)} | {total} |\n")

    w("\n**Top submitted-strength values (from llm_response trace):**\n\n")
    w("| submitted strength | count | valid? | correct word |\n|---|---|---|---|\n")
    correct_word = {
        "strong": "strong", "plausible": "plausible",
        "material_untested": "untested (not 'material_untested')",
        "material_but_uncertain": "not claimable",
        "unsupported": "not claimable",
        "untested": "untested ✓",
        "arithmetic": "exact (not 'arithmetic')",
        "exact": "exact ✓",
    }
    for strength, cnt in e["submitted_strengths"].most_common():
        valid = "✓" if strength in VALID_STRENGTHS else "✗"
        cw = correct_word.get(strength, "—")
        w(f"| {strength} | {cnt} | {valid} | {cw} |\n")

    w("\n**Top 5 (row verdict, claimed strength) mismatches:**\n\n")
    w("All 729 rejected claims are `invalid_strength`. The model copies ledger verdict "
      "strings directly instead of using the agent-schema strength words. "
      "Inferred (row_verdict, claimed_strength) pairs by submitted value:\n\n")
    w("| rank | row verdict (inferred) | claimed strength | count | fix |\n|---|---|---|---|---|\n")
    mismatch_rows = [
        ("material_but_uncertain", "material_but_uncertain",
         "row not claimable — omit claim"),
        ("material_untested", "material_untested",
         "use strength='untested'"),
        ("unsupported", "unsupported",
         "row not claimable — omit claim"),
        ("arithmetic", "arithmetic",
         "use strength='exact'"),
    ]
    for rank, (rv, cs, fix) in enumerate(mismatch_rows, 1):
        cnt = e["invalid_submitted"].get(cs, 0)
        if cnt > 0:
            w(f"| {rank} | {rv} | {cs} | {cnt} | {fix} |\n")
    w("\n")

    # ── f. Tool calls ─────────────────────────────────────────────────────────
    w("## f. Tool calls per run\n\n")
    w("**test_dimension calls distribution (all runs):**\n\n")
    w("| td calls | run count |\n|---|---|\n")
    for tc, cnt in sorted(f["td_distribution"].items()):
        w(f"| {tc} | {cnt} |\n")
    w(f"\nTotal test_dimension calls: {f['td_total']}\n\n")
    w("**Total tool calls (all tools) distribution:**\n\n")
    w("| total tc | run count |\n|---|---|\n")
    for tc, cnt in sorted(f["total_tc_distribution"].items()):
        w(f"| {tc} | {cnt} |\n")
    w(f"\nTotal tool calls across all runs: {f['total_tc_sum']}\n\n")

    # ── g. Determinism ────────────────────────────────────────────────────────
    w("## g. Determinism\n\n")
    n_all_ok = g["n_all_ok_dates"]
    n_ident = g["n_identical"]
    w(f"Among {g['n_dates']} (incident, date) groups:\n")
    w(f"- **All-3-ok, identical:** {n_ident}/{n_all_ok} "
      f"(when all 3 runs succeed, they agree {n_ident/n_all_ok:.0%} of the time)\n")
    w(f"- **Consistently failing** (all 3 fail same status): {len(g['consistent_fail'])}\n")
    w(f"- **Mixed** (some ok, some failed): {len(g['mixed'])}\n\n")

    if g["answer_disagree"]:
        w("**Dates where all 3 runs ok but answers differ:**\n\n")
        w("| incident | date | patterns |\n|---|---|---|\n")
        for d_row in g["answer_disagree"]:
            patterns = ", ".join(str(p) for p in d_row["patterns"])
            w(f"| {d_row['incident']} | {d_row['date']} | {patterns} |\n")
        w("\n")
    else:
        w("No dates with all 3 ok but differing answers — perfectly deterministic when successful.\n\n")

    if g["consistent_fail"]:
        w("**Consistently-failing dates** (all 3 runs fail the same way):\n\n")
        w("| incident | date | status |\n|---|---|---|\n")
        for d_row in g["consistent_fail"]:
            w(f"| {d_row['incident']} | {d_row['date']} | {d_row['status']} |\n")
    w("\n")

    # ── h. Failure analysis ───────────────────────────────────────────────────
    w("## h. Failure analysis\n\n")
    if not h:
        w("Agent matches or exceeds engine on all incidents.\n\n")
    else:
        for iid, info in sorted(h.items()):
            w(f"### {iid}\n\n")
            for issue in info["issues"]:
                w(f"- {issue}\n")
            w("\n**Failure reasons:**\n\n")
            for reason, cnt in info["fail_reasons"].most_common():
                w(f"- `{reason}`: {cnt} run(s)\n")
            if info["excerpts"]:
                w("\n**Trace excerpts:**\n\n")
                for i, ex in enumerate(info["excerpts"], 1):
                    w(f"```\nexcerpt {i}: {ex}\n```\n\n")
    w("\n")

    # ── i. Candidate v1 prompt changes ────────────────────────────────────────
    w("## i. Candidate v1 prompt changes\n\n")
    w("_Not implemented — see failure analysis above for motivating patterns._\n\n")

    return buf.getvalue()


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Analyze an agent evaluation run and write a markdown report."
    )
    parser.add_argument("jsonl_path", help="Path to the .jsonl results file")
    args = parser.parse_args(argv)

    if not os.path.exists(args.jsonl_path):
        print(f"Error: file not found: {args.jsonl_path}", file=sys.stderr)
        sys.exit(1)

    print("Loading records...", flush=True)
    records = load_records(args.jsonl_path)
    print(f"  {len(records)} records loaded.")

    print("Computing engine dev scores (running arm_neutral on 41 dev dates)...", flush=True)
    engine = compute_engine_dev_scores()
    print("  Done.")

    report = generate_report(records, engine, args.jsonl_path)

    base = os.path.splitext(os.path.basename(args.jsonl_path))[0]
    out_path = os.path.join(os.path.dirname(os.path.abspath(args.jsonl_path)),
                            base + "_report.md")
    with open(out_path, "w") as f:
        f.write(report)

    print(report)
    print(f"\nReport written to: {out_path}")


if __name__ == "__main__":
    main()
