"""Scoring script for the M3 incident benchmark (PLAN.md Section 5).

Reads each evaluation/ground_truth/<id>.json, runs the injector plus a forced
investigate() call for every pre-registered date, and checks that incident's
own pass condition. One scorer function per incident id, not a single
generic rule - each condition has a genuinely different shape (PLAN.md
Section 5, "General principle": rank+sign+verdict vs. absolute threshold vs.
measured-noise-distribution), which is the actual content of the rubric, not
an oversight to normalize away.

Every DateResult also records whether the real z>4 detector fired on each
forced component that date (`detector_fired`) - the pre-registered "before"
measurement for M2b (notes/baseline.md, "Detection power"; PLAN.md 5.1,
"Forced investigation": detection and diagnosis are scored separately).

Neither detection/ nor investigation/ import anything from here or from
ground_truth/ - the engine under test never sees its own answer key.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from detection.config import VALIDATOR
from evaluation.injector import build_incident_con, load_ground_truth
from investigation.orchestrator import investigate

GROUND_TRUTH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ground_truth")
PLAUSIBLE_OR_BETTER = ("strong", "plausible")


@dataclass
class DateResult:
    date: str
    passed: bool | None          # None where the incident is a measurement, not pass/fail (C1)
    detail: str
    measurements: dict = field(default_factory=dict)


def _run(incident_id: str, date: str, forced_metrics: list[str]) -> dict:
    con = build_incident_con(incident_id, date)
    r = investigate(date, con=con, force=forced_metrics, incident_id=f"{incident_id}_{date}", verbose=False)
    con.close()
    return r


def _detector_fired(r: dict, forced_metrics: list[str]) -> dict[str, bool]:
    """Did the real z>4 detector (unmodified thresholds) fire on each forced
    component this date, on THIS (possibly injected) data? Independent of
    whether the benchmark's own pass condition is satisfied - detection and
    diagnosis are scored separately (PLAN.md 5.1)."""
    today = r["today"]
    out = {}
    for m in forced_metrics:
        row = today[today["metric"] == m]
        out[m] = bool(row["is_anomaly"].iloc[0]) if not row.empty else None
    return out


def _dim_segments(df: pd.DataFrame, metric: str, dimension: str) -> pd.DataFrame:
    return df[(df["metric"] == metric) & (df["dimension"] == dimension)
              & (df["kind"] == "segment")].copy()


def _top_magnitude(seg: pd.DataFrame):
    if seg.empty:
        return None
    return seg.loc[seg["abs_contribution"].abs().idxmax()]


def _rank_sign_verdict(df: pd.DataFrame, metric: str, segment: str,
                       verdict_ok: tuple[str, ...]) -> tuple[bool, dict]:
    """Shared scorer for the rank+sign+verdict fix (I1/I2/I3/I7's channel
    sub-conditions, PLAN.md Section 5's factor (1)/(2))."""
    seg_rows = _dim_segments(df, metric, "channel")
    top = _top_magnitude(seg_rows)
    if top is None:
        return False, {"reason": "no channel results"}
    target = seg_rows[seg_rows["segment"] == segment]
    if target.empty:
        return False, {"reason": "target segment missing"}
    target = target.iloc[0]
    is_top = top["segment"] == segment
    correctly_signed = bool(target["actual"] < target["expected"])
    verdict_pass = target["verdict"] in verdict_ok
    passed = bool(is_top and correctly_signed and verdict_pass)
    return passed, {"top_segment": top["segment"], "is_top": is_top,
                     "correctly_signed": correctly_signed,
                     "verdict": target["verdict"], "q": target["q_value"]}


def score_I1(gt: dict, date: str) -> DateResult:
    r = _run("I1", date, gt["forced_metrics"])
    passed, meas = _rank_sign_verdict(r["results"], "sessions", gt["pass_condition"]["segment"], ("strong",))
    meas["detector_fired"] = _detector_fired(r, gt["forced_metrics"])
    return DateResult(date, passed, f"{meas}", meas)


def score_I2(gt: dict, date: str) -> DateResult:
    r = _run("I2", date, gt["forced_metrics"])
    passed, meas = _rank_sign_verdict(r["results"], "conversion_rate", gt["pass_condition"]["segment"],
                                      PLAUSIBLE_OR_BETTER)
    meas["detector_fired"] = _detector_fired(r, gt["forced_metrics"])
    return DateResult(date, passed, f"{meas}", meas)


def score_I3(gt: dict, date: str) -> DateResult:
    r = _run("I3", date, gt["forced_metrics"])
    df = r["results"]
    seg_rows = _dim_segments(df, "aov", "channel")
    top = _top_magnitude(seg_rows)
    if top is None:
        return DateResult(date, False, "no channel results")
    target = seg_rows[seg_rows["segment"] == gt["pass_condition"]["segment"]]
    if target.empty:
        return DateResult(date, False, "target segment missing")
    target = target.iloc[0]
    is_top = top["segment"] == gt["pass_condition"]["segment"]
    correctly_signed = bool(target["actual"] < target["expected"])
    verdict_ok = target["verdict"] == "material_untested"
    passed = bool(is_top and correctly_signed and verdict_ok)
    meas = {"top_segment": top["segment"], "is_top": is_top,
            "correctly_signed": correctly_signed, "verdict": target["verdict"],
            "detector_fired": _detector_fired(r, gt["forced_metrics"])}
    return DateResult(date, passed, f"{meas}", meas)


def score_I3b(gt: dict, date: str) -> DateResult:
    r = _run("I3b", date, gt["forced_metrics"])
    df = r["results"]
    seg_rows = _dim_segments(df, "aov", "channel")
    target = seg_rows[seg_rows["segment"] == gt["pass_condition"]["segment"]]
    if target.empty:
        return DateResult(date, False, "Direct's row missing (should be kept, not dropped)")
    target = target.iloc[0]
    present = True
    eligible_ok = bool(target["eligible"]) == gt["pass_condition"]["eligible"]
    verdict_ok = target["verdict"] == gt["pass_condition"]["verdict"]
    passed = bool(present and eligible_ok and verdict_ok)
    meas = {"present": present, "eligible": bool(target["eligible"]), "verdict": target["verdict"],
            "detector_fired": _detector_fired(r, gt["forced_metrics"])}
    return DateResult(date, passed, f"{meas}", meas)


def score_I4(gt: dict, date: str) -> DateResult:
    r = _run("I4", date, gt["forced_metrics"])
    df = r["results"]
    ref = gt["pass_condition"]["reference_distributions"]
    passed_all, meas = True, {}
    for dim, key in (("channel", "channel_aov_max_share"),
                     ("device", "device_aov_max_share"),
                     ("country", "country_aov_max_share")):
        seg_rows = _dim_segments(df, "aov", dim)
        if seg_rows.empty:
            passed_all = False
            meas[dim] = "no results"
            continue
        max_share = float(seg_rows["contribution"].abs().max())
        p95 = ref[key]["p95"]
        ok = max_share < p95
        passed_all = passed_all and ok
        meas[dim] = {"max_share": max_share, "p95_reference": p95, "ok": ok}
    meas["detector_fired"] = _detector_fired(r, gt["forced_metrics"])
    return DateResult(date, passed_all, f"{meas}", meas)


def score_I5(gt: dict, date: str) -> DateResult:
    r = _run("I5", date, gt["forced_metrics"])
    df = r["results"]
    pc = gt["pass_condition"]
    meas = {}

    dev = _dim_segments(df, "conversion_rate", "device")
    dev_row = dev[dev["segment"] == pc["device"]["segment"]]
    dev_ok = (not dev_row.empty and dev_row.iloc[0]["contribution"] >= pc["device"]["within_share_min"]
             and dev_row.iloc[0]["verdict"] == pc["device"]["verdict"])
    meas["device"] = (dev_row.iloc[0][["contribution", "verdict"]].to_dict() if not dev_row.empty else None)

    ctry = _dim_segments(df, "conversion_rate", "country")
    ctry_row = ctry[ctry["segment"] == pc["country"]["segment"]]
    ctry_ok = (not ctry_row.empty and ctry_row.iloc[0]["contribution"] >= pc["country"]["within_share_min"]
              and ctry_row.iloc[0]["verdict"] == pc["country"]["verdict"])
    meas["country"] = (ctry_row.iloc[0][["contribution", "verdict"]].to_dict() if not ctry_row.empty else None)

    ch = _dim_segments(df, "conversion_rate", "channel")
    ch = ch[ch["eligible"]]  # only ratio-eligible channels compete for the top-2 ratio
    mags = ch["abs_contribution"].abs().nlargest(2).tolist()
    ratio = (mags[0] / mags[1]) if len(mags) == 2 and mags[1] > 1e-12 else float("inf")
    ch_ok = ratio < pc["channel"]["ratio_max"]
    meas["channel_top2_ratio"] = ratio
    meas["detector_fired"] = _detector_fired(r, gt["forced_metrics"])

    passed = bool(dev_ok and ctry_ok and ch_ok)
    return DateResult(date, passed, f"{meas}", meas)


def score_I6(gt: dict, date: str) -> DateResult:
    r = _run("I6", date, gt["forced_metrics"])
    df = r["results"]
    dev = _dim_segments(df, "conversion_rate", "device")
    mix = df[(df["metric"] == "conversion_rate") & (df["dimension"] == "device") & (df["kind"] == "mix")]
    mix_ok = not mix.empty and mix.iloc[0]["verdict"] == "arithmetic"
    desktop = dev[dev["segment"] == "desktop"]
    mobile = dev[dev["segment"] == "mobile"]
    desktop_ok = desktop.empty or desktop.iloc[0]["verdict"] != "strong"
    mobile_ok = mobile.empty or mobile.iloc[0]["verdict"] != "strong"
    passed = bool(mix_ok and desktop_ok and mobile_ok)
    meas = {"mix_verdict": mix.iloc[0]["verdict"] if not mix.empty else None,
            "desktop_verdict": desktop.iloc[0]["verdict"] if not desktop.empty else None,
            "mobile_verdict": mobile.iloc[0]["verdict"] if not mobile.empty else None,
            "detector_fired": _detector_fired(r, gt["forced_metrics"])}
    return DateResult(date, passed, f"{meas}", meas)


def score_I7(gt: dict, date: str) -> DateResult:
    r = _run("I7", date, gt["forced_metrics"])
    df = r["results"]
    sess_pass, sess_meas = _rank_sign_verdict(df, "sessions", gt["pass_condition"]["sessions_channel"]["segment"],
                                              ("strong",))
    cr_pass, cr_meas = _rank_sign_verdict(df, "conversion_rate",
                                          gt["pass_condition"]["conversion_rate_channel"]["segment"],
                                          PLAUSIBLE_OR_BETTER)
    passed = bool(sess_pass and cr_pass)
    meas = {"sessions": sess_meas, "conversion_rate": cr_meas,
            "detector_fired": _detector_fired(r, gt["forced_metrics"])}
    return DateResult(date, passed, f"{meas}", meas)


def score_C1(gt: dict, date: str) -> DateResult:
    """Not pass/fail - the false-strong-rate measurement (PLAN.md 5.2, 5.3).

    Denominator is MATERIAL TESTED pairs only: testable AND eligible AND
    contribution clears the materiality floor - i.e. exactly the set that
    entered validator.py's BH family (validate_segments' `material` list).
    Excludes immaterial (below floor, never tested) and material_untested
    (no q-value at all, either ineligible or no valid test) - counting those
    in the denominator would understate the rate among pairs that actually
    COULD have come back significant."""
    r = _run("C1", date, gt["forced_metrics"])
    df = r["results"]
    seg = df[df["kind"] == "segment"].copy()
    material = seg[seg["testable"] & seg["eligible"]
                   & (seg["contribution"].abs() >= VALIDATOR["materiality_floor"])]
    strong = material[material["verdict"] == "strong"]
    by_metric = {}
    for m in ("sessions", "conversion_rate", "aov"):
        mm = material[material["metric"] == m]
        if m not in gt["forced_metrics"]:
            by_metric[m] = "n/a (not forced for C1)"
        else:
            ss = mm[mm["verdict"] == "strong"]
            by_metric[m] = {"n_material_tested": len(mm), "n_strong": len(ss)}
    meas = {"n_material_tested": len(material), "n_strong": len(strong),
            "by_metric": by_metric,
            "strong_segments": list(zip(strong["metric"], strong["dimension"], strong["segment"])),
            "detector_fired": _detector_fired(r, gt["forced_metrics"])}
    return DateResult(date, None, f"{meas}", meas)


SCORERS = {"I1": score_I1, "I2": score_I2, "I3": score_I3, "I3b": score_I3b,
          "I4": score_I4, "I5": score_I5, "I6": score_I6, "I7": score_I7,
          "C1": score_C1}


def score_incident(incident_id: str) -> list[DateResult]:
    gt = load_ground_truth(incident_id)
    scorer = SCORERS[incident_id]
    return [scorer(gt, date) for date in gt["dates"]]


def score_all() -> dict[str, list[DateResult]]:
    return {iid: score_incident(iid) for iid in SCORERS}


if __name__ == "__main__":
    results = score_all()
    print(f"{'incident':6s} {'date':12s} {'pass':6s} detail")
    print("-" * 100)
    total_pass, total_n = 0, 0
    for iid, date_results in results.items():
        for dr in date_results:
            status = "-" if dr.passed is None else ("PASS" if dr.passed else "FAIL")
            print(f"{iid:6s} {dr.date:12s} {status:6s} {dr.detail[:120]}")
        passed = [d.passed for d in date_results if d.passed is not None]
        if passed:
            print(f"       {'':12s} {sum(passed)}/{len(passed)} pass\n")
            total_pass += sum(passed); total_n += len(passed)
        else:
            print()
    print(f"TOTAL (excluding C1, which is a measurement, not pass/fail): {total_pass}/{total_n}\n")

    print("=== C1 false-strong rate (measurement, not pass/fail) ===")
    print("denominator = material AND testable AND eligible pairs only "
          "(excludes immaterial and material_untested)")
    c1 = results["C1"]
    total_strong = sum(d.measurements["n_strong"] for d in c1)
    total_tested = sum(d.measurements["n_material_tested"] for d in c1)
    rate = total_strong / total_tested if total_tested else float("nan")
    print(f"overall: {total_strong}/{total_tested} ({rate:.1%})")
    for m in ("sessions", "conversion_rate"):
        s = sum(d.measurements["by_metric"][m]["n_strong"] for d in c1)
        t = sum(d.measurements["by_metric"][m]["n_material_tested"] for d in c1)
        print(f"  {m:16s}: {s}/{t} ({s/t:.1%})" if t else f"  {m:16s}: 0/0")
    print(f"  {'aov':16s}: n/a (not forced for C1)")

    print("\n=== detector-fired (z>4), pre-registered 'before' for M2b ===")
    for iid, date_results in results.items():
        for dr in date_results:
            print(f"  {iid:6s} {dr.date:12s} {dr.measurements.get('detector_fired')}")
