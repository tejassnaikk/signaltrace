# Baseline Characterization — GA360 Google Merchandise Store

Source: `bigquery-public-data.google_analytics_sample`, aggregated to
date x channel x device x country. 38,278 raw rows.

## Usable range
**2016-08-01 to 2017-07-31 (365 days).** August 2017 was a 1-day stub
(2,556 sessions vs ~68K monthly norm) and has been deleted from both tables.
Leaving it in manufactures a false catastrophic incident on the final day.

## Identity
`Revenue = Sessions x ConversionRate x AOV` holds with max error 0.000000000.
Conversion and AOV are derived at query time from summed components; they are
never stored upstream. Multiplicative attribution is therefore exact.

## Dispersion (the central finding)
| metric | mean | median | CV |
|---|---|---|---|
| sessions | 2,469 | 2,391 | **0.29** |
| conversion_rate | 0.013 | 0.013 | 0.44 |
| transactions | 33.1 | 32.5 | 0.50 |
| aov | 120.08 | 108.95 | 0.59 |
| revenue | 4,208 | 3,859 | **0.81** |

Revenue is the NOISIEST series in the tree, not the cleanest. ~33 transactions
per day means it is a small count of heavy-tailed orders. Detecting on revenue
directly gives a p05/p95 band of -85% / +213% against a seasonal-naive
baseline: wide enough to hide any realistic incident.

**Design consequence:** detect on the COMPONENTS (sessions, conversion, AOV),
each with its own threshold, then compose the revenue verdict from component
verdicts. The metric tree is load-bearing for detection, not just explanation.

## Seasonality
Weekday index vs overall mean: Mon 1.22, Tue 1.38, Wed 1.36, Thu 1.23,
Fri 1.18, **Sat 0.27, Sun 0.35**. A Saturday runs ~1/5 of a Tuesday.
Any non-weekday-aware baseline is unusable. Within-weekday CV stays 0.55-0.67,
so the weekday cycle explains the LEVEL but not the SPREAD.

## Trend
None. Monthly revenue oscillates 98K-159K with no direction over 12 months.
No trend adjustment needed; seasonal-naive is sufficient. (Removes the
STL/trend-model component from the plan.)

## Baseline method
Trailing 4-week **median** of the same weekday, not the mean. The mean baseline
produced a systematic -11.3% residual median: right-skew from occasional 20K+
days drags the mean above the typical day. Median removes the offset.

## Extremes are AOV events, not traffic events
| date | revenue | sessions | txns | AOV |
|---|---|---|---|---|
| 2017-04-05 | 27,151 | 2,619 | 37 | **733.80** |
| 2017-02-14 | 23,028 | 2,379 | 51 | 451.54 |
| 2016-09-16 | 20,266 | 2,603 | 39 | 519.65 |
| 2016-11-30 | 13,827 | **4,435** | 58 | 238.40 |

The top revenue days have NORMAL traffic and one huge order. The highest-traffic
day in the top 8 has the lowest revenue of the group. Single-buyer noise must
not be reported as a business incident.

## Data quality exclusions
- **2016-08-03**: 2,890 sessions, 0 transactions, 0 revenue. Traffic normal,
  revenue absent = data gap, not an incident. Exclude from detection and eval.

## Segment viability
| channel | sessions | txns | revenue | usable? |
|---|---|---|---|---|
| Referral | 104,838 | 5,543 | 651,430 | yes |
| Organic Search | 381,561 | 3,581 | 326,381 | yes |
| Direct | 143,026 | 2,219 | 434,841 | yes |
| Paid Search | 25,326 | 479 | 43,559 | yes |
| Display | 6,262 | 152 | 78,337 | traffic-only |
| Social | 226,117 | **131** | 4,917 | traffic-only |
| Affiliates | 16,403 | **9** | 597 | traffic-only |
| (Other) | 120 | 1 | 10 | exclude |

| device | sessions | txns | revenue |
|---|---|---|---|
| desktop | 664,479 | 11,071 | 1,480,864 |
| mobile | 208,725 | 866 | 49,786 |
| tablet | 30,449 | 178 | 9,421 |

Social carries 226K sessions but 131 transactions. Affiliates: 9 transactions
in a year. These segments are viable for SESSION-level anomalies only;
conversion and AOV anomalies there are meaningless. Detection must gate on
minimum transaction volume per segment, not just session volume.

Mobile is 24% of traffic but 3% of revenue. A mobile session is worth far less
than a desktop session, so a traffic mix shift toward mobile depresses blended
conversion with no per-segment change. This is a real confound the
investigation layer must be able to distinguish (Simpson's paradox case).

## Open items for M2
- Minimum-volume floor per segment before a cell is eligible to alert
- Verify robust-z thresholds separately per component (sessions tight, AOV wide)
- False-positive floor on untouched data must be measured per component

---

## Order concentration (added after AOV detector design question)

The daily export carries only SUM(revenue) and SUM(transactions), so AOV is
structurally a mean and cannot be trimmed or winsorized: individual orders are
not present. A second BigQuery pull (`data/raw/order_concentration.csv`, 365
rows) adds per-day order distribution: order_count, max_order, median_order,
top_order_share. Loaded as table `order_concentration`.

### Population distribution of top_order_share
mean 0.271 | median 0.226 | p90 0.462 | p99 0.791 | max 0.852

### Concentration cleanly separates whales from broad-based demand
| date | AOV | txns | revenue | max order | median order | top share |
|---|---|---|---|---|---|---|
| 2017-04-05 | 734 | 37 | 27,151 | **23,130** | 59 | **0.852** |
| 2016-09-16 | 520 | 39 | 20,266 | 16,024 | 48 | **0.791** |
| 2017-02-14 | 452 | 51 | 23,028 | 17,856 | 45 | **0.775** |
| 2016-12-12 | 165 | 85 | 14,010 | 6,997 | 56 | 0.499 |
| 2016-11-30 | 238 | 58 | 13,827 | 4,848 | 45 | 0.351 |
| 2016-08-25 | 220 | 78 | 17,186 | 4,086 | **98** | 0.238 |

Top 3 = ONE buyer (78-85% of the day's revenue, median order still normal at
45-59). Bottom 3 = genuinely broad-based; 2016-08-25 has a median order of 98
vs the usual 45-50, i.e. many customers spending more.

### DECISION: AOV is an independent detector, not attribution-only
Attribution-only was considered and REJECTED: the benchmark includes a pure
AOV incident (-12% with traffic and conversion flat). If AOV cannot fire
independently, that incident is a silent miss.

Instead AOV detects with a wide threshold, then an explanation step
reclassifies single-order-driven spikes as **explained, not incident**. This is
a feature, not a suppression hack: the investigation concludes "driven by one
order representing 85% of daily revenue" and logs it to the evidence ledger.

### The concentration check needs TWO conditions
A share threshold alone misfires on thin days. 2016-10-01: top_order_share
0.790 on just 6 orders totalling 771, max order only 609. High share purely
because the denominator is small, not because a whale exists.

Rule: single-order-driven requires
1. `top_order_share` > ~0.65 (population p90 is 0.462, p99 is 0.791), AND
2. `max_order` large in absolute terms vs the trailing median order
   (2017-04-05: max is ~390x that day's median. 2016-10-01: max is within
   normal order range.)

Same failure mode as segment sparsity: **a ratio without a volume floor lies on
small denominators.** This applies to every ratio metric in the system.

---

## M2 — Detection results and the revised gate

### The gate changed, deliberately
Original gate: "zero alerts on untouched normal periods." **Not achievable and
the wrong target.** At ~33 txns/day the revenue residual spread is irreducible
(p05/p95 -72%/+161%); a detector that never fires on a thin Saturday or a whale
also never fires on a genuine 15% drop. Revised gate: **separability** - a
measured per-component false-positive floor, with injected incidents required
to land outside it.

### Two hypotheses tested and DISCARDED (kept for the writeup)
1. **Heteroscedasticity by weekday.** Predicted weekend residuals would be much
   tighter than weekday. Measured MAD: Sat 152.8 vs Tue 212.5 - only 1.4x, not
   the ~4x needed. Per-weekday scaling changed sessions crossings 37 -> 38.
   No effect. (It DID help revenue: 32 -> 19.)
2. **Short-window lag as a universal fix.** Sessions bias/scale went +0.15 ->
   -0.02 and crossings 37 -> 19 from 4wk to 12wk, confirming it for sessions.
   But revenue bottoms at 8wk and worsens at 12; conversion is flat. Window
   length is NOT a universal fix - it is component-specific.

### Final settings (detection/config.py)
| component | window | threshold | FP floor | measured |
|---|---|---|---|---|
| sessions | 12 wk | z > 4 | 1.4% | 1.4% |
| conversion_rate | 8 wk | z > 4 | 1.6% | 1.8% |
| aov | 12 wk | z > 4 + concentration | 2.9% | 1.4% |
| revenue | - | composed from components | - | 4.6% |

Windows are per-component and justified by measured variance structure:
sessions is a large-N count that drifts slowly (longer window stabilises);
conversion is a bounded ratio that responds fast (longer window adds lag
without reducing noise). AOV lands BELOW its floor because the concentration
check absorbed 4 of its firings.

Cost: the 12-week warm-up burns 84 days. Usable detection range is
**2016-10-24 to 2017-07-31 (281 days)** out of 365. This is the price of the
12-month dataset limit accepted when GA360 was chosen over Olist.

### Concentration check validated
All 4 whales caught: 2017-04-05 (share 0.85, max 395x median), 2017-02-14
(0.78, 399x), 2017-03-24 (0.78, 141x), 2016-11-26 (0.81, **45x**). The last is
the marginal case and the reason BOTH conditions are required - share alone
would also catch thin days with no whale.

### The composed incidents are mostly REAL SEASONALITY, not false positives
13 flagged days, of which ~8 fall into two clusters:
- **2016-12-26 to 2017-01-02**: sessions down 56-65%, 4 days. Christmas. This
  is a corporate merch store; holiday traffic collapse is expected.
- **2016-12-09 to 2016-12-16**: conversion up 185-322%, 4 days. Holiday
  purchasing intent - same traffic, far more buying.

Non-seasonal FP floor is therefore ~5/281 = **1.8%**, genuinely tight.

### Consequence for M3
With only 12 months there is no prior year, so year-over-year seasonality
detection is impossible. Substitute: a **persistence and shape check** - a
single-day deviation is an incident candidate; a multi-day cluster moving
coherently in one direction inside a known low-activity calendar window is a
seasonal regime. This belongs in the INVESTIGATION layer, not the detector.
The detector's job is "sessions dropped 58%"; concluding "it was Christmas" is
a hypothesis that must be tested against evidence.

---

## M3 — Segment viability (measured, changes what is diagnosable)

Only these segments clear the ratio gate (>=5 txns/day) on most days:

| dimension | segment | sessions | txns | pct days ratio-eligible |
|---|---|---|---|---|
| channel | Referral | 104,402 | 5,514 | **91%** |
| channel | Organic Search | 380,215 | 3,575 | **87%** |
| channel | Direct | 142,626 | 2,211 | **55%** |
| device | desktop | 662,737 | 11,028 | **99%** |
| country | United States | 363,457 | 11,396 | **99%** |

Everything else is TRAFFIC-ONLY: Social 2%, Paid Search 3%, Affiliates 0%,
Display 0%, mobile 12%, tablet 1%, and every non-US country 0-4%. Vietnam has
24,578 sessions and **zero** transactions all year.

### Structural pattern
Each dimension is one dominant segment plus a long traffic-only tail. Desktop
carries 11,028 of 12,070 txns; US carries 11,396. A US-localized ratio anomaly
is therefore nearly indistinguishable from the global one.

### Consequences
1. **The hypothesis generator must consult eligibility BEFORE emitting a test.**
   A conversion hypothesis for Social (2% eligible) is statistically
   meaningless. Third occurrence of the volume-floor principle (after the
   concentration check and the segment gates).
2. **Benchmark incidents must target eligible segments for ratio metrics.**
   Injecting a conversion drop into Canada (199 txns/year) produces an incident
   the system correctly cannot diagnose - which tests nothing. Traffic
   incidents may target any reasonably-sized segment.
3. **Mobile sets up the Simpson's paradox case.** 24% of traffic, 3% of
   revenue, 12% ratio-eligible. A traffic mix shift toward mobile depresses
   BLENDED conversion with no per-segment change. Hard diagnosis, strong
   demonstration case if the system handles it.

---

## Scope decision: seasonality discrimination NOT built

A persistence/coherence module was drafted and then removed. Its thresholds
(run length, coherence fraction, score cutoffs) would have been invented rather
than measured, and with only 12 months of data there is no ground truth for
"was this deviation seasonal" against which to validate them.

Every other threshold in this system traces to a measured false-positive floor
or an exact identity. This one could not, so it was cut.

**Consequence, stated rather than hidden:** the engine cannot distinguish a
recurring calendar regime from an incident. The Dec 26 - Jan 2 traffic collapse
and the Dec 9-16 conversion spike remain in the false-positive count. Roughly 8
of 13 flagged days on untouched data are real seasonality the system reports as
anomalies.

This is a dataset limitation, not an implementation gap. Year-over-year
comparison is the standard solution and requires at least two years.

---

## Detection power — PRE-REGISTERED "BEFORE" RESULT (recorded 2026-09-26)

Measured before any incident injector existed, against the M2 detector as
committed. For each component: the fraction of detection-range days
(2016-10-24 to 2017-07-31) on which a clean single-day multiplicative shift
of X% in the actual value, baseline untouched, would cross |z| > 4.

| | -30% | -50% | -70% | -90% | +30% | +50% | +100% | +200% |
|---|---|---|---|---|---|---|---|---|
| sessions | 7% | 15% | 22% | 64% | 11% | 16% | 58% | 94% |
| conversion_rate | 0% | 0% | 0% | 5% | 9% | 15% | 35% | 74% |
| aov | 1% | 0% | 0% | 2% | 7% | 11% | 29% | 67% |

Robust scale: sessions 508.5, conversion_rate 0.004066, aov 38.11 (absolute
units, one MAD pooled across all weekdays).

**Finding: the detector cannot see declines in conversion or AOV.** The
residual is in absolute units with one pooled MAD, and residual spread is
~30-40% of the metric's level, so a 4-sigma drop exceeds 100% of the metric -
impossible for a positive quantity. Consistent with the real data: none of the
13 flagged days is a conversion or AOV decline.

Consequences:
- The -12% AOV incident that justified AOV as an independent detector (see
  "DECISION" above) would fire on 0-1% of days.
- M2's separability gate ("injected incidents land outside the FP floor") was
  never tested; this table is the first measurement of it.
- A device mix-shift incident cannot fire at any magnitude: all-desktop raises
  blended conversion only ~24%; all-mobile lowers it ~66%, an undetectable
  decline.

Decision: detection and diagnosis are scored separately in the M3 benchmark
(diagnosis via forced investigation of the injected metric). Log-space
residuals become milestone M2b after the M3 benchmark passes, re-measured with
the same injector as the "after" result. Inflating incident magnitudes until
they fire is rejected.
