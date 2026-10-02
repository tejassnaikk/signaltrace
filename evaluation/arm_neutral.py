"""Arm-neutral scoring for M5 (PLAN.md Section 5.5).

Scores the deterministic engine and the future agent with the SAME two
rubrics (Score A: within-dimension localization; Score B: root-cause
pattern/claim matching), built only from `investigate()`'s existing
outputs (the ledger, the factor table) and the Section 4.1 answer schema.

Deliberately independent of `evaluation/score.py`'s existing per-incident
pass conditions - not imported, not modified. The two scorers are allowed
to disagree; a disagreement is a finding, not a bug to reconcile.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from evaluation.injector import load_ground_truth

FORCED_METRICS = ("sessions", "conversion_rate", "aov")
_CONFIDENT = ("strong", "plausible")


@dataclass
class Claim:
    metric: str
    dimension: str
    segment: str | None
    direction: str   # "down" | "up"
    kind: str        # "within" | "mix" - claim-schema vocabulary (4.1); maps
                      # onto ledger kind "segment"/"mix" (PLAN.md 5.5's note -
                      # the ledger itself never uses the literal word "within")
    evidence_ids: list[str]
    strength: str     # the ledger verdict string, carried through unchanged
    basis: str | None = None  # added 2026-10-01, PLAN.md 5.5's schema
                              # amendment - needed for I3b's Score A check,
                              # not in 4.1's original 7-field schema


@dataclass
class Answer:
    pattern: str   # "localized" | "broad" | "mix" | "none"
    claims: list[Claim] = field(default_factory=list)
    # ^ ALL claims, uncapped, any (metric, dimension) - amended 2026-10-01
    # (PLAN.md 5.5). Used ONLY by Score A: filter to a target (metric,
    # dimension), take the first (already ranked by the arm's own
    # ordering within that group).
    primary: list[Claim] = field(default_factory=list)
    # ^ ranked, capped at 3, channel-dimension only for the engine - the
    # arm's actual asserted answer. Used by Score B and the C1 false-
    # positive/untested-claim counts (PLAN.md 5.5's original definition,
    # unchanged - this is "what was asserted", not "what could be cited").
    narrative: str = ""


# --- engine answer -----------------------------------------------------

def _direction(entry) -> str:
    if entry.actual is None or entry.expected is None:
        return "up"
    return "down" if entry.actual < entry.expected else "up"


def _entry_to_claim(entry) -> Claim:
    return Claim(
        metric=entry.metric, dimension=entry.dimension, segment=entry.segment,
        direction=_direction(entry),
        kind="within" if entry.kind == "segment" else entry.kind,
        evidence_ids=[entry.evidence_id], strength=entry.verdict, basis=entry.basis,
    )


def _is_qualifying(e) -> bool:
    return (e.kind == "segment" and e.metric in FORCED_METRICS
           and (e.verdict in _CONFIDENT or e.verdict == "material_untested"))


def engine_answer(investigate_result: dict) -> Answer:
    """PLAN.md 5.5, amended 2026-10-01 - builds BOTH `claims` and
    `primary` from `investigate()`'s existing `ledger`/`factors` outputs
    only. No new statistics, no new thresholds.

    `claims`: every qualifying row in every forced (metric, dimension) -
    not just channel - ranked by |contribution| WITHIN each (metric,
    dimension) group. This is what Score A reads. The original version
    only ever built channel-dimension, top-3-capped claims and used THAT
    for Score A too, which conflated "what can localize the target" with
    "what the naive rule asserts" - the direct cause of I5's by-
    construction Score A failure (I5's target is device-dimension) and
    3 of the 7 dev disagreements against evaluation/score.py's SCORERS
    (the other 3 were the 3-claim cap dropping a qualifying row; the
    7th, I3b, is a genuine rank disagreement, unaffected by this fix).

    `primary`: the UNCHANGED naive selection rule from the original 5.5 -
    channel-dimension only, one primary metric chosen by the revenue
    factor table's log-space contribution, top-3 claims total. This is
    the arm's actual asserted pattern/answer, used by Score B and by the
    C1 false-positive/untested-claim counts - deliberately NOT re-tuned
    after seeing the dev-date disagreements (PLAN.md 5.5): I4/I6 still
    fail Score B by construction (never outputs broad/mix), and I5's
    top-1/top-3 still fail by construction too (pattern is correct, but
    the device-dimension target can never appear in a channel-only rule).
    """
    ledger = investigate_result["ledger"]
    entries = ledger.entries
    factors = investigate_result["factors"]

    all_qualifying = [e for e in entries if _is_qualifying(e)]

    groups: dict[tuple, list] = {}
    for e in all_qualifying:
        groups.setdefault((e.metric, e.dimension), []).append(e)
    claims = []
    for key in sorted(groups):
        rows = sorted(groups[key], key=lambda e: -abs(e.contribution or 0))
        claims.extend(_entry_to_claim(e) for e in rows)

    channel_qualifying = [e for e in all_qualifying if e.dimension == "channel"]
    if not channel_qualifying:
        return Answer(pattern="none", claims=claims, primary=[])

    if factors is None or factors.empty:
        raise RuntimeError(
            "engine_answer: qualifying channel rows exist but no factor "
            "table was returned - investigate() should always produce one "
            "when any metric fired or was forced successfully; this is a "
            "real inconsistency to investigate, not an expected edge case.")

    metrics_with_rows = sorted({e.metric for e in channel_qualifying}
                               & set(factors.index[factors["contribution"].notna()]))
    if not metrics_with_rows:
        raise RuntimeError(
            "engine_answer: every qualifying metric's factor contribution "
            "is NaN (near-zero total log-space revenue change) - cannot "
            "rank a primary metric.")

    primary_metric = max(metrics_with_rows, key=lambda m: abs(factors.loc[m, "contribution"]))

    def rows_for(metric):
        return sorted((e for e in channel_qualifying if e.metric == metric),
                      key=lambda e: -abs(e.contribution or 0))

    primary = [_entry_to_claim(e) for e in rows_for(primary_metric)]
    other_metrics = sorted((m for m in metrics_with_rows if m != primary_metric),
                           key=lambda m: -abs(factors.loc[m, "contribution"]))
    for m in other_metrics:
        rows = rows_for(m)
        if rows:
            primary.append(_entry_to_claim(rows[0]))

    return Answer(pattern="localized", claims=claims, primary=primary[:3])


# --- checker (PLAN.md 4.1) ----------------------------------------------

def check_answer(answer: Answer, ledger) -> dict:
    """Every claim's evidence_ids must resolve to a real ledger entry, with
    matching verdict (`strength`) and `basis`. Rejected claims are counted
    and reported, never silently dropped. Checks BOTH `claims` (Score A's
    uncapped evidence list) and `primary` (the asserted answer, Score B) -
    both are ledger-built claim lists an arm could be held to."""
    by_id = {e.evidence_id: e for e in ledger.entries}

    def _check(claim_list):
        rejected = []
        for claim in claim_list:
            for eid in claim.evidence_ids:
                entry = by_id.get(eid)
                if entry is None:
                    rejected.append({"claim": claim, "reason": f"no ledger entry {eid!r}"})
                    break
                if entry.verdict != claim.strength:
                    rejected.append({"claim": claim, "reason":
                        f"strength {claim.strength!r} != entry verdict {entry.verdict!r}"})
                    break
                if entry.basis != claim.basis:
                    rejected.append({"claim": claim, "reason":
                        f"basis {claim.basis!r} != entry basis {entry.basis!r}"})
                    break
        return rejected

    rejected = _check(answer.claims) + _check(answer.primary)
    n_claims = len(answer.claims) + len(answer.primary)
    return {"n_claims": n_claims, "n_rejected": len(rejected), "rejected": rejected}


# --- Score A --------------------------------------------------------------

TARGETS: dict[str, list[tuple[str, str, str]]] = {
    "I1": [("sessions", "channel", "Organic Search")],
    "I2": [("conversion_rate", "channel", "Referral")],
    "I3": [("aov", "channel", "Direct")],
    "I3b": [("aov", "channel", "Direct")],
    "I5": [("conversion_rate", "device", "desktop")],
    "I7": [("sessions", "channel", "Organic Search"),
          ("conversion_rate", "channel", "Referral")],
}


def score_a(answer: Answer, incident_id: str) -> dict[tuple, bool] | None:
    """PLAN.md 5.5 Score A. Returns {(metric,dimension,segment): passed},
    or None for I4/I6/C1, which have no single named target segment."""
    targets = TARGETS.get(incident_id)
    if targets is None:
        return None
    out = {}
    for metric, dimension, segment in targets:
        candidates = [c for c in answer.claims if c.metric == metric and c.dimension == dimension]
        if not candidates:
            out[(metric, dimension, segment)] = False
            continue
        top = candidates[0]
        ok = (top.segment == segment and top.direction == "down" and top.kind == "within")
        if metric == "aov":
            ok = ok and top.strength == "material_untested"
        else:
            ok = ok and top.strength in _CONFIDENT
        if incident_id == "I3b":
            ok = ok and top.basis == "ineligible_segment"
        out[(metric, dimension, segment)] = ok
    return out


# --- Score B --------------------------------------------------------------

def _i6_metric() -> str:
    """Read directly from ground truth, not assumed - fails loudly if the
    file ever states more than one forced metric (PLAN.md 5.5)."""
    gt = load_ground_truth("I6")
    fm = gt["forced_metrics"]
    if len(fm) != 1:
        raise ValueError(f"I6 forced_metrics is ambiguous for Score B: {fm!r}")
    return fm[0]


_CORRECT_B = {
    "I1": {"pattern": "localized", "causes": [("sessions", "channel", "Organic Search")]},
    "I2": {"pattern": "localized", "causes": [("conversion_rate", "channel", "Referral")]},
    "I3": {"pattern": "localized", "causes": [("aov", "channel", "Direct")]},
    "I3b": {"pattern": "localized", "causes": [("aov", "channel", "Direct")]},
    "I5": {"pattern": "localized", "causes": [("conversion_rate", "device", "desktop")]},
    "I7": {"pattern": "localized", "causes": [
        ("sessions", "channel", "Organic Search"),
        ("conversion_rate", "channel", "Referral")]},
    "I4": {"pattern": "broad", "metric": "aov"},
    "I6": {"pattern": "mix", "dimension": "device"},   # metric filled from ground truth below
    "C1": {"pattern": "none", "causes": []},
}


def _claim_key(c: Claim) -> tuple:
    return (c.metric, c.dimension, c.segment)


def score_b(answer: Answer, incident_id: str) -> dict:
    """Unchanged since the 2026-10-01 amendment - reads `answer.primary`
    (the arm's actual capped, asserted answer), never `answer.claims`
    (Score A's uncapped evidence list). PLAN.md 5.5."""
    expect = dict(_CORRECT_B[incident_id])
    if incident_id == "I6":
        expect["metric"] = _i6_metric()

    pattern_correct = answer.pattern == expect["pattern"]
    result = {"pattern_correct": pattern_correct, "top1_correct": None, "top3_correct": None}

    if incident_id in ("I1", "I2", "I3", "I3b", "I5"):
        cause = expect["causes"][0]
        top1 = bool(answer.primary) and _claim_key(answer.primary[0]) == cause
        top3 = any(_claim_key(c) == cause for c in answer.primary[:3])
        result["top1_correct"] = pattern_correct and top1
        result["top3_correct"] = pattern_correct and top3
    elif incident_id == "I7":
        causes = set(expect["causes"])
        top1 = bool(answer.primary) and _claim_key(answer.primary[0]) in causes
        top3_set = {_claim_key(c) for c in answer.primary[:3]}
        result["top1_correct"] = pattern_correct and top1
        result["top3_correct"] = pattern_correct and bool(top3_set & causes)
        result["both_in_top3"] = pattern_correct and causes.issubset(top3_set)
    # I4, I6, C1: no single named cause to rank - pattern match is the whole
    # check, top1/top3 stay None (not False - "not applicable", not "wrong").

    return result


# --- C1 false positives / untested-claim counts ---------------------------

def c1_claim_counts(answer: Answer) -> dict:
    """PLAN.md 5.5: a confident (strong/plausible) claim on a C1 date is a
    false positive by definition; an untested claim is counted separately,
    not folded into the false-positive count. Reads `answer.primary` - the
    arm's actual asserted answer, not the uncapped `claims` evidence list
    (which would inflate both counts with rows the arm never asserted)."""
    false_positives = [c for c in answer.primary if c.strength in _CONFIDENT]
    untested = [c for c in answer.primary if c.strength not in _CONFIDENT]
    return {"false_positives": len(false_positives), "untested_claims": len(untested)}


# --- dev-date report (41 dates, build_incident_con, all 3 metrics forced) -

if __name__ == "__main__":
    import duckdb
    import pandas as pd

    from evaluation.injector import build_incident_con
    from evaluation.score import SCORERS
    from investigation.orchestrator import investigate

    INCIDENTS = ["I1", "I2", "I3", "I3b", "I4", "I5", "I6", "I7", "C1"]

    sa_counts = {iid: {} for iid in INCIDENTS}
    sb_counts = {iid: {"n": 0, "pattern": 0, "top1": 0, "top3": 0} for iid in INCIDENTS}
    i7_both_top3 = 0
    c1_fp = c1_untested = 0
    checker_rejections = 0
    checker_claims_total = 0
    disagreements = []

    for iid in INCIDENTS:
        gt = load_ground_truth(iid)
        for date in gt["dates"]:
            con = build_incident_con(iid, date)
            r = investigate(date, con=con, force=list(FORCED_METRICS),
                            incident_id=f"{iid}_{date}_armneutral", verbose=False)
            con.close()

            answer = engine_answer(r)

            chk = check_answer(answer, r["ledger"])
            checker_claims_total += chk["n_claims"]
            checker_rejections += chk["n_rejected"]

            sa = score_a(answer, iid)
            if sa is not None:
                for target, passed in sa.items():
                    sa_counts[iid].setdefault(target, {"pass": 0, "n": 0})
                    sa_counts[iid][target]["n"] += 1
                    sa_counts[iid][target]["pass"] += int(passed)

                # side-by-side with the existing, independent SCORERS result.
                # existing.passed is a single bool for the incident's own
                # condition; Score A is per-target (I7 has two) - "all
                # targets pass" is the fair 1:1 comparison.
                existing = SCORERS[iid](gt, date) if iid in SCORERS else None
                this_date_all_pass = all(sa.values())
                if existing is not None and existing.passed is not None:
                    if this_date_all_pass != existing.passed:
                        # 2026-10-01: score_a now reads answer.claims (the
                        # uncapped, every-(metric,dimension) list), so the
                        # device-exclusion and 3-claim-cap mechanisms that
                        # explained 6 of the previous 7 disagreements no
                        # longer apply - any remaining mismatch is diagnosed
                        # by directly diffing the top candidate's fields
                        # against the target.
                        reasons = []
                        for (metric, dimension, segment), passed in sa.items():
                            if passed:
                                continue
                            candidates = [c for c in answer.claims
                                         if c.metric == metric and c.dimension == dimension]
                            if not candidates:
                                reasons.append(
                                    f"{metric}/{dimension}: no qualifying claim at all for "
                                    f"{segment} under force=all-3-metrics "
                                    f"(answer.pattern={answer.pattern!r})")
                            else:
                                top = candidates[0]
                                diffs = []
                                if top.segment != segment: diffs.append(f"segment={top.segment!r}")
                                if top.direction != "down": diffs.append(f"direction={top.direction!r}")
                                if top.kind != "within": diffs.append(f"kind={top.kind!r}")
                                if top.strength not in _CONFIDENT and top.strength != "material_untested":
                                    diffs.append(f"strength={top.strength!r}")
                                if iid == "I3b" and top.basis != "ineligible_segment":
                                    diffs.append(f"basis={top.basis!r}")
                                reasons.append(f"{metric}/{dimension}: top claim is "
                                              f"{top.segment!r} not {segment!r}" if top.segment != segment
                                              else f"{metric}/{dimension}: top claim mismatch "
                                                   f"({', '.join(diffs)})")
                        if not reasons:
                            # arm_neutral passed every target; existing failed
                            # the incident's own, possibly stricter condition
                            # (e.g. I5's absolute device-share floor, which
                            # Score A's uniform rank+sign+verdict check does
                            # not apply) - quote the existing scorer's own
                            # measured detail rather than guess.
                            reasons.append(
                                f"arm_neutral's rank+sign+verdict check passed, but the "
                                f"existing scorer's own (possibly stricter/different) "
                                f"condition failed: {existing.detail}")
                        disagreements.append({
                            "incident": iid, "date": date,
                            "arm_neutral_score_a": this_date_all_pass,
                            "existing_scorer": existing.passed,
                            "answer_pattern": answer.pattern,
                            "n_claims": len(answer.claims),
                            "reason": "; ".join(reasons),
                        })

            if iid == "C1":
                c = c1_claim_counts(answer)
                c1_fp += c["false_positives"]
                c1_untested += c["untested_claims"]

            sb = score_b(answer, iid)
            sb_counts[iid]["n"] += 1
            sb_counts[iid]["pattern"] += int(sb["pattern_correct"])
            if sb["top1_correct"] is not None:
                sb_counts[iid]["top1"] += int(sb["top1_correct"])
            if sb["top3_correct"] is not None:
                sb_counts[iid]["top3"] += int(sb["top3_correct"])
            if iid == "I7" and sb.get("both_in_top3"):
                i7_both_top3 += 1

    print("=== Score A pass counts per incident/target ===")
    for iid in INCIDENTS:
        if not sa_counts[iid]:
            print(f"  {iid}: no Score A")
            continue
        for target, p in sa_counts[iid].items():
            print(f"  {iid} {target}: {p['pass']}/{p['n']}")

    print(f"\n=== Disagreements with existing SCORERS (n={len(disagreements)}) ===")
    for d in disagreements:
        print(f"  {d['incident']} {d['date']}: arm_neutral={d['arm_neutral_score_a']} "
             f"existing={d['existing_scorer']} pattern={d['answer_pattern']} "
             f"n_claims={d['n_claims']}\n      reason: {d['reason']}")

    print("\n=== Score B: pattern / top1 / top3 per incident ===")
    for iid in INCIDENTS:
        c = sb_counts[iid]
        print(f"  {iid}: pattern {c['pattern']}/{c['n']}  top1 {c['top1']}/{c['n']}  "
             f"top3 {c['top3']}/{c['n']}")
    print(f"\nI7 both-targets-in-top-3: {i7_both_top3}/{sb_counts['I7']['n']}")

    print(f"\n=== C1 claim counts ===")
    print(f"  false positives (strong/plausible claims): {c1_fp}")
    print(f"  untested claims (reported separately): {c1_untested}")

    print(f"\n=== Checker ===")
    print(f"  total claims checked: {checker_claims_total}")
    print(f"  rejections: {checker_rejections} (expect 0)")
