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
