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
COMPONENTS = {
    "sessions":        {"weeks": 12, "z": 4.0, "fp_floor": 4 / 280},
    "conversion_rate": {"weeks":  8, "z": 4.0, "fp_floor": 5 / 308},
    "aov":             {"weeks": 12, "z": 4.0, "fp_floor": 8 / 280},
}

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
