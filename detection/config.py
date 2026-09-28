"""Detection settings, selected empirically from measured false-positive floors.

Every number here was chosen by sweeping windows and thresholds against
UNTOUCHED data (no injected incidents) and reading off the crossing counts.
The sweep lives in notes/baseline.md; do not change these without re-running it.
"""

# Per-component windows. Not uniform, and deliberately so:
#   sessions  - large-N count, drifts slowly -> longer window stabilises it.
#               8wk gives 13 crossings at z>4, 12wk gives 4. 3x reduction.
#   conversion- bounded ratio, responds fast -> longer window adds lag without
#               reducing noise. 8wk: 5 crossings, 12wk: 4. Essentially flat.
#   aov       - heavy-tailed, needs the long window AND the concentration check.
_COMPONENTS_ABSOLUTE = {
    "sessions":        {"weeks": 12, "z": 4.0, "fp_floor": 4 / 280},
    "conversion_rate": {"weeks":  8, "z": 4.0, "fp_floor": 5 / 308},
    "aov":             {"weeks": 12, "z": 4.0, "fp_floor": 8 / 280},
}

# M2b (PLAN.md 3.11): log residual ln(actual)-ln(expected), fixing the
# absolute-residual asymmetry (a decline is bounded at -100%, a rise is not).
# Windows chosen from a real-data bias/crossing sweep, NOT reused from the
# absolute scale above - sessions reconfirms 12wk; conversion_rate and aov
# both genuinely change (4wk, 8wk) because log-space bias is measurably
# smaller there at a shorter window. fp_floor values below are placeholders
# pending the regression-then-real-pipeline re-measurement (PLAN.md 3.11);
# do not treat as final until that re-measurement is recorded.
_COMPONENTS_LOG = {
    "sessions":        {"weeks": 12, "z": 4.0, "fp_floor": 5 / 281},
    "conversion_rate": {"weeks":  4, "z": 4.0, "fp_floor": 4 / 281},
    "aov":             {"weeks":  8, "z": 4.0, "fp_floor": 1 / 281},
}

# M2b, amended (PLAN.md 3.11): pure log-space fixes decline-blindness but
# costs increase-sensitivity everywhere, and specifically misses 3 of the 4
# known AOV whale days (measured: log z=3.15/2.80/2.25 vs the z>4 bar, all
# real spikes the absolute scale caught at z=4.69-15.83). Derived from the
# mechanism, not picked from whichever table looked best: an absolute
# decline is bounded at -100%, so log is the only valid scale for the lower
# tail; log COMPRESSES the upper tail (that is what a log transform does to
# proportional increases), so absolute is kept for the upper tail. Same
# per-component windows as the pure-log/pure-absolute sweeps above - the
# hybrid reuses each scale's own already-frozen window for its own tail,
# not a newly tuned pair.
# fp_floor measured through the real detect_components/annotate_concentration
# path (not a standalone sweep), 281-day detection range, non-suppressed
# firings only - AOV's 4 firings are exactly the 4 known whale days, all
# reclassified explained_single_order, so its non-whale floor is 0/281.
#
# "weeks" IS ALSO SET HERE, aliased to weeks_abs, deliberately - NOT for
# detection (detect_components branches on "weeks_log"/"weeks_abs" and
# ignores this key entirely). This exists because investigation/orchestrator.
# py and investigation/dispersion.py read COMPONENTS[m]["weeks"] directly for
# their OWN, unrelated purpose (segment-level expectation windows, module-
# level at dispersion.py's import time) - a genuinely different use of the
# same COMPONENTS dict than detection's. Without this alias, changing
# RESIDUAL_SCALE to "hybrid" crashes investigation/dispersion.py at IMPORT
# time (KeyError: 'weeks') - found by testing, not assumed. Aliasing to
# weeks_abs keeps investigation/'s window choice IDENTICAL to the absolute
# scale regardless of which RESIDUAL_SCALE is active, so switching detection
# to hybrid cannot silently change segment-level investigation behavior -
# exactly the "no changes to investigation/, segment expectations" scope
# boundary PLAN.md 3.11 was built under.
_COMPONENTS_HYBRID = {
    "sessions":        {"weeks_log": 12, "weeks_abs": 12, "weeks": 12,
                        "z": 4.0, "fp_floor": 5 / 280},
    "conversion_rate": {"weeks_log":  4, "weeks_abs":  8, "weeks": 8,
                        "z": 4.0, "fp_floor": 6 / 281},
    "aov":             {"weeks_log":  8, "weeks_abs": 12, "weeks": 12,
                        "z": 4.0, "fp_floor": 4 / 281},
}

COMPONENTS_BY_SCALE = {"absolute": _COMPONENTS_ABSOLUTE, "log": _COMPONENTS_LOG,
                       "hybrid": _COMPONENTS_HYBRID}

# Single switch for both expectation.py's build_expectation and this file's
# COMPONENTS - toggle to produce the M2b "before"/"after" from one harness,
# no duplicated detection module (PLAN.md 3.11, "config-flag design").
#
# "hybrid" (2026-09-28): the earlier "absolute" default during M2b's build-
# out was never a deliberate design choice - it was the safe holding value
# while the hybrid design was being built and validated on a branch, with no
# stated reason to prefer it permanently. Once fully measured, hybrid
# strictly dominates for this system's purpose: identical reference-date
# ledgers, all 4 whale days still caught and reclassified, decline-detection
# fixed (conversion_rate/AOV at -90%: 5%/2% -> 93%/94%), at a modest,
# already-quantified false-positive cost (composed floor 4.6% -> 5.34%).
# See PLAN.md 3.11 for the full measurement record this default change
# rests on.
RESIDUAL_SCALE = "hybrid"

COMPONENTS = COMPONENTS_BY_SCALE[RESIDUAL_SCALE]

# Revenue has NO independent threshold. At ~33 txns/day its residual spread is
# irreducible (p05/p95 of -72%/+161%), so a revenue-level detector either
# misses real incidents or fires constantly. The revenue verdict is COMPOSED
# from component verdicts. This is why the metric tree is load-bearing for
# detection and not merely an explanation device.
REVENUE_COMPOSED = True

# Single-order concentration check (see notes/baseline.md).
# BOTH conditions required: a share threshold alone misfires on thin days
# (2016-10-01: share 0.79 on 6 orders, max order only 609 = no whale).
CONCENTRATION = {
    "top_order_share_min": 0.65,   # population p90 0.462, p99 0.791
    "max_over_median_min": 20.0,   # 2017-04-05: max is ~390x that day's median
}

# Segment eligibility. A ratio without a volume floor lies on small
# denominators - Social has 226K sessions but 131 txns/year, Affiliates 9.
SEGMENT_GATES = {
    "min_sessions_per_day": 30,
    "min_txns_per_day": 5,      # below this, conversion/AOV are meaningless
}

EXCLUDE_DATES = {"2016-08-03"}  # data gap: 2890 sessions, 0 txns, 0 revenue

# 12-week warm-up burns 84 days. Usable detection range on a 365-day dataset:
DETECTION_START = "2016-10-24"
DETECTION_END = "2017-07-31"


# --- Validator thresholds (JUDGMENT CALLS, not derived from data) -----------
# These are the only numbers in the system chosen by reasoning rather than
# measurement. Flagged as such so the writeup can be honest about it.
VALIDATOR = {
    # Contribution share below which a hypothesis is dropped BEFORE testing.
    # Affiliates is ~1.4% of sessions and falls out automatically here.
    "materiality_floor": 0.05,
    # A hypothesis explaining this much is reported even if its test is
    # marginal -> the "material but statistically uncertain" verdict.
    "high_contribution": 0.30,
    "q_strong": 0.05,
    "q_plausible": 0.10,
    # Benjamini-Hochberg family = (metric, investigation). Sessions hypotheses
    # and conversion hypotheses are different outcome families.
    "bh_family": ("metric",),
}
