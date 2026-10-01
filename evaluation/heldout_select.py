"""Freeze the M5 held-out set (PLAN.md Section 5.4).

Read-only against the real engine. The only engine calls here run on REAL,
UNINJECTED data, for two purposes only: (1) rebuild the screened candidate
pool exactly as the M3m holiday fix did (detection/detect_components,
compose_revenue_verdict, annotate_concentration - never hardcoded date
lists, so this stays correct if the data or detection config ever change);
(2) reproduce the per-incident eligibility/contamination filters already
measured by hand in the 2026-09-30 read-only verification. This script
makes that one-off measurement reproducible and frozen - it does NOT run
investigate() or any scorer on a held-out date, and never will: the whole
point of a held-out set is that the engine's own performance on it stays
unmeasured until M5/M6 formally score it.

Determinism: numpy default_rng(SEED) shuffles the 77-date candidate pool
exactly once. Every downstream choice - which k is feasible, which date
goes to which incident - is derived from that single shuffled order (kept
in explicit lists throughout, never from dict/set iteration, which is not
guaranteed stable for this purpose). Running this script twice produces a
byte-identical JSON.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from detection.anomaly import (annotate_concentration, compose_revenue_verdict,
                               detect_components)
from detection.config import DETECTION_END, DETECTION_START, EXCLUDE_DATES
from evaluation.injector import load_ground_truth
from investigation.hypotheses import eligible_segments
from investigation.orchestrator import DB, investigate

SEED = 20261001
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "heldout")
OUT_PATH = os.path.join(OUT_DIR, "heldout_dates.json")

DEV_INCIDENTS = ["I1", "I2", "I3", "I3b", "I4", "I5", "I6", "I7", "C1"]

EXPECTED_COUNTS = {
    "I1": 71, "I2": 67, "I3": 52, "I3b": 1, "I4": 57,
    "I5": 62, "I6": 62, "I7": 61, "C1": 77,
}
EXPECTED_REMAINING_POOL = 77

# Quoted verbatim from notes/PLAN.md, Section 5.1 and Section 8 - the only
# place the contamination principle is actually stated. There is no single
# numeric formula on record; this script operationalizes it (see
# `_elevated`/`_manufactured` below) the same way the 2026-09-30 read-only
# report did, as a mechanical generalization of the same two ingredients
# (an already-passing real verdict; elevated real background noise),
# not a literal replay of the original by-hand, 8-date judgment call.
CONTAMINATION_RULE_QUOTE = (
    "PLAN.md 5.1: \"Per-date empirical check (real, uninjected data, not "
    "just calendar proximity) - swap only where an actual effect exists, "
    "not just proximity.\" "
    "PLAN.md 8 (holiday screening gap row): \"A date can pass its pass "
    "condition for the wrong reason, not just fail for the wrong reason; "
    "a contamination check needs to look for both.\""
)

METHOD_DESCRIPTION = (
    "Bipartite max-matching (Kuhn/DFS augmenting paths, hand-implemented, "
    "no networkx dependency), not greedy. Each of the 8 non-I3b incidents "
    "is represented by k interchangeable slots; edges connect a slot to a "
    "date iff that date survived the incident's own eligibility+"
    "contamination filters AND is not I3b's single reserved date. Slots "
    "are processed in a fixed (incident, replica-index) order; each slot's "
    "candidate dates are iterated in the order they appear in the single "
    "numpy default_rng(SEED)-shuffled date sequence, so the seed - not "
    "insertion order or dict/set iteration - decides which valid dates are "
    "picked whenever more than one feasible assignment exists. k starts at "
    "9 and drops to 8, then 7, stopping at the first value for which every "
    "slot can be matched to a distinct date (a perfect matching on the "
    "slot side)."
)


# --- 1. Pool reconstruction (dynamic, same method as the M3m holiday fix) --

def _buffer(dates, n=3):
    out = set()
    for d in dates:
        for off in range(-n, n + 1):
            out.add(pd.Timestamp(d) + pd.Timedelta(days=off))
    return out


def reconstruct_pool(con) -> list[pd.Timestamp]:
    daily = con.execute("SELECT * FROM metrics_daily ORDER BY date").df()
    daily["date"] = pd.to_datetime(daily["date"])
    conc = con.execute("SELECT * FROM order_concentration ORDER BY date").df()
    conc["date"] = pd.to_datetime(conc["date"])

    anoms = annotate_concentration(detect_components(daily), conc)
    composed = compose_revenue_verdict(anoms)
    composed_days = pd.to_datetime(sorted(composed["date"].unique()))
    whale = anoms[anoms["single_order_driven"] & anoms["is_anomaly"]]
    whale_days = pd.to_datetime(sorted(whale["date"].unique()))

    full = pd.date_range(DETECTION_START, DETECTION_END, freq="D")
    weekdays = pd.DatetimeIndex(full[full.weekday < 5])

    core = _buffer(composed_days.union(whale_days))
    excl = set(pd.to_datetime(list(EXCLUDE_DATES)))

    from pandas.tseries.holiday import USFederalHolidayCalendar
    holidays = USFederalHolidayCalendar().holidays(start=DETECTION_START, end=DETECTION_END)
    holiday_buf = _buffer(holidays)

    pool = sorted(d for d in weekdays if d not in (core | excl | holiday_buf))
    return pool, len(composed_days), len(whale_days)


def load_dev_dates() -> set[pd.Timestamp]:
    gt_dir = os.path.join(ROOT, "ground_truth")
    dev = set()
    for fname in sorted(os.listdir(gt_dir)):
        if not fname.endswith(".json"):
            continue
        gt = json.load(open(os.path.join(gt_dir, fname)))
        for d in gt["dates"]:
            dev.add(pd.Timestamp(d))
    return dev


# --- 2. Per-incident eligibility + contamination filters -------------------

def _ordinary_day_p90(anoms: pd.DataFrame, core: set, excl: set) -> dict[str, float]:
    clean = anoms[~anoms["date"].isin(core | excl)]
    return {m: float(clean[clean["metric"] == m]["robust_z"].abs().quantile(0.90))
           for m in ("sessions", "conversion_rate", "aov")}


def _dim_segments(rdf: pd.DataFrame, metric: str, dimension: str) -> pd.DataFrame:
    if rdf.empty or "metric" not in rdf.columns:
        return rdf.iloc[0:0]
    return rdf[(rdf["metric"] == metric) & (rdf["dimension"] == dimension)
              & (rdf["kind"] == "segment")]


class RealDataCache:
    """One forced, UNINJECTED investigate() call per (date, metric), reused
    across every incident that needs that same real reading."""

    def __init__(self, con):
        self.con = con
        self._cache: dict[tuple, pd.DataFrame] = {}

    def results(self, date: pd.Timestamp, metric: str) -> pd.DataFrame:
        key = (date, metric)
        if key not in self._cache:
            r = investigate(date, con=self.con, force=[metric], verbose=False)
            self._cache[key] = r["results"]
        return self._cache[key]


def _elevated(anoms, date, metric, p90) -> bool:
    row = anoms[(anoms["date"] == date) & (anoms["metric"] == metric)]
    if row.empty or pd.isna(row.iloc[0]["robust_z"]):
        return False
    return abs(row.iloc[0]["robust_z"]) >= p90[metric]


def _rank_sign_verdict_manufactured(rdf, metric, dimension, segment, verdict_ok) -> bool:
    seg = _dim_segments(rdf, metric, dimension)
    if seg.empty:
        return False
    top = seg.loc[seg["abs_contribution"].abs().idxmax()]
    tgt = seg[seg["segment"] == segment]
    if tgt.empty:
        return False
    tgt = tgt.iloc[0]
    is_top = top["segment"] == segment
    signed = bool(tgt["actual"] < tgt["expected"])
    return bool(is_top and signed and tgt["verdict"] in verdict_ok)


def eligible_dates_for(dimension, metric, segment, pool, con, want_eligible=True):
    out = []
    for d in pool:
        elig = set(eligible_segments(dimension, metric, con, asof=d))
        if (segment in elig) == want_eligible:
            out.append(d)
    return out


def contamination_clean(pool, cache, anoms, p90, checks):
    """`checks` is a list of (metric, dimension, segment, verdict_ok) tuples
    - a date survives only if NONE of them are manufactured or elevated."""
    out = []
    for d in pool:
        bad = False
        for metric, dimension, segment, verdict_ok in checks:
            rdf = cache.results(d, metric)
            if _rank_sign_verdict_manufactured(rdf, metric, dimension, segment, verdict_ok):
                bad = True
                break
            if _elevated(anoms, d, metric, p90):
                bad = True
                break
        if not bad:
            out.append(d)
    return out


def i3_aov_clean(pool, cache, anoms, p90):
    """AOV's verdict is structurally always material_untested (3.7) - the
    manufacture check for I3/I3b is top+correctly-signed alone, no verdict
    bar to check (there isn't one in the real pass condition either)."""
    out = []
    for d in pool:
        rdf = cache.results(d, "aov")
        seg = _dim_segments(rdf, "aov", "channel")
        manufactured = False
        if not seg.empty:
            top = seg.loc[seg["abs_contribution"].abs().idxmax()]
            tgt = seg[seg["segment"] == "Direct"]
            if not tgt.empty:
                manufactured = bool(top["segment"] == "Direct"
                                    and tgt.iloc[0]["actual"] < tgt.iloc[0]["expected"])
        if manufactured or _elevated(anoms, d, "aov", p90):
            continue
        out.append(d)
    return out


def i4_clean(pool, cache):
    """No single target segment (uniform cut). Substitute I4's OWN
    established method (5.1/5.2): real per-dimension max-|share| falls
    below that dimension's own measured p90, recomputed from this pool."""
    per_date = {}
    maxshares = {"channel": [], "device": [], "country": []}
    for d in pool:
        rdf = cache.results(d, "aov")
        row = {}
        for dim in maxshares:
            seg = _dim_segments(rdf, "aov", dim)
            ms = float(seg["contribution"].abs().max()) if not seg.empty else np.nan
            row[dim] = ms
            if not np.isnan(ms):
                maxshares[dim].append(ms)
        per_date[d] = row
    p90 = {dim: float(np.percentile(maxshares[dim], 90)) for dim in maxshares}
    return [d for d in pool
            if all(not np.isnan(per_date[d][dim]) and per_date[d][dim] < p90[dim]
                  for dim in maxshares)]


def i6_clean(pool, cache):
    """No single target segment (mix shift). Substitute I6's OWN pass
    condition: neither desktop's nor mobile's real within-effect reaches
    verdict=strong - that would manufacture a false pass for I6
    specifically."""
    out = []
    for d in pool:
        rdf = cache.results(d, "conversion_rate")
        seg = _dim_segments(rdf, "conversion_rate", "device")
        desktop = seg[seg["segment"] == "desktop"]
        mobile = seg[seg["segment"] == "mobile"]
        d_ok = desktop.empty or desktop.iloc[0]["verdict"] != "strong"
        m_ok = mobile.empty or mobile.iloc[0]["verdict"] != "strong"
        if d_ok and m_ok:
            out.append(d)
    return out


def compute_clean_pools(pool, con) -> dict[str, list[pd.Timestamp]]:
    daily = con.execute("SELECT * FROM metrics_daily ORDER BY date").df()
    daily["date"] = pd.to_datetime(daily["date"])
    anoms = detect_components(daily)
    conc = con.execute("SELECT * FROM order_concentration ORDER BY date").df()
    conc["date"] = pd.to_datetime(conc["date"])
    anoms = annotate_concentration(anoms, conc)

    composed = compose_revenue_verdict(anoms)
    composed_days = pd.to_datetime(sorted(composed["date"].unique()))
    whale = anoms[anoms["single_order_driven"] & anoms["is_anomaly"]]
    whale_days = pd.to_datetime(sorted(whale["date"].unique()))
    core = _buffer(composed_days.union(whale_days))
    excl = set(pd.to_datetime(list(EXCLUDE_DATES)))
    p90 = _ordinary_day_p90(anoms, core, excl)

    cache = RealDataCache(con)

    elig = {
        "I2": eligible_dates_for("channel", "conversion_rate", "Referral", pool, con, True),
        "I3": eligible_dates_for("channel", "aov", "Direct", pool, con, True),
        "I3b": eligible_dates_for("channel", "aov", "Direct", pool, con, False),
        "I5": eligible_dates_for("device", "conversion_rate", "desktop", pool, con, True),
    }
    i7_sess = set(eligible_dates_for("channel", "sessions", "Organic Search", pool, con, True))
    i7_cr = set(eligible_dates_for("channel", "conversion_rate", "Referral", pool, con, True))
    elig["I7"] = [d for d in pool if d in i7_sess and d in i7_cr]

    out = {}
    out["I1"] = contamination_clean(
        pool, cache, anoms, p90,
        [("sessions", "channel", "Organic Search", ("strong",))])
    out["I2"] = contamination_clean(
        elig["I2"], cache, anoms, p90,
        [("conversion_rate", "channel", "Referral", ("strong", "plausible"))])
    out["I3"] = i3_aov_clean(elig["I3"], cache, anoms, p90)
    out["I3b"] = i3_aov_clean(elig["I3b"], cache, anoms, p90)
    out["I4"] = i4_clean(pool, cache)
    out["I5"] = [d for d in elig["I5"] if _i5_clean_one(cache, anoms, p90, d)]
    out["I6"] = i6_clean(pool, cache)
    out["I7"] = contamination_clean(
        elig["I7"], cache, anoms, p90,
        [("sessions", "channel", "Organic Search", ("strong",)),
         ("conversion_rate", "channel", "Referral", ("strong", "plausible"))])
    out["C1"] = list(pool)
    return out


def _i5_clean_one(cache, anoms, p90, d) -> bool:
    """I5's own pass condition is an ABSOLUTE threshold (desktop within-
    share >= 90%, verdict=strong), not rank+sign - the General Principle's
    factor-(3) case (PLAN.md 5.1). The manufacture check mirrors that
    threshold directly instead of the rank+sign+verdict pattern used
    elsewhere."""
    rdf = cache.results(d, "conversion_rate")
    seg = _dim_segments(rdf, "conversion_rate", "device")
    tgt = seg[seg["segment"] == "desktop"]
    manufactured = False
    if not tgt.empty:
        t = tgt.iloc[0]
        manufactured = bool(t["contribution"] >= 0.90 and t["verdict"] == "strong")
    return not (manufactured or _elevated(anoms, d, "conversion_rate", p90))


# --- 3. Seeded shuffle + bipartite max-matching (Kuhn's algorithm) ---------

def kuhn_match(slots: list, adjacency: dict) -> dict | None:
    """Standard DFS-augmenting-path bipartite matching. `adjacency[slot]`
    is an ORDERED list of candidate dates (order matters - it's what makes
    the result seed-dependent, not an arbitrary implementation detail).
    Returns {slot: date} if every slot is matched, else None."""
    match_date_to_slot: dict = {}

    def try_slot(slot, visited: set) -> bool:
        for date in adjacency[slot]:
            if date in visited:
                continue
            visited.add(date)
            if date not in match_date_to_slot or try_slot(match_date_to_slot[date], visited):
                match_date_to_slot[date] = slot
                return True
        return False

    for slot in slots:
        if not try_slot(slot, set()):
            return None
    return {slot: date for date, slot in match_date_to_slot.items()}


def find_assignment(clean: dict[str, list[pd.Timestamp]], i3b_date: pd.Timestamp,
                    shuffled_order: list[pd.Timestamp]):
    rank = {d: i for i, d in enumerate(shuffled_order)}
    incidents = ["I1", "I2", "I3", "I4", "I5", "I6", "I7", "C1"]
    reserved = {i3b_date}

    for k in (9, 8, 7):
        slots, adjacency = [], {}
        for inc in incidents:
            candidates = sorted((d for d in clean[inc] if d not in reserved),
                                key=lambda d: rank[d])
            for r in range(k):
                slot = (inc, r)
                slots.append(slot)
                adjacency[slot] = candidates
        result = kuhn_match(slots, adjacency)
        if result is not None:
            per_incident = {inc: [] for inc in incidents}
            for (inc, _r), date in result.items():
                per_incident[inc].append(date)
            for inc in incidents:
                per_incident[inc].sort()
            return k, per_incident
    raise RuntimeError("no feasible k in {9, 8, 7} - pool too constrained")


# --- 4. Main ----------------------------------------------------------------

def main():
    con = duckdb.connect(DB, read_only=True)
    pool113, n_composed, n_whale = reconstruct_pool(con)
    assert len(pool113) == 113, f"pool reconstruction gave {len(pool113)}, expected 113"

    dev_dates = load_dev_dates()
    assert len(dev_dates) == 41, f"dev-date set has {len(dev_dates)}, expected 41"

    remaining = [d for d in pool113 if d not in dev_dates]
    assert len(remaining) == EXPECTED_REMAINING_POOL, (
        f"remaining pool is {len(remaining)}, expected {EXPECTED_REMAINING_POOL}")

    clean = compute_clean_pools(remaining, con)
    mismatches = {inc: (len(clean[inc]), EXPECTED_COUNTS[inc])
                 for inc in DEV_INCIDENTS if len(clean[inc]) != EXPECTED_COUNTS[inc]}
    if mismatches:
        raise AssertionError(
            f"clean-pool counts do not match the 2026-09-30 measurement: "
            f"{mismatches} (got, expected)")

    assert len(clean["I3b"]) == 1, "I3b must have exactly one clean date"
    i3b_date = clean["I3b"][0]

    rng = np.random.default_rng(SEED)
    shuffled_order = list(rng.permutation(np.array(remaining, dtype="datetime64[ns]")))
    shuffled_order = [pd.Timestamp(d) for d in shuffled_order]

    k, assignment = find_assignment(clean, i3b_date, shuffled_order)
    assignment["I3b"] = [i3b_date]

    all_dates = [d for dates in assignment.values() for d in dates]
    assert len(all_dates) == len(set(all_dates)), "a date was assigned to more than one incident"
    assert not (set(all_dates) & dev_dates), "a held-out date collides with a dev date"

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()

    payload = {
        "seed": SEED,
        "k": k,
        "k_search_order": [9, 8, 7],
        "method": METHOD_DESCRIPTION,
        "contamination_rule_quote": CONTAMINATION_RULE_QUOTE,
        "pool": {
            "detection_range": [DETECTION_START, DETECTION_END],
            "weekdays_in_range": 201,
            "composed_days": n_composed,
            "whale_days": n_whale,
            "screened_pool_113": len(pool113),
            "dev_dates_removed": len(dev_dates),
            "remaining_pool_77": len(remaining),
        },
        "stage_counts": {inc: len(clean[inc]) for inc in DEV_INCIDENTS},
        "incidents": {inc: [d.strftime("%Y-%m-%d") for d in sorted(assignment[inc])]
                     for inc in DEV_INCIDENTS},
        "git_head": head,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")

    con.close()

    print(f"k={k}  total_dates={sum(len(v) for v in assignment.values())}")
    print(f"stage counts: {payload['stage_counts']}")
    print(f"wrote {OUT_PATH}")
    print(f"git_head={head}")


if __name__ == "__main__":
    main()
