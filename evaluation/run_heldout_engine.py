"""Run the frozen engine (tag `engine-frozen-m5`) on the frozen held-out
set (`evaluation/heldout/heldout_dates.json`), exactly once (PLAN.md 5.6).

Refuses to run unless the held-out file's SHA-256 matches the value
written into PLAN.md 5.4 at freeze time - a changed file is a reason to
stop, not to proceed.

**`build_incident_con` bypass, deliberate, scoped to this script only.**
`evaluation.injector.build_incident_con` refuses any date not listed in
that incident's `evaluation/ground_truth/<id>.json` - a safety rail that
exists everywhere else in this project specifically to stop a held-out
(or any unregistered) date from being tested by accident. This script's
entire purpose is to test held-out dates, once, deliberately - so it uses
a local copy of the same injection logic with that one check removed,
rather than weakening the real guard that protects every other call site.
`evaluation/ground_truth/*.json` is never touched; held-out dates are
never added to it.

`evaluation.score.SCORERS` is also run here, for the side-by-side
comparison PLAN.md 5.6 asks for. `evaluation/score.py` imports
`build_incident_con` by name (`from evaluation.injector import
build_incident_con`), so its internal `_run()` needs `evaluation.score`'s
OWN bound name patched - patching `evaluation.injector.build_incident_con`
alone would not reach it. The patch is scoped to this script's run and
restored unconditionally afterward; no file on disk is modified.

On any exception: stop immediately, print the incident id and the full
traceback (never the date - the whole point of a held-out set is that it
isn't inspected date-by-date), and exit non-zero. No retry, no silent
work-around.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
import traceback
from dataclasses import asdict

import duckdb
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.abspath(__file__))
HELDOUT_PATH = os.path.join(ROOT, "heldout", "heldout_dates.json")
RESULTS_PATH = os.path.join(ROOT, "results", "heldout_engine.json")
EXPECTED_HELDOUT_SHA256 = "82012176af32e9677f0a08af33243166b972c14adf15cfb11dc539f1e3ccfe54"
INCIDENTS = ["I1", "I2", "I3", "I3b", "I4", "I5", "I6", "I7", "C1"]


def _verify_heldout_sha256() -> str:
    with open(HELDOUT_PATH, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if digest != EXPECTED_HELDOUT_SHA256:
        raise RuntimeError(
            f"evaluation/heldout/heldout_dates.json SHA-256 mismatch: got "
            f"{digest}, expected {EXPECTED_HELDOUT_SHA256}. Refusing to run "
            f"- the frozen held-out set may have changed since it was frozen."
        )
    return digest


def _build_con_any_date(incident_id: str, date) -> duckdb.DuckDBPyConnection:
    """Delegates to evaluation.injector.build_incident_con_any_date — the
    single shared implementation for both held-out callers (this script and
    evaluation/run_agent.py --split heldout)."""
    from evaluation.injector import build_incident_con_any_date
    return build_incident_con_any_date(incident_id, date)


@contextlib.contextmanager
def _patched_scorers_build_incident_con():
    import evaluation.score as score_module
    original = score_module.build_incident_con
    score_module.build_incident_con = _build_con_any_date
    try:
        yield
    finally:
        score_module.build_incident_con = original


def main():
    heldout_sha = _verify_heldout_sha256()
    heldout = json.load(open(HELDOUT_PATH))
    assert heldout["seed"] == 20261001 and heldout["k"] == 9, "unexpected held-out file contents"

    from evaluation.arm_neutral import (FORCED_METRICS, check_answer, c1_claim_counts,
                                        engine_answer, score_a, score_b)
    from evaluation.injector import load_ground_truth
    from evaluation.score import _NO_RESULTS_DETAIL, SCORERS
    from investigation.orchestrator import investigate

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()

    per_date_records = []
    agg = {
        "score_a": {},   # incident -> {(metric,dimension,segment): {"pass":n,"n":n}}
        "score_b": {iid: {"n": 0, "pattern": 0, "top1": 0, "top3": 0} for iid in INCIDENTS},
        "i7_both_top3": 0,
        "existing_scorer": {iid: {"pass": 0, "n": 0} for iid in INCIDENTS if iid != "C1"},
        "no_results": {iid: 0 for iid in INCIDENTS},
        "c1": {"false_positives": 0, "untested_claims": 0},
        "checker": {"n_claims": 0, "n_rejected": 0},
    }

    with _patched_scorers_build_incident_con():
        for iid in INCIDENTS:
            gt = load_ground_truth(iid)
            dates = heldout["incidents"][iid]
            for date in dates:
                try:
                    con = _build_con_any_date(iid, date)
                    r = investigate(date, con=con, force=list(FORCED_METRICS),
                                    incident_id=f"{iid}_heldout_{date}", verbose=False)
                    con.close()

                    answer = engine_answer(r)
                    chk = check_answer(answer, r["ledger"])
                    sa = score_a(answer, iid)
                    sb = score_b(answer, iid)
                    existing = SCORERS[iid](gt, date)
                    c1c = c1_claim_counts(answer) if iid == "C1" else None
                    is_no_results = existing.detail == _NO_RESULTS_DETAIL

                    # aggregate
                    agg["checker"]["n_claims"] += chk["n_claims"]
                    agg["checker"]["n_rejected"] += chk["n_rejected"]
                    if is_no_results:
                        agg["no_results"][iid] += 1
                    if sa is not None:
                        agg["score_a"].setdefault(iid, {})
                        for target, passed in sa.items():
                            agg["score_a"][iid].setdefault(target, {"pass": 0, "n": 0})
                            agg["score_a"][iid][target]["n"] += 1
                            agg["score_a"][iid][target]["pass"] += int(passed)
                    agg["score_b"][iid]["n"] += 1
                    agg["score_b"][iid]["pattern"] += int(sb["pattern_correct"])
                    if sb["top1_correct"] is not None:
                        agg["score_b"][iid]["top1"] += int(sb["top1_correct"])
                    if sb["top3_correct"] is not None:
                        agg["score_b"][iid]["top3"] += int(sb["top3_correct"])
                    if iid == "I7" and sb.get("both_in_top3"):
                        agg["i7_both_top3"] += 1
                    if iid != "C1" and existing.passed is not None:
                        agg["existing_scorer"][iid]["n"] += 1
                        agg["existing_scorer"][iid]["pass"] += int(existing.passed)
                    if c1c is not None:
                        agg["c1"]["false_positives"] += c1c["false_positives"]
                        agg["c1"]["untested_claims"] += c1c["untested_claims"]

                    per_date_records.append({
                        "incident": iid,
                        "date": date,
                        "answer": {
                            "pattern": answer.pattern,
                            "claims": [asdict(c) for c in answer.claims],
                            "primary": [asdict(c) for c in answer.primary],
                        },
                        "checker": {"n_claims": chk["n_claims"], "n_rejected": chk["n_rejected"]},
                        "score_a": ([{"metric": m, "dimension": d, "segment": s, "passed": p}
                                    for (m, d, s), p in sa.items()] if sa is not None else None),
                        "score_b": sb,
                        "c1_claim_counts": c1c,
                        "no_results": is_no_results,
                        "existing_scorer": {
                            "passed": existing.passed,
                            "detail": existing.detail,
                            "measurements": existing.measurements,
                        },
                        "ledger_entries": [asdict(e) for e in r["ledger"].entries],
                    })
                except Exception:
                    print(f"FATAL: exception while processing incident={iid} "
                         f"(date withheld). Stopping - not retrying, not working around it.",
                         file=sys.stderr)
                    traceback.print_exc()
                    sys.exit(1)

    # serialize score_a's tuple-keyed aggregate into a JSON-safe list form
    agg_score_a_json = {
        iid: [{"metric": m, "dimension": d, "segment": s, "pass": v["pass"], "n": v["n"]}
             for (m, d, s), v in targets.items()]
        for iid, targets in agg["score_a"].items()
    }

    payload = {
        "seed": heldout["seed"],
        "k": heldout["k"],
        "heldout_file": "evaluation/heldout/heldout_dates.json",
        "heldout_sha256": heldout_sha,
        "git_head_at_generation": head,
        "tag": "engine-frozen-m5",
        "forced_metrics": list(FORCED_METRICS),
        "aggregate": {
            "score_a": agg_score_a_json,
            "score_b": agg["score_b"],
            "i7_both_top3": agg["i7_both_top3"],
            "existing_scorer": agg["existing_scorer"],
            "no_results": agg["no_results"],
            "c1": agg["c1"],
            "checker": agg["checker"],
        },
        "records": per_date_records,
    }

    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str)
        f.write("\n")

    print(f"wrote {RESULTS_PATH}")
    print(f"records: {len(per_date_records)}")
    print(f"git_head={head}")
    print()
    print("=== AGGREGATE (counts only - no dates) ===")
    print("Score A:")
    for iid, targets in agg_score_a_json.items():
        for t in targets:
            print(f"  {iid} ({t['metric']}, {t['dimension']}, {t['segment']}): {t['pass']}/{t['n']}")
    print("\nExisting SCORERS (held-out):")
    for iid, c in agg["existing_scorer"].items():
        print(f"  {iid}: {c['pass']}/{c['n']}")
    print("\nScore B (pattern / top1 / top3):")
    for iid, c in agg["score_b"].items():
        print(f"  {iid}: {c['pattern']}/{c['n']}  {c['top1']}/{c['n']}  {c['top3']}/{c['n']}")
    print(f"\nI7 both-in-top-3: {agg['i7_both_top3']}/{agg['score_b']['I7']['n']}")
    print(f"\nC1: false_positives={agg['c1']['false_positives']} "
         f"untested_claims={agg['c1']['untested_claims']}")
    print(f"\nChecker: claims={agg['checker']['n_claims']} rejections={agg['checker']['n_rejected']}")
    print(f"\nno_results per incident: {agg['no_results']}")


if __name__ == "__main__":
    main()
