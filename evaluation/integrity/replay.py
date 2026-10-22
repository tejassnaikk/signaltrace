"""Integrity replay for agent result files (M7 step 0).

What it checks: for every claim the checker rejected as `unknown_entry` or
`direction_mismatch`, was the cited id (A) absent from the harness ledger,
(B) shown to the model on a non-claimable row (claim_as '-'), (C) in the
ledger but never shown, or (D) shown as a claimable row yet rejected
(harness mismatch, which would invalidate a held-out run, PLAN 4.2)? It also
confirms the direction shown to the model equals the checker's definition,
and that replayed rejection counts equal the stored counts.

Method: the deterministic tool layer only (ToolSession + the frozen
_check_claims) is replayed from each run's recorded tool calls and submitted
claims. No model call, no agent re-run.

Inputs: one or more result .jsonl files. Files with "heldout" in the name use
the held-out injection path; others use the dev path. Session split is by
record order (--split-at N, default none).

Run from the repo root:
    python -I evaluation/integrity/replay.py \\
        evaluation/results/agent_heldout_f854dee9.jsonl --split-at 194
    python -I evaluation/integrity/replay.py evaluation/results/agent_dev_f854dee9.jsonl
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

from agent.loop import _check_claims, _dispatch  # noqa: E402
from agent.tools import ToolSession, _row_direction  # noqa: E402
from agent.versions import get_version  # noqa: E402
from evaluation.injector import build_incident_con, build_incident_con_any_date  # noqa: E402
from evaluation.run_agent import _classify_rejection_reason  # noqa: E402

CATS = {
    "A": "absent from ledger",
    "B": "shown, non-claimable (claim_as '-')",
    "C": "in ledger, never shown",
    "D": "shown claimable but rejected (HARNESS MISMATCH)",
}


def _cat(x: dict) -> str:
    if not x["in_ledger"]:
        return "A"
    if x["shown"] and x["claim_as"] == "-":
        return "B"
    if x["shown"]:
        return "D"
    return "C"


def replay_file(path: str) -> list[dict]:
    heldout = "heldout" in os.path.basename(path)
    cfg = get_version("v1")
    out = []
    with open(path) as f:
        recs = [json.loads(l) for l in f if l.strip()]
    for idx, r in enumerate(recs):
        build = build_incident_con_any_date if heldout else build_incident_con
        con = build(r["incident_id"], r["date"])
        s = ToolSession(r["date"], con, render_fn=cfg.render_test_dimension,
                        render_ledger_fn=cfg.render_ledger_summary)
        shown, shown_dir = {}, {}
        for e in r["trace"]:
            if e["event"] == "tool_call":
                res = _dispatch(e["name"], e["args"], s)
                if e["name"] == "test_dimension" and isinstance(res, list):
                    for row in res:
                        if "entry_id" in row:
                            shown[row["entry_id"]] = row.get("claim_as")
                            shown_dir[row["entry_id"]] = row.get("direction")
        sub = None
        for e in r["trace"]:
            if e["event"] == "llm_response":
                for tc in e["tool_calls"]:
                    if tc["name"] == "submit_answer":
                        sub = tc["args"]
        led = s._harness_ledger()
        _, rej = (_check_claims(sub["claims"], led, cfg.strength_to_verdict,
                                cfg.verdict_to_claim_as) if sub else ([], []))
        by = {e.evidence_id: e for e in led.entries}
        items = []
        for c in rej:
            eid = c.get("entry_id")
            ent = by.get(eid)
            items.append(dict(
                cls=_classify_rejection_reason(c["reason"]), entry_id=eid,
                in_ledger=ent is not None, shown=eid in shown, claim_as=shown.get(eid),
                verdict=ent.verdict if ent else None, shown_dir=shown_dir.get(eid),
                claimed_dir=c.get("direction"),
                checker_dir=_row_direction(ent.actual, ent.expected) if ent else None,
                strength=c.get("strength")))
        out.append(dict(idx=idx, iid=r["incident_id"], date=r["date"], run=r["run"],
                        stored=r["rejected_reasons"],
                        replay=dict(collections.Counter(i["cls"] for i in items)),
                        rej=items))
        con.close()
    return out


def summarize(name: str, runs: list[dict]) -> bool:
    ue = [x for o in runs for x in o["rej"] if x["cls"] == "unknown_entry"]
    dm = [x for o in runs for x in o["rej"] if x["cls"] == "direction_mismatch"]
    c = collections.Counter(_cat(x) for x in ue)
    n = len(runs)
    print(f"\n[{name}] runs={n}  unknown_entry={len(ue)} ({len(ue)/n:.2f}/run, "
          f"{sum(1 for o in runs if any(x['cls']=='unknown_entry' for x in o['rej']))} runs)  "
          + "  ".join(f"{k}={c.get(k,0)}" for k in CATS))
    by_inc = collections.defaultdict(collections.Counter)
    for o in runs:
        for x in o["rej"]:
            if x["cls"] == "unknown_entry":
                by_inc[o["iid"]][_cat(x)] += 1
    print("  by incident:", {k: dict(v) for k, v in sorted(by_inc.items())})
    print("  verdicts of cited rows:", dict(collections.Counter(x["verdict"] for x in ue)))
    print(f"  direction_mismatch={len(dm)}; shown dir != checker dir in "
          f"{sum(1 for o in runs for x in o['rej'] if x['shown_dir'] and x['checker_dir'] and x['shown_dir']!=x['checker_dir'])} cited rows")
    bad = [o for o in runs if o["stored"] != o["replay"]]
    tot_bad = [o for o in runs if sum(o["stored"].values()) != sum(o["replay"].values())]
    print(f"  replay vs stored: label differences in {len(bad)} runs, "
          f"total-count differences in {len(tot_bad)} runs")
    return c.get("D", 0) == 0 and not tot_bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--split-at", type=int, default=None,
                    help="report records [:N] and [N:] separately (session split)")
    a = ap.parse_args(argv)
    ok = True
    for p in a.paths:
        runs = replay_file(p)
        base = os.path.basename(p)
        if a.split_at:
            ok &= summarize(base + " [session 1]", runs[:a.split_at])
            ok &= summarize(base + " [session 2]", runs[a.split_at:])
        ok &= summarize(base + " [all]", runs)
    print("\nINTEGRITY:", "PASS (0 harness-mismatch claims)" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
