"""Resumable agent evaluation runner (M6, PLAN.md Section 4.1).

Usage:
    python -m evaluation.run_agent --split dev [--runs 3] [--limit N]
    python -m evaluation.run_agent --split heldout --i-confirm-heldout [--runs 3]

For --split heldout the script also requires a git tag named 'agent-frozen'
to exist on the current HEAD (or any ancestor) — refuses otherwise.

Output: evaluation/results/agent_<split>_<config_hash>.jsonl
Debug (--limit) output: evaluation/results/agent_<split>_<config_hash>_debug.jsonl
One JSON line per finished (incident, date, run), flushed/fsynced immediately.
On restart, lines already present are skipped (resumable).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from dataclasses import asdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.config import config as _agent_config, _verify_pinned_digest
from agent.llm import OllamaClient
from agent.loop import run_agent
from agent.versions import get_version, FROZEN_AGENT, FROZEN_THINK
from evaluation.arm_neutral import (
    Answer, Claim, c1_claim_counts, check_answer, engine_answer,
    load_ground_truth, score_a, score_b,
)
from evaluation.injector import build_incident_con
from agent.tools import ToolSession

INCIDENTS = ["I1", "I2", "I3", "I3b", "I4", "I5", "I6", "I7", "C1"]
RESULTS_DIR = os.path.join(ROOT, "evaluation", "results")


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _classify_rejection_reason(reason: str) -> str:
    if reason.startswith("invalid_strength"):
        return "invalid_strength"
    if reason.startswith("invalid_direction"):
        return "invalid_direction"
    if reason.startswith("no ledger entry"):
        return "unknown_entry"
    if "contradicts entry sign" in reason:
        return "direction_mismatch"
    if "requires verdict" in reason:
        return "verdict_mismatch"
    return "other"


def _build_out_path(results_dir: str, split: str, chash: str, limit) -> str:
    """Output path. --limit runs get a _debug suffix so they never touch the main file."""
    suffix = "_debug" if limit is not None else ""
    return os.path.join(results_dir, f"agent_{split}_{chash}{suffix}.jsonl")


def _config_hash(config_dict: dict, prompt_version: str) -> str:
    payload = json.dumps(
        {"config": config_dict, "prompt": prompt_version}, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


def _git_head() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=ROOT
        )
        return r.stdout.strip()
    except Exception:
        return "unknown"


def _agent_frozen_tag_exists() -> bool:
    try:
        r = subprocess.run(
            ["git", "tag"], capture_output=True, text=True, cwd=ROOT
        )
        return "agent-frozen" in r.stdout.split()
    except Exception:
        return False


def _load_done_keys(path: str) -> set[str]:
    done = set()
    if not os.path.exists(path):
        return done
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                key = f"{rec['incident_id']}_{rec['date']}_{rec['run']}"
                done.add(key)
            except Exception:
                pass
    return done


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


def _score_run(result: dict, iid: str, ledger) -> dict:
    """Score one run.  Failed runs fail every score."""
    status = result["status"]
    if status != "ok" or result["answer"] is None:
        # failed_* run: all scores fail
        return {
            "score_a": None,
            "score_b": {
                "pattern_correct": False,
                "top1_correct": False,
                "top3_correct": False,
            },
            "c1_claim_counts": {"false_positives": 0, "untested_claims": 0},
            "checker": {"n_claims": 0, "n_rejected": 0, "rejected": []},
            "failed_run": True,
        }

    answer = _dict_to_answer(result["answer"])

    sa = score_a(answer, iid)
    sb = score_b(answer, iid)
    c1 = c1_claim_counts(answer) if iid == "C1" else None
    chk = check_answer(answer, ledger)

    return {
        "score_a": {str(k): v for k, v in sa.items()} if sa is not None else None,
        "score_b": sb,
        "c1_claim_counts": c1,
        "checker": {
            "n_claims": chk["n_claims"],
            "n_rejected": chk["n_rejected"],
            "rejected": [str(r) for r in chk["rejected"]],
        },
        "failed_run": False,
    }


def _answer_to_dict(answer: Answer | None) -> dict | None:
    if answer is None:
        return None
    def _claim_to_dict(c):
        return {
            "metric": c.metric, "dimension": c.dimension, "segment": c.segment,
            "direction": c.direction, "kind": c.kind,
            "evidence_ids": c.evidence_ids, "strength": c.strength,
            "basis": c.basis,
        }
    return {
        "pattern": answer.pattern,
        "claims": [_claim_to_dict(c) for c in answer.claims],
        "primary": [_claim_to_dict(c) for c in answer.primary],
        "narrative": answer.narrative,
    }


# ------------------------------------------------------------------ #
# Main                                                                 #
# ------------------------------------------------------------------ #

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Resumable agent evaluation runner"
    )
    parser.add_argument("--split", choices=["dev", "heldout"], required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after this many (incident, date) pairs (for testing)")
    parser.add_argument("--i-confirm-heldout", action="store_true",
                        help="Required when --split heldout")
    parser.add_argument("--think", action="store_true",
                        help="Enable Qwen3 think mode; overrides THINK=False in config. "
                             "Produces a distinct config_hash from the think=False run.")
    parser.add_argument("--version", choices=["v1", "v4"], default="v4",
                        help="Agent version (prompt + rendering + strength mapping). "
                             "Default: v4.  Use v1 for the pre-registered think test.")
    args = parser.parse_args(argv)

    # Heldout guard
    if args.split == "heldout":
        if not args.i_confirm_heldout:
            print(
                "Error: --split heldout requires --i-confirm-heldout. "
                "Run on heldout data only when you have finished developing on dev.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not _agent_frozen_tag_exists():
            print(
                "Error: no 'agent-frozen' git tag found. "
                "Tag the commit (`git tag agent-frozen`) before running on held-out data.",
                file=sys.stderr,
            )
            sys.exit(1)
        # Enforce frozen configuration — no overrides permitted on held-out
        if args.version != FROZEN_AGENT.name:
            print(
                f"Error: --split heldout requires --version {FROZEN_AGENT.name} "
                f"(frozen agent). Passed --version {args.version!r}. "
                "Do not override the frozen configuration.",
                file=sys.stderr,
            )
            sys.exit(1)
        if args.think != FROZEN_THINK:
            print(
                "Error: --split heldout requires think=False (frozen agent). "
                "--think is not permitted on held-out runs.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Verify pinned digest at run start (fails loudly if model changed or Ollama down)
    _verify_pinned_digest()
    version_cfg = get_version(args.version)
    cfg = _agent_config()
    if args.think:
        # Override think=True and raise num_predict to 4096 (baseline 2048 is too
        # tight once thinking tokens count against the cap).  Context guard is
        # tightened to 11,500 (vs 13,000) so prompt + response always fit in 16,384.
        # Both overrides avoid touching the frozen agent/config.py module.
        new_options = {**cfg["options"], "num_predict": 4096}
        cfg = {**cfg, "think": True, "options": new_options}
    client = OllamaClient(cfg)
    chash = _config_hash(cfg, version_cfg.prompt_version)

    if args.split == "heldout":
        _FROZEN_HASH = "f854dee9"
        if chash != _FROZEN_HASH:
            print(
                f"Error: frozen config_hash should be {_FROZEN_HASH} but computed {chash}. "
                "The frozen configuration has drifted — do not run held-out.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Frozen config_hash: {chash}")

    out_path = _build_out_path(RESULTS_DIR, args.split, chash, args.limit)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    done_keys = _load_done_keys(out_path)
    git_head = _git_head()

    # Build work list
    work: list[tuple[str, str]] = []
    for iid in INCIDENTS:
        gt = load_ground_truth(iid)
        for date in gt["dates"]:
            work.append((iid, date))
    if args.limit is not None:
        work = work[: args.limit]

    total = len(work) * args.runs
    done_count = sum(
        1 for iid, date in work
        for run in range(args.runs)
        if f"{iid}_{date}_{run}" in done_keys
    )
    print(
        f"split={args.split}  config_hash={chash}  "
        f"out={os.path.relpath(out_path, ROOT)}"
    )
    print(f"work: {total} runs ({len(work)} dates × {args.runs}), "
          f"{done_count} already done")

    # Aggregate counters
    agg: dict[str, dict] = {
        iid: {"n": 0, "status_counts": {}, "sa_pass": {},
              "sb_pat": 0, "sb_t1": 0, "sb_t3": 0,
              "failed": 0, "checker_rejected": 0,
              "rejection_reasons": {}, "elapsed_times": []}
        for iid in INCIDENTS
    }

    consecutive_failed_error = 0
    with open(out_path, "a", buffering=1) as fh:
        for iid, date in work:
            gt = load_ground_truth(iid)
            for run in range(args.runs):
                key = f"{iid}_{date}_{run}"
                if key in done_keys:
                    continue

                print(f"  {iid} {date} run={run} ... ", end="", flush=True)

                t_start = time.time()
                try:
                    con = build_incident_con(iid, date)
                    session = ToolSession(
                        date, con,
                        render_fn=version_cfg.render_test_dimension,
                        render_ledger_fn=version_cfg.render_ledger_summary,
                    )
                    context_limit = 11500 if args.think else None
                    run_kw = {"version_cfg": version_cfg}
                    if context_limit is not None:
                        run_kw["context_limit"] = context_limit
                    result = run_agent(session, client, **run_kw)
                    ledger = session._harness_ledger()
                    scores = _score_run(result, iid, ledger)
                    con.close()
                except Exception:
                    result = {
                        "answer": None,
                        "trace": [],
                        "tool_call_counts": {},
                        "status": "failed_error",
                        "n_rejected": 0,
                        "rejected": [],
                        "error": traceback.format_exc(),
                        "error_context": None,
                    }
                    scores = _score_run(result, iid, None)
                elapsed_s = time.time() - t_start

                # Classify per-claim rejection reasons for record and aggregate
                rejected_reasons: dict[str, int] = {}
                for rj in result.get("rejected", []):
                    key = _classify_rejection_reason(rj.get("reason", ""))
                    rejected_reasons[key] = rejected_reasons.get(key, 0) + 1

                record = {
                    "incident_id": iid,
                    "date": str(date),
                    "run": run,
                    "status": result["status"],
                    "answer": result["answer"],
                    "n_rejected": result["n_rejected"],
                    "rejected_reasons": rejected_reasons,
                    "tool_call_counts": result["tool_call_counts"],
                    "elapsed_s": round(elapsed_s, 2),
                    "trace": result.get("trace", []),
                    "error": result.get("error"),
                    "error_context": result.get("error_context"),
                    "score_a": scores["score_a"],
                    "score_b": scores["score_b"],
                    "c1_claim_counts": scores["c1_claim_counts"],
                    "checker": scores["checker"],
                    "failed_run": scores["failed_run"],
                    "config": cfg,
                    "prompt_version": version_cfg.prompt_version,
                    "git_head": git_head,
                }

                fh.write(json.dumps(record, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())

                status_tag = result["status"]
                print(status_tag)

                # Circuit breaker: 2 consecutive failed_error runs → stop.
                # Both failing runs are already written; resuming later skips them.
                if status_tag == "failed_error":
                    consecutive_failed_error += 1
                    if consecutive_failed_error >= 2:
                        print(
                            f"\nCircuit breaker: {consecutive_failed_error} consecutive "
                            f"failed_error runs. Last error:\n"
                            f"{result.get('error', '(none)')}\n"
                            f"Fix the issue and resume with the same command.",
                            file=sys.stderr,
                        )
                        sys.exit(1)
                else:
                    consecutive_failed_error = 0

                # Update aggregates
                a = agg[iid]
                a["n"] += 1
                a["status_counts"][status_tag] = a["status_counts"].get(status_tag, 0) + 1
                a["elapsed_times"].append(elapsed_s)
                for rk, cnt in rejected_reasons.items():
                    a["rejection_reasons"][rk] = a["rejection_reasons"].get(rk, 0) + cnt
                if scores["failed_run"]:
                    a["failed"] += 1
                else:
                    sb = scores["score_b"]
                    a["sb_pat"] += int(sb.get("pattern_correct", False))
                    if sb.get("top1_correct") is not None:
                        a["sb_t1"] += int(sb["top1_correct"])
                    if sb.get("top3_correct") is not None:
                        a["sb_t3"] += int(sb["top3_correct"])
                    a["checker_rejected"] += scores["checker"]["n_rejected"]
                    if scores["score_a"]:
                        for tgt, passed in scores["score_a"].items():
                            a["sa_pass"].setdefault(tgt, []).append(int(passed))

    # Summary
    print("\n=== Summary ===")
    for iid in INCIDENTS:
        a = agg[iid]
        n = a["n"]
        if n == 0:
            continue
        parts = [f"{iid} n={n}"]
        # Status breakdown
        sc = a["status_counts"]
        parts.append("status=[" + " ".join(f"{k}:{v}" for k, v in sorted(sc.items())) + "]")
        parts.append(f"score_b_pattern={a['sb_pat']}/{n}")
        if a["sa_pass"]:
            for tgt, results in a["sa_pass"].items():
                parts.append(f"score_a[{tgt}]={sum(results)}/{len(results)}")
        if a["rejection_reasons"]:
            rr = a["rejection_reasons"]
            parts.append("rejected=[" + " ".join(f"{k}:{v}" for k, v in sorted(rr.items())) + "]")
        if a["checker_rejected"]:
            parts.append(f"checker_rejected={a['checker_rejected']}")
        if a["elapsed_times"]:
            mean_s = sum(a["elapsed_times"]) / len(a["elapsed_times"])
            max_s = max(a["elapsed_times"])
            parts.append(f"mean_s={mean_s:.1f} max_s={max_s:.1f}")
        print("  " + "  ".join(parts))

    print(f"\nOutput: {out_path}")


if __name__ == "__main__":
    main()
