# SignalTrace — Build Plan v2 (updated to match actual decisions)

**Autonomous KPI Root-Cause Investigation**

> An analytical engine that detects statistically significant KPI anomalies,
> traverses a metric dependency graph to localize the driver, quantifies each
> factor's contribution, and validates every conclusion against an evidence
> ledger. An LLM agent operates the engine; it does not perform the analysis.

This supersedes the original Olist-based plan. Section 0 (the one rule),
the overall phasing (Phase 1 no-LLM / Phase 2 agent / Phase 3 evaluation),
and the portfolio positioning are unchanged. Everything about the data
source, metric tree, and detection design has been revised based on what
was actually measured while building M1–M3.

---

## 0. The one rule (unchanged)

A feature ships in v1 only if it improves **detection, diagnosis,
attribution, recommendation, or evaluation.**

No Kafka, no Postgres, no Airflow, no Kubernetes, no React, no Slack
integration, no multi-tenancy.

---

## 1. Positioning (unchanged)

| Level | Question | Output |
|-------|----------|--------|
| 1. Detection | What changed? | Sessions are 58.6% below expected |
| 2. Diagnosis | Why did it change? | Social channel traffic collapsed |
| 3. Attribution | How much did it matter? | Social accounts for 64.5% of the decline (−1,553 sessions) |
| 4. Recommendation | What should we do? | Investigate Social campaign/placement changes |
| 5. Grounding | How do we know? | Evidence ledger, BH-corrected significance, negative evidence retained |

Every artifact leads with levels 2 and 3, never level 1.

---

## 2. Data foundation — REVISED

**Decision: GA360 Google Analytics Sample (Google Merchandise Store),
via BigQuery, not Olist.**

Olist was the original choice but is order-level transactional data with
no sessions, so Conversion Rate and Traffic cannot be derived without
inventing them — which would have reintroduced exactly the circularity the
project exists to avoid. GA360 provides real session-level data (Aug 2016
– Aug 2017, 12 months), so every node in the metric tree is grounded in
data that actually exists.

**Cost accepted:** only 12 months, so no year-over-year seasonality
comparison. This is a known, documented limitation (see Section 3.6).

### Metric model (v1) — REVISED, deeper than the original plan

```
                      Revenue
                         │
          ┌──────────────┼──────────────┐
          ↓               ↓              ↓
      Sessions      ConversionRate      AOV
          │               │              │
          └───────────────┴──────────────┘
                          ↓
              Segmentation dimensions
              ├── channel  (Organic Search, Direct, Referral,
              │             Paid Search, Social, Affiliates, Display)
              ├── device   (desktop, mobile, tablet)
              └── country  (top 10 + "Other")
```

Identity: `Revenue = Sessions × ConversionRate × AOV` — verified exact,
max error 0.000000000, computed in log space so factor contributions sum
to exactly 1.0.

No delivery/review/freight evidence metrics (those were Olist-specific,
order-fulfilment signals that don't exist in session data). No seller
dimension (GA360 has no seller concept — this is a single-store dataset).

### Segment eligibility — NEW, not in the original plan

Measured, not assumed: most segments cannot support ratio-metric
hypotheses (conversion, AOV) because they have too few transactions.
Only these clear a >=50%-of-days eligibility gate:

| dimension | ratio-eligible segments |
|---|---|
| channel | Direct, Organic Search, Referral |
| device | desktop only |
| country | United States only |

Every other segment (Social, Paid Search, Affiliates, Display, mobile,
tablet, every non-US country) is **traffic-only** — testable for session
anomalies but not for conversion or AOV. The validator gates on this before
Benjamini-Hochberg: an ineligible segment's contribution is still computed
and kept, but it is never significance-tested — it receives material_untested
or immaterial, never a q-value.

**Correction (2026-09-27, found while fixing I3b's benchmark row, Section 5):**
this section originally justified "kept, not dropped" with "per-dimension
shares must still sum to ~100%". That is an EXACT identity for the additive
metrics (sessions, transactions) — Σ segment_delta = total_delta by
construction, so Σ contribution = 1 always, verified exactly (1.000 across
all three dimensions on real reference dates, notes/baseline.md). It is NOT
true in general for the ratio metrics (conversion_rate, AOV): a ratio
segment's `contribution` field is `within_share` alone (within_effect /
blend_delta) - it excludes both the mix term (its own separate ledger entry)
AND the interaction term (`within + mix + interaction = blend_delta` exactly,
attribution.py, but interaction is never surfaced as its own entry anywhere).
Σ within_share + mix_share = 1 - interaction_total/blend_delta, which only
approaches 1 when interaction_total is small relative to blend_delta - true
on the three dates this was originally checked against (0.697-1.043, all
reasonably close to 1), but not guaranteed generally: on an arbitrary day
where blend_delta happens to be small (real noise, unrelated to eligibility),
interaction_total/blend_delta can dominate. Measured directly on 2017-01-23
(I3b's benchmark date, channel x AOV): the sum is 14.49 (1449%), not ~100%.

**This does not affect the mark-not-drop DESIGN, only its stated
justification for ratio metrics.** Keeping an ineligible segment's Result
(rather than filtering it out) is still correct and still load-bearing - it's
how the ledger avoids silently making an eligible segment's own reported
share look more complete/exhaustive than it is. What was wrong is claiming a
specific NUMERIC property (~100%) as the reason, for a metric class where
that number is an unreported-term-dependent approximation, not an identity.
`investigation/validator.py`'s two comments repeating the same "~100%"
justification (lines ~159, ~217) have the identical inaccuracy and were
corrected alongside this section, same day.

### Attribution method — REVISED, two regimes instead of one

The original plan's single multiplicative-decomposition formula is
insufficient once there are three factors and additive segments within
them. Two separate regimes are used:

**Additive** (sessions, transactions): plain share of the parent delta,
sums to 1 by construction.

**Ratio** (conversion_rate, AOV): within-segment vs. mix decomposition —

```
Δblend = Σ_s [ w_expected × (rate_actual − rate_expected) ]   (within)
       + Σ_s [ rate_expected × (w_actual − w_expected) ]      (mix)
       + interaction
```

This was necessary, not optional: a segment's ratio metric can look
unchanged while the *traffic mix* shifts toward a lower-converting
segment, depressing the blended rate with no per-segment cause
(Simpson's paradox). GA360's mobile segment — 24% of traffic, 3% of
revenue — is the real-data example. Verified by construction: a pure
mix-shift test case shows `within_total ≈ 0` and `mix_total` carrying
100% of the blend change, residual ~1e-19.

**Multiplicative** (revenue factors): log-space decomposition, contributions
sum to exactly 1.0 with no leftover interaction term.

The original plan's single formula (`contribution_s = Δs / Δtotal`) is
still correct and used for the additive case; it was simply insufficient
on its own once ratio metrics and mix effects entered the picture.

---

## 3. Phase 1 — Deterministic engine (no LLM)

**Exit gate: unchanged in principle — the system produces a complete,
correct root-cause report with zero LLM involvement — but the specific
benchmark incidents it must pass are not yet built** (see Section 3.7).

### 3.1 Ingestion — DONE (M1)
BigQuery aggregation query → CSV → DuckDB. `fact_sessions` (date × channel
× device × country) and `metrics_daily` (daily rollup with derived
conversion_rate and aov). Identity verified exact.

### 3.2 Exploratory baseline — DONE (M1), and it changed the design
`notes/baseline.md` documents, with numbers:
- Usable range locked to 2016-08-01 – 2017-07-31 (365 days); the trailing
  1-day stub in Aug 2017 was deleted.
- **Revenue is the noisiest node, not the cleanest** (CV 0.81 vs sessions'
  0.29), because of ~33 transactions/day with a heavy tail. This is the
  central finding that reshaped detection (Section 3.4).
- No trend over 12 months (monthly revenue oscillates 98K–159K, no
  direction) — the plan's trend-adjustment step was dropped as
  unnecessary, and confirmed unnecessary empirically rather than assumed.
- Strong weekday seasonality (Sat 0.27, Sun 0.35 vs weekday 1.18–1.38
  index) — validates the seasonal-naive approach.
- Top revenue days are single-whale orders on normal traffic, not
  business events — led to the order-concentration check (3.3).
- One data gap (2016-08-03: normal traffic, zero transactions) — excluded
  from detection.

### 3.3 Order concentration check — NEW, not in the original plan
A second BigQuery pull of per-day order-level distribution (order count,
max order, median order, top-order share) was needed because AOV's
extremes are single-buyer artifacts, not business events. Two-condition
rule (share > 0.65 AND max-order > 20x the day's median) validated
against four known whale days; reclassifies them as
`explained_single_order` rather than suppressing them silently — this
becomes an evidence-ledger entry, not a hidden filter.

### 3.4 Expectation model — DONE (M2), REVISED
Seasonal-naive: trailing **median** (not mean — the mean produced a
systematic −11.3% bias from right-skew) of the same weekday, over a
**per-component** window rather than one fixed window:

| component | window | reason |
|---|---|---|
| sessions | 12 wk | large-N count, drifts slowly; shorter window left residual bias |
| conversion_rate | 8 wk | bounded ratio, responds fast; longer window only adds lag |
| aov | 12 wk | heavy-tailed; needs the long window AND the concentration check |

No trend term (see 3.2). Robust z-score via median/MAD, unchanged from
the original plan.

### 3.5 Anomaly detection — DONE (M2), gate REVISED
**The original gate ("zero alerts on untouched normal periods") was
tested and found unachievable** given revenue's actual noise floor — a
detector that never fires on a normal thin Saturday or a whale day would
also never fire on a genuine incident. Gate changed, with explicit
sign-off, to **measured separability**: each component has an empirically
determined false-positive floor, and detected incidents must be
demonstrated to fall outside it.

Measured floors (on untouched 2016-10-24 to 2017-07-31, 281 days after
the 12-week warm-up): sessions 1.4%, conversion_rate 1.8%, aov 1.4% (after
the concentration check absorbs whale days), revenue (composed) 4.6%.

**[2026-09-27] These are the absolute-scale floors, kept as the pre-M2b
"before" - see Section 3.11 for the hybrid design's re-measured floors**
(sessions 1.79%, conversion_rate 2.14%, aov 1.42%, composed 5.34%), a real
cost traded for decline-sensitivity, not a free upgrade. **[2026-09-28]
`RESIDUAL_SCALE` now defaults to "hybrid"** (was "absolute" during M2b's
build-out; that was never a deliberate permanent choice, just the safe
holding value while hybrid was being validated on a branch - see Section
3.11's end for the flip and the bug it surfaced). The hybrid floors above
are therefore now the ones that apply by default; the original absolute
floors on this line remain available via an explicit override.

Revenue has **no independent threshold** — its verdict is composed from
which components fired. This is a structural departure from the original
plan's single revenue-level detector, made necessary by 3.2's finding.

### 3.6 Seasonal-regime detection — ATTEMPTED, then explicitly cut
The original plan's "seasonal artifact" hypothesis (comparable prior
periods) is impossible with only 12 months of data — there is no prior
year. A substitute (persistence + cross-segment coherence, scored within
one year) was designed and coded, then **deleted** because its thresholds
(run-length, coherence fraction, score cutoffs) would have been invented
rather than measured, and there is no ground truth against which to
validate them.

**This is now a documented limitation, not a missing feature under
construction.** The engine cannot currently distinguish a recurring
calendar regime (e.g. the Dec 26–Jan 2 Christmas traffic collapse) from a
genuine incident. Roughly 8 of the 13 flagged days on untouched data are
real seasonality reported as anomalies. If revisited, it needs either (a)
a second year of data, or (b) an explicit, separately-validated
calendar-window list treated as advisory context rather than a scored
signal.

### 3.7 Hypothesis generation and validation — DONE (M3), REVISED
Generator enumerates: factor hypotheses (which of sessions/conversion/AOV
moved), segment hypotheses for every segment of every fired metric x
dimension (gated by the eligibility table in Section 2 — an ineligible
segment's contribution is kept for the per-dimension share total but never
significance-tested), and a `mix` hypothesis for
ratio metrics (Section 2). The seasonal hypothesis kind from the original
plan is not generated (3.6); single-order concentration is handled as a
detection-time reclassification (3.3), not a post-hoc hypothesis.

**Validator is contribution-first, not significance-first** — a deviation
from the original plan's implicit significance-first framing, adopted
because ranking by p-value alone let statistically-significant-but-tiny
segments (e.g. Affiliates: 9 transactions/year) outrank the real driver.
Flow: compute contribution → drop below a materiality floor (5%, a
judgment call, not derived) → test survivors → Benjamini-Hochberg within
`(metric)` family → seven-verdict taxonomy:

| verdict | meaning |
|---|---|
| `strong` | contribution ≥ floor, q < 0.05 |
| `plausible` | contribution ≥ floor, q < 0.10 |
| `material_but_uncertain` | contribution ≥ 30%, q ≥ 0.10 |
| `material_untested` | contribution ≥ floor, no valid test exists |
| `unsupported` | tested, contribution ≥ floor, not significant |
| `immaterial` | below the materiality floor, dropped before testing |
| `arithmetic` | mix effect — an exact identity, not a sampled claim |

(Earlier commits called this the "six-verdict" taxonomy; `material_untested`
was added later, making seven.) An eighth ledger label,
`explained_single_order`, is not a validator verdict: the orchestrator writes
it for AOV anomalies the concentration check (3.3) reclassified at detection
time, with basis `threshold`.

**`material_untested` and the `decomposition_only` basis are new,** added
after discovering the original plan's implicit assumption (every
hypothesis can be significance-tested) is false for AOV: AOV is a mean,
not a proportion, and testing it validly requires within-segment order
variance the daily export does not contain. Rather than apply an invalid
test (which was tried and produced silent NaN p-values), AOV segment
hypotheses are reported as decomposition-only with an explicit
"untested" verdict.

**Ranking is per-dimension, never pooled across dimensions** — a bug
found by running on real data: pooling channel + device + country into
one leaderboard double-counts, since each dimension independently
partitions the same population (contributions summed to ~287% on a real
incident before this was fixed).

### 3.8 Evidence ledger — DONE (M3), matches the plan's intent
Append-only. Negative evidence retained (immaterial/unsupported tests are
kept, not discarded) — implements the original plan's stated purpose
exactly: the LLM in Phase 2 can only reference what was actually tested.
Schema differs from the original plan's sketch (adds `basis`, the
seven-verdict taxonomy, and q-values) but serves the same function.

### 3.9 Report generator — NOT STARTED
Unchanged from the original plan's intent: template-rendered from the
ledger, deterministic, the Phase-2 baseline.

### 3.10 Streamlit dashboard — NOT STARTED (M4)
Unchanged in intent from the original plan (KPI header, anomaly timeline,
investigation chain, contribution view, evidence table, incident history)
— but the contribution view must show shares **within each dimension**
(3.7), with direction stated once and absolute values alongside every
share (a display bug found and fixed during M3: `contribution =
Δsegment/Δtotal` is positive whenever segment and total move the same
way, so printing it with a `+` sign beside a metric that *fell* read as
though the segment rose).

### 3.11 M2b — log-space residuals (proposal frozen 2026-09-27, no injector run yet)

Pre-registered in notes/baseline.md ("Detection power," recorded 2026-09-26):
log-space residuals become milestone M2b after M3 passes, to fix the
documented asymmetry that a decline in a positive quantity is bounded at
-100% while a rise is not, so the current absolute-unit residual has no
power to detect real declines in conversion_rate/AOV (measured: 0-5%
detection at -30% to -90%). **Scope: detection only** - no changes to
investigation/, metrics/segments.py's `segment_expectation`, or the
revenue-composition rule; `detect_components`/`annotate_concentration`'s
structure is unchanged, only the residual definition feeding them.

**Residual and window selection, chosen ONLY from a real-data bias and
crossing-rate sweep (same criterion as Section 3.4/3.5), before any
injector run:**

Residual: `ln(actual) - ln(expected)`, same median-of-same-weekday-lags
baseline as today, just computed on log-transformed values. Swept the same
three candidate windows the original design considered (4/8/12 weeks) per
component, over the untouched 281-day detection range:

| component | window | median (bias) | MAD | raw crossings | crossings excl. known real/whale days |
|---|---|---|---|---|---|
| sessions | 4wk | +0.0102 | 0.1221 | 11 (3.9%) | 7 (2.5%) |
| sessions | 8wk | +0.0174 | 0.1557 | 12 (4.3%) | 8 (2.8%) |
| **sessions** | **12wk** | **-0.0046** | **0.2110** | **5 (1.8%)** | **2 (0.7%)** |
| conversion_rate | **4wk** | **+0.0056** | **0.3657** | **4 (1.4%)** | **2 (0.7%)** |
| conversion_rate | 8wk | +0.0437 | 0.3537 | 3 (1.1%) | 2 (0.7%) |
| conversion_rate | 12wk | +0.0325 | 0.3488 | 3 (1.1%) | 2 (0.7%) |
| aov | 4wk | -0.0247 | 0.4351 | 0 (0.0%) | 0 (0.0%) |
| **aov** | **8wk** | **+0.0054** | **0.3880** | **1 (0.4%)** | **0 (0.0%)** |
| aov | 12wk | -0.0104 | 0.3891 | 1 (0.4%) | 0 (0.0%) |

**Frozen: sessions=12wk (unchanged from today), conversion_rate=4wk
(CHANGED from today's 8wk), aov=8wk (CHANGED from today's 12wk).** Sessions
reconfirms the existing choice outright - clearly lowest bias and crossings.
The other two are genuine, measured deviations from precedent, not
reused defaults: in log-space, conversion_rate's bias is smallest at 4wk
(0.0056 vs 0.033-0.044 at 8/12wk) with an essentially-tied crossing rate
(4 vs 3 - not a meaningful difference at these counts); AOV's bias is
smallest at 8wk, and 4wk's zero-crossing result is treated as suspiciously
low (an under-powered/too-conservative window, the same reasoning Section
3.5 used to reject "zero alerts" as a detection goal), not as the best
outcome. z-threshold stays at 4.0, matching the existing uniform convention;
not re-tuned, since the criterion here is about window choice, not the
threshold itself.

**Median's log-space bias, reported explicitly:** all three chosen
windows land under 1% in log-return terms (sessions -0.46%, conversion_rate
+0.56%, aov +0.54%) - comfortably smaller than the -11.3% mean-vs-median
bias that originally justified using the median at all (Section 3.4). The
median-of-lags baseline appears well-behaved in log-space across all three
components; no new bias-correction step is needed.

**Per-weekday scaling, re-tested (not assumed to carry over from the
absolute-residual finding):** MAD ratio to the pooled value, by weekday, at
the frozen windows - sessions 0.60x (Wed) to 1.19x (Tue), conversion_rate
0.81x (Wed/Thu) to 1.46x (Sun), aov 0.79x (Mon) to 1.29x (Sun). Max spread
observed is ~2x (conversion_rate/aov, both driven by Sunday) - still far
short of the ~4x that would justify per-weekday scaling (the same threshold
Section 3.5's original test used, finding 1.4x and rejecting it). **Re-
confirmed, not merely assumed: no per-weekday scaling in log-space either**,
though conversion_rate and AOV both show mildly more weekend heterogeneity
than sessions did originally - worth another look if a later re-measurement
shows the spread growing, not dismissed permanently.

**Pre-registered predictions for the 41 date-metric checks, computed from
segment weights on REAL data - before running the injector on any of
them.** The organizing idea, stated first: **every one of I1/I2/I3/I3b/I7's
mechanisms is a single-segment cut, and detection operates on the AGGREGATE
series, not the segment level - the injected effect gets diluted by
whatever fraction of the business that segment ISN'T** (this is the exact
reason forced investigation exists in the first place, Section 5.1). Using
real per-segment session/transaction shares (notes/baseline.md) and each
component's frozen log-space MAD to convert "how far from the aggregate
baseline would this dilute to" into a z-score:

| incident | mechanism weight in the aggregate | diluted aggregate move | log-move vs. threshold (4x MAD) | prediction |
|---|---|---|---|---|
| I1/I7 (Organic Search sessions) | 43.0% of sessions | -21.5% / -17.2% | \|ln\|=0.24/0.19 vs 0.844 needed | **False** |
| I2/I7 (Referral conversion) | 11.8% of sessions (mix weight) | -4.7% / -3.5% blended CR | \|ln\|=0.05/0.04 vs 1.464 needed | **False** |
| I3/I3b (Direct AOV) | 18.3% of transactions | -5.5% blended AOV | \|ln\|=0.06 vs 1.552 needed | **False** |
| I4 (AOV everywhere, NOT diluted) | 100% | -25% blended AOV | \|ln\|=0.29 vs 1.552 needed | **False** |
| I5 (Desktop conversion) | 73.5% of sessions (largest weight of any incident) | -36.8% blended CR | \|ln\|=0.46 vs 1.464 needed | **False** |
| I6 (mix shift, device->device) | ~22.1% of sessions reallocated | ~-20.7% blended CR (via the mix term, not a within-effect) | \|ln\|=0.23 vs 1.464 needed | **False** |

**Predicted result: none of the 41 checks flip to True from the injected
effect itself** - even I4 (undiluted) and I5 (minimally diluted, the
largest weight of any single-segment incident) fall well short of the
log-space threshold, because the floor itself requires a genuinely large
relative move (roughly -45% to -80% depending on component) to cross z=4,
not because log-space failed to help - it did help (Section on detection
power below), it just isn't a large enough help to rescue THESE specific
magnitudes once diluted.

**One exception, found by checking REAL (uninjected) data directly, not
from dilution arithmetic:** the real, unmodified conversion_rate on
**2017-07-04 already crosses z=-4.00 in log-space on its own**, before any
injection. 2017-07-04 is one of the 41 dates (I7's). Since I7's injected
Referral cut is a decline in the SAME direction, stacking it on an already-
anomalous real background is expected to cross more clearly, not less.
**Predicted exception: I7's conversion_rate check on 2017-07-04 flips to
True** - not because the injected magnitude is large enough alone (it
isn't, per the table above), but because the real background that day
already is. No date change is being made in response to this; it's
reported as a prediction to verify, per instruction.

**Newly-firing real days under the frozen design, checked against the old
exclusion buffer and the 41 dates (detection only, no injection):**

| date | metric (window) | z | in old exclusion buffer? | one of the 41? |
|---|---|---|---|---|
| 2016-12-24, 12-26, 12-30, 12-31, 2017-01-02 | sessions (12wk) | -4.1 to -4.9 | yes (Christmas cluster) | no |
| 2016-12-11 | conversion_rate (4wk) | +4.22 | yes | no |
| 2017-03-04 | conversion_rate (4wk) | +4.97 | yes (already a known real incident) | no |
| **2017-07-01** | conversion_rate (4wk) | -5.13 | **no** | no |
| **2017-07-04** | conversion_rate (4wk) | -4.00 | **no** | **yes (I7)** |
| 2017-04-05 | aov (8wk) | +4.41 | yes (already a known whale day) | no |

7 of 8 new crossings fall inside dates already known/excluded (the Christmas
cluster, 2017-03-04's already-documented real incident, 2017-04-05's
already-documented whale day) - the log-space detector is mostly
rediscovering the same known signal, which is a good consistency check, not
a sign it's firing arbitrarily. **2017-07-01 is genuinely new** - not
excluded, not one of the 41 - and needs the same concentration-check
treatment as any other AOV/conversion candidate before being called
anything (Section 2 of the M2b proposal). **2017-07-04 is the exception
above.** No dates are being changed as a result of this table.

**Config-flag design (proposal, not yet implemented):** `detection/
config.py` gets a `RESIDUAL_SCALE` setting ("absolute" | "log"), each with
its OWN window/floor config (the two scales are not assumed to share window
lengths - they don't, per the sweep above). `detection/expectation.py`'s
`build_expectation` branches on it. The SAME harness
(`detect_components`/`annotate_concentration`/`evaluation/score.py`'s
`detector_fired` check) produces both "before" and "after" by toggling the
flag - no duplicated detection module, no drift between the two.

**Floors and the separability gate still need re-measuring properly once
this is implemented** - the sweep above establishes WINDOW choice; the
actual false-positive FLOOR at z=4 with these windows (the number that
matters for the separability gate, Section 3.5's criterion) is the raw-
crossings percentage already in the table (sessions 1.8%, conversion_rate
1.4%, aov 0.4%) but should be re-verified end-to-end through the real
`detect_components` code path, not just this standalone sweep, before being
called the frozen floor.

**AMENDMENT 2026-09-27 - the text above stays as the frozen proposal record;
this section documents what actually shipped and why, without editing it.**

**Pure log-space was implemented, measured, and found to be a real trade-off,
not a pure win - the amendment exists because of what that measurement
showed.** The synthetic sensitivity table (run once through the real
`build_expectation`/`detect_components` path, not a standalone script):

| component | scale | -30% | -50% | -70% | -90% | +30% | +50% | +100% | +200% |
|---|---|---|---|---|---|---|---|---|---|
| sessions | absolute | 7% | 15% | 22% | 64% | 11% | 16% | 58% | 94% |
| sessions | log | 8% | 26% | 94% | 100% | 0% | 4% | 23% | 84% |
| conversion_rate | absolute | 0% | 0% | 0% | 5% | 9% | 15% | 35% | 74% |
| conversion_rate | log | 1% | 2% | 15% | 93% | 1% | 2% | 4% | 14% |
| aov | absolute | 1% | 0% | 0% | 2% | 7% | 11% | 29% | 67% |
| aov | log | 0% | 2% | 21% | 94% | 0% | 1% | 4% | 12% |

Decline sensitivity transforms exactly as intended (conversion_rate/AOV at
-90%: 5%/2% -> 93%/94%), but increase sensitivity drops by a similar
magnitude in the other direction (conversion_rate/AOV at +200%: 74%/67% ->
14%/12%). **This is the same mechanism in both directions**, not two
unrelated findings: a log transform compresses the upper tail exactly as it
expands the lower one. Concretely, for AOV specifically, this cost 3 of the
4 known whale days their `is_anomaly` flag entirely under pure log-space
(2016-11-26 z=3.15, 2017-02-14 z=2.80, 2017-03-24 z=2.25, all below the z>4
bar; only 2017-04-05 at z=4.35 still cleared it) - they never even reach the
concentration-check stage if they never cross the threshold first.

**Shipped instead: directional hybrid, derived from the mechanism, not from
which table looked best.** Declines use the log residual (bounded-at--100%
problem only exists on the downside); increases use the absolute residual
(log-compression only hurts the upside). Implementation: each tail gets its
OWN `robust_z`, computed from its OWN history in its OWN scale - the two
residuals are never pooled into one MAD (their units differ), only the
resulting z-scores are combined per day, by whichever tail that day's log
residual's sign says it belongs to (`detection/expectation.py`,
`residual_scale="hybrid"`; `detection/anomaly.py`'s loop passes
`weeks_log`/`weeks_abs` instead of a single `weeks` when a hybrid config is
active - the only change to anomaly.py, and it's inside detection/, so the
"detection only" scope holds).

**1. Regression, confirmed exactly, with the hybrid code present but
`RESIDUAL_SCALE="absolute"` (the unchanged default):** 13 composed real-
incident days, 4 whale days, identical dates to the original baseline,
single-component firings on the three reference dates (sessions/
conversion_rate/aov) - byte-for-byte the same as before any of this
existed. M3 benchmark unaffected: 34/36, C1 5/78 (6.4%).

**2. Floors, re-measured through the real `detect_components` path for the
HYBRID config specifically** (not the earlier placeholder values, and not
the pure-log values from the frozen proposal above) - written into
`detection/config.py`:

| component | old absolute floor | hybrid floor | composed |
|---|---|---|---|
| sessions | 1.4% (4/280) | **1.79% (5/280)** | - |
| conversion_rate | 1.8% (5/308) | **2.14% (6/281)** | - |
| aov | 1.4% (8/280) | **1.42% (4/281)** | - |
| revenue (composed) | 4.6% | **15/281 real-incident-days union = 5.3%** (see below) | - |

**3. All 4 whale days confirmed firing and reclassified
`explained_single_order` under hybrid** - z-values identical to the
absolute scale (4.69, 8.43, 6.24, 15.83), because all 4 are increases and
increases route to the absolute-scale test. No whale-detection loss under
the shipped design.

**Composed real-incident days: 15, not 13 - a real, reported shift, not an
error.** Net +2: 2016-12-29 (fired under absolute, does not fire under
hybrid) is dropped; 2016-12-24, 2016-12-31, and 2017-07-01 (none of which
fired under absolute) are added. All three additions are declines picked up
by the new lower-tail sensitivity - 12-24/12-31 fall inside the already-
known Christmas cluster (consistent with real seasonality, Section 3.6);
2017-07-01 is genuinely new, not in the old exclusion buffer, not one of
the 41 dates.

**Per-day margin from the z>4 bar, checked individually rather than folded
into "net +2" - 3 of the 4 are genuine boundary cases, one is not (2026-
09-28):**

| date | metric | hybrid z | margin from 4.0 | boundary case? |
|---|---|---|---|---|
| 2016-12-29 (dropped) | sessions | -3.893 | -0.107 (2.7% short) | **yes** |
| 2016-12-24 (added) | sessions | -4.158 | +0.158 (4.0% over) | **yes** |
| 2016-12-31 (added) | sessions | -4.236 | +0.236 (5.9% over) | **yes, marginally less so** |
| 2017-07-01 (added) | conversion_rate | -4.703 | +0.703 (17.6% over) | **no - comfortable** |

Three of the four sit within ~6% of the threshold on either side - the same
underlying Christmas-cluster signal crossing or not crossing depending on
which residual scale measures it, not three independent findings. 2017-07-01
is the outlier in both directions: it's the only one of the four that's a
clean, unambiguous crossing, and it's also the only one that's a genuinely
new calendar date rather than a threshold flicker inside an already-known
event window. This mirrors I5's 87.96%-vs-90% finding directly: a headline
count ("15, not 13") can hide that most of the underlying change is
boundary noise around one recurring cluster, with a single real new finding
sitting inside it.

**Concentration check: not applicable to any of the 4 changed days.** All
three additions fire on sessions (12-24, 12-31) or conversion_rate (07-01);
the one drop (12-29) also fired on sessions. Concentration only classifies
AOV firings - none of these four are AOV, so there is nothing for it to
check here. **Floor increase, stated as a cost, not just a number**:
sessions 1.4%->1.79% (+28% relative), conversion_rate 1.8%->2.14% (+19%),
aov 1.4%->1.42% (+1%, effectively free since its floor is dominated by
whale reclassification either way), composed 4.6%->5.34% (+16%). Every
component except AOV pays a real increase in false-positive rate for the
decline-sensitivity gain.

**Reference-date ledgers, re-checked under hybrid: unchanged on all
three.** 2016-12-26 (sessions), 2017-03-04 (conversion_rate), 2017-04-27
(aov) - same metric fires under hybrid as under absolute for every one of
them. Unsurprising for 2017-03-04 specifically (a real INCREASE, +564.4%
conversion_rate, which routes to the unchanged absolute test under hybrid
by construction); the other two happen to clear both scales' thresholds
regardless of which one is active.

**2017-07-01, classified via forced investigation (`force=
["conversion_rate"]`):** a genuine, holiday-weekend-adjacent broad-based
conversion collapse, not a concentration/whale artifact. Revenue $85 vs
expected $1,139 (-92.6%); conversion_rate -86.3% and AOV -60.1% collapse
together while sessions is actually +35.8% (people browsing, not buying).
Segment story: United States (99.6% within-share, strong, q=0.0002) and
desktop (71.9%, strong, q=0.012) dominate, matching the site's usual
structural imbalance rather than pointing at a channel-specific cause;
channel dimension is diffuse (mix 36.5%, Referral 36.2% but not
significant), consistent with a broad, cross-channel behavior shift, not a
campaign problem. This date is not one of the 41 and is not being added to
any incident's date list - reported as a real, explainable finding, not
acted on.

**4. Sensitivity table and the injector, each run once, from the real
harness - verified to reproduce the log/absolute tables exactly, then never
re-run:**

| component | -30% | -50% | -70% | -90% | +30% | +50% | +100% | +200% |
|---|---|---|---|---|---|---|---|---|---|
| sessions | 8% | 26% | 94% | 100% | 11% | 16% | 58% | 94% |
| conversion_rate | 1% | 2% | 15% | 93% | 9% | 15% | 35% | 74% |
| aov | 1% | 3% | 21% | 94% | 7% | 11% | 29% | 67% |

Declines match the log column exactly; increases match the absolute column
exactly, for all three components - confirms the hybrid routes correctly,
not just approximately.

**41-date injector run, once, hybrid mode - exactly 1 of 41 flips to
True:** I7's conversion_rate check on **2017-07-04**. Every other check
(40/41) stays False, matching the dilution-based predictions in the frozen
proposal above. The one flip is explained, not a surprise requiring further
investigation: 2017-07-04's real background conversion_rate sits close to
the log threshold on its own (z=-3.65, confirmed via the real pipeline -
retracting an earlier, buggy standalone-script claim of z=-4.00, which did
NOT reproduce here); I7's injected Referral decline stacks in the same
direction and pushes it across. **This is also the same date the holiday
screen below flags as sitting exactly on a US federal holiday** - both
findings point at the same day for related but distinct reasons (a real
calendar effect on the background, and a screening gap in the original
exclusion filter). No date change made.

**5. Holiday screening gap, FIXED 2026-09-28.** The original exclusion
filter (Section 5.1) covered Thanksgiving/Black Friday/Cyber Monday/pre-
Christmas week only - it was never checked against the rest of the US
federal holiday calendar. 8 of the 41 pre-registered dates fell within 3
days of a holiday it didn't cover:

| incident | date | nearest holiday | gap (days) |
|---|---|---|---|
| I3 | 2016-11-11 | Veterans Day | 0 (exact) |
| I7 | 2017-07-04 | Independence Day | 0 (exact) |
| C1 | 2016-11-10 | Veterans Day | 1 |
| I1 | 2017-07-05 | Independence Day | 1 |
| I2 | 2017-01-17 | MLK Day | 1 |
| I2 | 2016-11-14 | Veterans Day | 3 |
| I3 | 2016-11-21 | Thanksgiving | 3 |
| C1 | 2017-01-13 | MLK Day | 3 |

I7 with 2017-07-04: 5/5 (M3 benchmark). I7 without it: 4/4 - the holiday
date wasn't propping up an otherwise-failing result on its own, but two
dates (I3's 2016-11-11, I7's 2017-07-04) sat exactly on a federal holiday,
a genuine gap regardless of whether it happened to change the pass/fail
outcome. No date swaps were made in M2b, per instruction at the time - this
was logged as a known, unaddressed screening gap for a future round, the
same way I3/I3b/I4's channel-dominance fragility was logged before being
fixed in a later round.

**Rebuild, 2026-09-28: `pandas.tseries.holiday.USFederalHolidayCalendar`
replaces the hand-picked Nov/Dec list, same ±3-day buffer as every other
exclusion.** 8 real federal holidays fall in the 281-day detection range -
Veterans Day, Thanksgiving, Christmas(obs), New Year's(obs), MLK Day,
Presidents Day, Memorial Day, Independence Day - vs. the 2 the old filter
ever covered. Pool: 201 weekdays in range → **113** survive (was ~141-143
under the old filter; see the reconciliation note below for why the
remembered range and this round's recomputed baseline differ, and why both
are correct).

**141 vs. 136 reconciliation:** reconstructing the old hand-picked filter
(single dates 11/24, 11/25, 11/28 + pre-Christmas week 12/19-12/23) against
the pre-M2b composed-day set (13 days) reproduces 141, matching the low end
of the remembered 141-143 range. Reapplying the IDENTICAL old filter but
against the CURRENT, post-M2b composed-day set (15 days - M2b added
2016-12-24/12-31/2017-07-01 and dropped 2016-12-29) gives 136, not 141. This
is not a discrepancy to resolve - it's the same pool-size definition
evaluated at two different, both-legitimate points in time. The entire gap
traces to one mechanical fact: 2017-07-01 is relatively isolated (no other
composed day already covers its ±3-day buffer), so adding it to the
composed set removes 5 weekdays (2017-06-28 through 2017-07-04) that
weren't excluded before; December's net change (-1 date, +2 dates, all
clustered near the already-dense 12-26/12-30 buffers) contributes zero net
change since those buffers already overlapped. **136 is the correct current
baseline** (uses the composed-day set actually in force); 141 was a stale,
pre-M2b number, correct for its own time but no longer the right thing to
diff the holiday-calendar fix against. The holiday-calendar fix itself is
measured against 136, not 141: 136 → 113.

**Per-date empirical check (real, uninjected data, not just calendar
proximity) - swap only where an actual effect exists, not just proximity:**

| incident | date | gap | real-data finding | decision |
|---|---|---|---|---|
| I3 | 2016-11-11 (Veterans Day) | 0, exact | Direct is **not** the real top channel AOV mover (Paid Search is, +0.827); Direct's own real contribution is -0.608 | **swap** (exact collision) |
| I7 | 2017-07-04 (Independence Day) | 0, exact | conversion_rate top-level z=-3.65 = 99th percentile of the ordinary-day distribution (n=191/192); on real data alone Referral is already the real top channel mover on BOTH forced metrics (sessions verdict=strong, conversion_rate verdict=plausible) | **swap** (exact collision + clearest measurable effect of all 8 - see the sharper Section 8 finding) |
| I2 | 2017-01-17 (MLK Day) | 1 | Referral's real channel contribution is +3.421, vs. +0.324/+0.697/+0.647 on I2's 3 non-flagged dates - 5-8x the ordinary range | **swap** (measurable effect) |
| C1 | 2017-01-13 (MLK Day) | 3 | sessions z=-2.71 = 94.2nd percentile of the ordinary-day distribution (conversion_rate ordinary, z=+0.60) | **swap** (measurable effect) |
| C1 | 2016-11-10 (Veterans Day) | 1 | sessions z=+2.42 = 89.0th percentile - elevated but just under the p90 line used above; conversion_rate ordinary (z=-1.03); the segment that actually produces this date's one false-strong (device/desktop conversion_rate) shows no elevation of its own | **kept**, borderline, flagged explicitly rather than folded into a clean/unclean binary |
| I1 | 2017-07-05 (Independence Day) | 1 | sessions z=0.97, ordinary; Organic Search real contribution -0.045 (negligible); real top mover is Social, verdict material_but_uncertain (not strong/plausible) - same pattern as I1's other already-vetted dates | **kept**, empirically clean |
| I2 | 2016-11-14 (Veterans Day) | 3 | Referral real contribution +0.429, squarely inside I2's own ordinary range (0.324-0.697) | **kept**, empirically clean |
| I3 | 2016-11-21 (Thanksgiving) | 3 | Direct real contribution +1.138, within I3's own (wide - AOV is heavy-tailed) ordinary range (0.720-3.775) | **kept**, empirically clean |

**Swaps made (all re-verified 5/5 against the incident's own pass condition,
from the rebuilt 113-date pool, no collisions with the other 40 dates):**

| incident | old date | new date | verification |
|---|---|---|---|
| I3 | 2016-11-11 | 2017-03-10 | Direct top-magnitude, correctly signed, verdict=material_untested |
| I7 | 2017-07-04 | 2017-02-01 | sessions strong (q=0.035), conversion_rate strong (q=0.021) - both sub-conditions clear together, the only one of 81 candidates that does (matches this incident's known compound-condition scarcity) |
| I2 | 2017-01-17 | 2017-02-02 | Referral top-magnitude, correctly signed, verdict=strong (q=0.006) |
| C1 | 2017-01-13 | 2017-01-20 | chosen by calendar proximity to the original date (7 days later, same season), deliberately NOT by minimum-noise - re-applying a low-noise-cherry-pick here would violate C1's own stated design principle just above. Verified clean (0/12 material-tested false-strong) but not selected for that property. |

Full M3 benchmark after all 4 swaps: **34/36 unchanged.** C1 false-strong
rate: 3/78 (3.8%) → 3/75 (4.0%) - same 3 false-strongs, smaller denominator
from the 2017-01-13 → 2017-01-20 swap; not a regression, see Section 5.3.

**Scope, confirmed:** `investigation/`, `metrics/segments.py`'s
`segment_expectation`, and the revenue-composition rule (`REVENUE_COMPOSED`,
`compose_revenue_verdict`) are all untouched. The only files changed are
`detection/config.py`, `detection/expectation.py`, and one small,
detection-internal branch in `detection/anomaly.py`'s `detect_components`
loop (reading `weeks_log`/`weeks_abs` instead of `weeks` when a hybrid
config is active).

---

## 4. Phase 2 — Agent layer (unchanged from original plan)

Not started. Evidence-ledger constraint and the "SignalTrace Engine vs
SignalTrace Agent" naming carry over unchanged. **[2026-09-29] LangGraph
orchestration does NOT carry over — dropped for now, see 4.1's "LLM"
bullet.** One addition given 3.7: the agent's tools should expose the
`basis` field (identity / statistical_test / decomposition_only /
threshold) so its narration can distinguish a tested claim from a
decomposition-only one rather than reporting both with equal confidence.

### 4.1 Agent tool contract (frozen 2026-09-29, no agent code yet)

**Task given to both arms:** a date, with all three components (sessions,
conversion_rate, aov) forced. This measures diagnosis, not detection —
the real z>4 detector does not fire on the injected effects at these
magnitudes (notes/baseline.md, "Detection power" sensitivity section;
confirmed directly, Section 5.1's "Detector-fired data point": 0/36
incident-date pairs fired on any forced component).

**Tool design.** Tools are thin wrappers that open the DB `read_only=True`
(or receive the harness's already-injected in-memory connection, with no
indication to the agent that it is injected — the agent cannot tell a
benchmark date from a real one), call the engine, record into a
**harness-owned** `Ledger`, and return JSON-serializable dicts that
include ledger entry ids. **The agent never holds a `Ledger`, a raw
connection, or a `Result` object** — only the JSON a tool call returns.

**Tools (three, no more):**
- `get_factor_decomposition(date)` — the revenue factor table (sessions /
  conversion_rate / aov: actual, expected, pct_change, contribution).
- `test_dimension(metric, dimension)` — calls `validate_segments` for the
  WHOLE dimension (no segment filter — see below) and returns every
  segment row plus the mix row: verdict, basis, eligible, contribution,
  q_value, entry_id.
- `get_ledger_summary()` — read-only, the harness ledger's summary.

**No tool takes a segment filter.** The Benjamini-Hochberg family is one
(metric, dimension) call (`validator.py:277-282`); subsetting segments
before testing changes every surviving q-value (confirmed directly,
2026-09-29's read-only report, Q2), so a per-segment tool would silently
corrupt the exact statistic it exists to expose.

**Forbidden to the agent:** raw SQL; any import from `evaluation/` or
`ground_truth/`; the `incident_id` parameter; `Ledger.record`; computing
shares, contributions, or p-values itself; **reading
`evaluation/results/heldout_engine.json`** (added 2026-10-04, Section 5.6
— the frozen engine's own held-out answers; the agent must not see them,
by the same logic that it must not see `evaluation/ground_truth/`). All
computation happens inside the engine — the agent only ever reads what
`test_dimension` / `get_factor_decomposition` return.

**Final answer schema, identical for both arms:**
- `pattern` in `{localized, broad, mix, none}`
- `claims`: a ranked list, each `(metric, dimension, segment, direction,
  kind in {within, mix}, evidence_ids, strength)`. An empty claims list is
  valid.
- `narrative`: free text.

A programmatic checker rejects any claim whose `evidence_ids` don't
resolve to an actual harness-ledger entry, or whose asserted verdict/basis
differs from that entry's recorded verdict/basis. **Rejected claims are
counted and reported, never silently dropped.**

**The engine's own answer in this schema is derived by a rule
pre-registered in M5, before any agent code exists** — so the
deterministic arm's score doesn't depend on hand-labeling its output after
the fact, and the rule can't be shaped, even unconsciously, by having
already seen what the agent says.

**C1 scoring, both arms:** the count of claims asserted on negative-control
dates; each one is a false positive by definition (C1 has no injection).

**Efficiency** is measured in `test_dimension` calls. Exhaustive coverage
is 9 calls (3 metrics x 3 dimensions), so the headroom for an agent to
economize is small by construction. The stronger hypothesis worth testing
is **cross-dimension reasoning on I5 and I6** (both need a channel/device/
country read together to get the correlated-vs-independent story right,
Section 5.1's I5 redesign note) — stated here as a hypothesis to test in
M6, not a result.

**LLM:** local open-weight model served by Ollama, zero cost. Candidate
`qwen3:8b` (`qwen3:14b` if RAM >= 24GB). The exact model tag and its Ollama
digest are pinned at the start of M6 after a smoke test on dev dates,
stored as one constant, and written into every run's metadata. Temperature
0, fixed seed, 3 runs per date to measure residual nondeterminism. Plain
tool-use loop via the `ollama` Python package; no paid API anywhere in the
project. **LangGraph is dropped for now** (see the pointer above).

**Pinned 2026-10-04 (`agent/config.py`):** model tag `qwen3:8b`, full
digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`,
Ollama server version `0.35.1`. `agent/config.py` re-verifies this digest
against the running server at the start of each evaluation run (via
`_verify_pinned_digest()` called from `evaluation/run_agent.py`'s
`main()`) and fails loudly on any mismatch — a silently re-pulled or
swapped model must never go unnoticed mid-project. `think` (Qwen3's hybrid reasoning mode) is left `False` for
now — a real design choice, deliberately not decided here; it gets
decided on dev dates only, same as everything else model-behavior-related
(never on held-out).

**Smoke test (`agent/smoke_test.py`), run once via the `ollama` Python
package, not curl:** same scenario as an earlier standalone curl check
(2017-02-06, device dimension, conversion_rate) — prompt text re-authored
for this script, not a byte-for-byte reproduction, since this session has
no record of the original wording. Two measurements:
- **2026-10-04 (warm):** one correct tool call
  (`test_dimension(metric="conversion_rate", dimension="device")`), 20.4
  tok/s, 0.0s load — model was already warm within `OLLAMA_KEEP_ALIVE=2h`.
- **2026-10-16 (cold):** same result, **20.6 tok/s, 217.6s cold load**
  from the external USB SSD (~38 MB/s). This is the worst-case first-call
  latency for any evaluation run.

**The agent runner must be resumable.** Each completed (date, run) pair
saved immediately, not batched — a restart must skip already-finished
runs, never recompute them. Not optional: the full evaluation (41 dev
dates × up to 3 runs, eventually the held-out set too) is estimated at
12–17 hours on this machine, long enough that an unresumable run that
dies partway through is a real, not theoretical, risk.

**[Amended 2026-10-07: tool layer design decisions, M6 Part 1]**

- **Date-bound tools (no date argument).** The three tools are methods on
  a `ToolSession` constructed with `(date, con)`.  None of them accept a
  date parameter, so the agent cannot query a different date than the one
  it was given.

- **Single investigate() call, cached slices.** `ToolSession.__init__`
  runs the frozen `investigate()` once with
  `force=["sessions","conversion_rate","aov"]` and caches the result.
  `test_dimension(metric, dimension)` returns a slice of that cache —
  tool outputs therefore equal engine outputs by construction and the
  engine is never touched again.  The frozen engine tag `engine-frozen-m5`
  guarantees no drift.

- **Harness Ledger records only revealed rows.** The fresh `Ledger` inside
  `ToolSession` accumulates entries only as `test_dimension` is called.
  `check_answer()` therefore accepts only evidence the agent actually saw;
  it rejects any claim whose `evidence_id` traces to a (metric, dimension)
  pair that was never revealed in this session.

- **Model pinning and smoke test pending.** `agent/config.py` and
  `agent/smoke_test.py` require Ollama and the local model on the external
  SSD, both currently disconnected.  They are not modified here and will
  be re-validated when the SSD is reconnected.

**[Amended 2026-10-07: agent loop, checker, resumable runner, M6 Part 2]**

- **Cite-not-transcribe answer schema.** `submit_answer` accepts a list of
  `{entry_id, direction, strength}` triples; the loop hydrates
  `metric/dimension/segment/kind/verdict/basis` from the harness ledger.
  The agent never copies content it read from tool output back into its
  answer — it only cites the entry_id it was given.  This prevents
  hallucination of segment names and ensures every claim is traceable to
  a specific ledger row.

- **`submit_answer` counts as a tool call against the budget.**  The budget
  is 12 total tool calls (regular + submit).  Any call beyond 12 — whether
  a regular tool or a submit — transitions the run to `failed_budget`.

- **Malformed submit handling (up to 2 repairs).** If `submit_answer`
  fails schema validation, the error is returned to the model as a tool
  result and `malformed_count` increments.  After 3 malformed submits
  (2 retries exhausted) the run is marked `failed_malformed`.  Semantic
  checker failures (wrong direction, wrong strength, missing entry_id) are
  NOT schema errors — those claims are silently removed from `accepted`
  and the run still completes with `status=ok`.

- **Failed-run scoring rule.** Any run with `status != ok` is treated as a
  total failure: `score_a=None`, all `score_b` flags False, no checker
  counts.  This is the scoring floor; `answer=None` cannot be scored.

- **Heldout guard (two-layer).** `evaluation/run_agent.py --split heldout`
  requires (1) the `--i-confirm-heldout` flag and (2) a `git tag
  agent-frozen` on the current HEAD.  Neither condition alone suffices.
  The dev split has no guard and can be run freely during development.

**[Amended 2026-10-07: direction field, strength "exact", prompt v0 corrections]**

- **direction field in test_dimension rows (one shared definition).**
  Every row returned by `test_dimension` now carries a `direction` field
  ("up"/"down") computed by `_row_direction(e.actual, e.expected)` in
  `agent/tools.py`.  The loop's claim checker imports and calls the same
  function, so the tool output and the validation rule are guaranteed to
  stay consistent — the agent can always satisfy the direction check by
  copying the field directly from the row.

- **strength "exact" for arithmetic mix rows.**  `submit_answer`'s strength
  enum now includes "exact", which maps to verdict "arithmetic".  Before
  this change, mix rows (verdict="arithmetic", kind="mix") could not be
  cited in any claim because no valid strength existed — pattern="mix"
  answers were correct but unclaimable by construction.  "exact" fixes that.

- **prompt v0 kind field corrected: "segment" not "within".**
  `test_dimension` returns `e.kind` directly from the engine, which is
  "segment" for within-segment rows.  The "within" translation only happens
  in the checker's output (accepted claim dict).  The prompt was corrected
  to match what the model actually sees in the tool output.

**[Amended 2026-10-16: verification timing, debug-file rule, first plumbing run]**

- **Digest verification moved to run start.**  `_verify_pinned_digest()` is
  now called at the start of `main()` in `evaluation/run_agent.py`, not at
  import time.  Importing `agent.config` no longer requires Ollama to be
  running, so the test suite and harness code work without a live server.

- **Debug-file safety rule.**  Any `evaluation/run_agent.py` invocation that
  uses `--limit` writes to a separate file
  `evaluation/results/agent_<split>_<chash>_debug.jsonl` and never touches
  the main results file `agent_<split>_<chash>.jsonl`.  Enforced by
  `_build_out_path()` in `run_agent.py`; proved by
  `test_limit_run_writes_to_debug_file_only` in `tests/test_run_agent.py`.

- **First plumbing run (2026-10-16).** `python -m evaluation.run_agent
  --split dev --runs 1 --limit 3` on the first 3 I1 dev dates (2017-01-26,
  2017-03-16, 2017-05-04).  All three runs: **`failed_budget`**.
  Tool-call counts identical across all runs:
  `get_factor_decomposition=2, test_dimension=11, get_ledger_summary=1` = 14
  (budget).  No `submit_answer` was ever called.

  Full trace for run 1 (I1 2017-01-26), 14 calls:

  1. `get_factor_decomposition({date: "2017-01-26"})` ← model passed date arg (ignored)
  2. `test_dimension(sessions, channel)`
  3. `test_dimension(sessions, device)`
  4. `test_dimension(sessions, country)`
  5. `test_dimension(conversion_rate, channel)`
  6. `test_dimension(conversion_rate, device)`
  7. `test_dimension(conversion_rate, country)`
  8. `test_dimension(aov, channel)`
  9. `test_dimension(aov, device)`
  10. `test_dimension(aov, country)`
  11. `get_ledger_summary({})`
  — model then generated a text response (no tool call); nudge sent —
  12. `get_factor_decomposition({})` ← repeated
  13. `test_dimension(conversion_rate, country)` ← duplicate
  14. `test_dimension(aov, country)` ← duplicate
  — budget exhausted; no `submit_answer` —

  **Finding:** with `think=False`, the model completes full 9-call coverage,
  then stalls generating text instead of calling `submit_answer`.  When
  nudged, it loops back to tool calls (including duplicates) rather than
  submitting.  This is the primary failure mode to address in prompt v1.
  Score A/B: all False (failed run).  Run duration: ~9 minutes each (model
  warm; cold-start would add 217.6s to first call only).

**[Amended 2026-10-16: compact tool output, context guard, num_predict=2048]**

- **Root cause of stall identified: context truncation.**  The first plumbing
  run's Ollama log showed `limit=8194` — the model had been loaded with the
  OS default context (8192) before `num_ctx=16384` took effect.  With
  `n_keep=4`, Ollama retains only 4 system-prompt tokens once the conversation
  exceeds 8192 tokens.  By the nudge cycle the model had lost the entire
  `submit_answer` schema and could not call it.

- **Fix: `agent/render.py` — data/rendering separation.**  New module with
  two functions.  `render_test_dimension(rows)` collapses immaterial rows
  (verdict = "immaterial", contribution < 5%, never claimable) to a single
  count note; material rows are returned intact with all fields.
  `render_ledger_summary(entries)` returns `{entry_id, verdict}` pairs only
  (down from 6 fields); immaterial entries excluded.  `agent/tools.py`
  imports these and returns rendered output; raw rows are stored in
  `_revealed` for repeat-call idempotency and checker access.

- **Fix: context guard in `agent/loop.py`.**  At the top of each loop
  iteration, estimates conversation size as `sum(len(json.dumps(m)) for m in
  messages) // 3`.  If estimate exceeds `context_limit` (default 8000),
  returns `status = "failed_context"` immediately.  Prevents silent Ollama
  truncation by failing loudly before the request is sent.

- **Fix: `num_predict=2048` added to OPTIONS.**  Limits model output tokens
  per turn, preventing verbose responses from growing the context unchecked.
  This change also forced an Ollama model reload; the re-run Ollama log
  confirmed `n_ctx_slot = 16384` — the correct context size is now active.

- **Exhaustive transcript size measurement (all 41 dev dates).**
  System prompt + gfd + 9 × td (3 metrics × 3 dimensions) + gls + user
  message chars // 3.  Config hash changed from `bc81b015` to `23ad1ff5`
  (num_predict added to OPTIONS).  Results:

  | stat | tokens |
  |------|--------|
  | max  | 6,828  |
  | median | 5,663 |
  | limit | 8,000 (context_guard) |

  All 41 dev dates pass.  The worst case (I6 2017-03-09, 44 non-immaterial
  ledger entries) uses 6,828 tokens — 1,172 tokens below the guard limit.

- **Re-run (`--limit 3`, `agent_dev_23ad1ff5_debug.jsonl`).** 3 runs on the
  same first 3 I1 dev dates:

  | date | status | note |
  |------|--------|------|
  | 2017-01-26 | `failed_context` | context guard fired after 6 nudge cycles — model calling submit_answer as plain text; text responses + nudges grew context past 8,000 tokens |
  | 2017-03-16 | `failed_error` | 2 tool calls (gfd + td), then HTTP 400 from Ollama |
  | 2017-05-04 | `failed_error` | 0 tool calls; HTTP 400 from Ollama on first request |

  **Truncation check:** 0 new `truncating input prompt` warnings in
  `~/ollama-server.log` (previously 4 from the first run, still only 4 total).
  Context fix confirmed working.

  **New failure modes to investigate before prompt v1:**
  1. Model calls `submit_answer` as plain text (not a tool call) after
     exhausting coverage.  Context is intact (guard would fire after), but
     the model confuses function call syntax with text generation.  This is a
     prompt v0 failure, not a compaction issue.
  2. HTTP 400 errors from Ollama on runs 2–3.  These fail immediately (< 1 ms),
     suggesting malformed requests.  `"error"` field was not stored in the
     record dict; fixed in this amendment — `run_agent.py` now writes
     `"error": result.get("error")` so traceback is visible on next re-run.
     Root cause not yet identified.

- **Test count:** 193 passed (up from 127 at M6 Part 1; up from 188 before
  this amendment).  New tests: `test_h_immaterial_rows_collapsed`,
  `test_h_no_note_when_no_immaterial`, `test_h_ledger_summary_compact`,
  `test_h_context_guard_fires`, `test_h_exhaustive_transcript_under_7000_tokens`.

**[Amended 2026-10-16: claim-level validation, nudge-path bug fix, full trace capture, guard raised to 13,000]**

- **Context guard raised to 13,000 tokens.**  `n_ctx_slot = 16384` confirmed
  active; max exhaustive transcript is 6,828 tokens.  The previous 8,000
  limit risked false `failed_context` on long but legitimate conversations.
  `ChatResult` now carries `prompt_eval_count` and `eval_count` from Ollama;
  `llm_response` trace events record full `tool_calls` list and both counts.

- **Claim-level vs. structural validation (RATIONALE AND RULE).**  An agent
  submitting `strength="material_but_uncertain"` has the correct evidence but
  used a ledger verdict string instead of the agent strength word.  This is an
  over-claimed weak row — the right outcome is a rejected claim, not a failed
  answer.  `_validate_submit_schema` now only checks structural issues:
  missing/wrong `pattern`, `claims` not a list, a claim missing
  `entry_id`/`direction`/`strength`, or unparseable args.  Enum violations
  (`direction` / `strength` values outside the allowed set) are handled in
  `_check_claims` as `invalid_direction` / `invalid_strength` rejections.
  The rest of the answer is kept; status is `ok`.  The repair cycle is
  reserved for answers the loop cannot proceed with at all.

- **Nudge-path HTTP 400 bug fixed.**  The `failed_error` on 2017-05-04 in
  the previous re-run was caused by the model sending plain-text
  `submit_answer(...)` (not a tool call) and then the subsequent nudge path
  calling `ollama.chat()` with a missing model field.  Root cause:
  `OllamaClient.chat()` read `self._config["model_tag"]` from the config dict
  passed at construction; the path that followed a text turn could, in certain
  message-list configurations, produce a 400 from Ollama.  Fix: `chat()` now
  imports `MODEL_TAG`, `OPTIONS`, and `THINK` directly from `agent.config` at
  call time, bypassing the stored dict entirely.  Every ollama.chat() call
  goes through one code path that always includes the pinned model and options.

- **Re-run after these fixes (`--limit 3`, `agent_dev_23ad1ff5_debug.jsonl`),
  all on first 3 I1 dev dates.**

  | date | status | tool_calls | n_rejected (loop) | max prompt_eval_count | wall time | Score A | Score B |
  |------|--------|-----------|-------------------|-----------------------|-----------|---------|---------|
  | 2017-01-26 | `ok` | gfd=1 td=9 gls=1 | 6 (invalid_strength) | 5,775 | ~84s | True | pat✓ top1✓ top3✓ |
  | 2017-03-16 | `ok` | gfd=1 td=9 gls=1 | 5 (invalid_strength) | 5,329 | ~85s | True | pat✓ top1✓ top3✓ |
  | 2017-05-04 | `failed_context` | gfd=1 td=9 gls=1 | 0 | 10,280 | ~18.5min | N/A | N/A |

  **No HTTP 400 errors** in this run.  No new `truncating input prompt`
  warnings (still exactly 4 total, all from the first plumbing run).

  Runs 0–1 now score correctly: the model submitted claims with ledger
  verdict strings as strengths; those were rejected per-claim
  (`invalid_strength`), the single valid claim was accepted, and the answer
  hit Score A and full Score B.

  Run 2 (2017-05-04): the model generates the exact same 693-char plain-text
  `submit_answer(...)` call on every turn (27 text turns, 27 nudges), with no
  variation.  With the nudge-path bug fixed, the model runs indefinitely until
  the context guard fires at ~10,280 prompt tokens.  The model's content
  contains `"strength": "material_but_uncertain"` and
  `"strength": "material_untested"` — showing the same strength-word confusion
  as runs 0–1.  The plain-text call itself is **not** parseable JSON — it uses
  positional Python function syntax (`submit_answer("localized", [...], "...")`)
  rather than a tool call.  **Root cause (prompt v1 issue, not a harness bug):**
  for this date, the model never transitions to calling `submit_answer` as a
  tool call.  The nudge message ("Please call one of the available tools, or
  call submit_answer to finish.") is not sufficient to change this behaviour.
  This is the single issue to address in prompt v1.

- **Rule going forward (PLAN.md 4.2 reconfirmed):** prompt changes driven by
  this model behaviour count as new prompt versions (v1 onward).  Prompt v0
  gets its full 41-date × 3-run dev evaluation unchanged before any v1 change
  is introduced.  The two fixes in this amendment (claim-level validation,
  nudge-path bug) are harness corrections, not prompt changes.

- **Test count:** 196 passed (+3 from previous amendment).  New tests:
  `test_h_invalid_strength_rejected_not_malformed`,
  `test_h_structural_error_still_triggers_malformed`,
  `test_h_ollama_client_model_and_options_present_on_every_call`.

**[Amended 2026-10-16: text-turn cap (failed_no_tool_call) and 20-turn hard cap]**

- **Text-turn cap.**  After 2 consecutive text-only model turns (no tool call),
  `run_agent()` returns `status = "failed_no_tool_call"`.  The first text turn
  still sends one nudge (unchanged wording — the nudge text is part of the
  prompt version and must not change under v0).  The cap fires on the second
  consecutive text turn without a tool call.  The counter resets to 0 whenever
  any tool call is received.

  **Rationale:** The previous re-run on I1/2017-05-04 ran for 18.5 minutes
  (27 consecutive text turns, 27 nudges, all identical) before the context
  guard fired.  At temperature 0, the model produces the same output forever
  once stuck.  Text turns did not count toward the tool-call budget, so the
  context guard was the only bound — too expensive in time and context.  The
  cap makes this failure mode cheap and explicit.

- **20-turn hard cap.**  `run_agent()` now tracks total LLM calls (text + tool
  calls).  Before each `client.chat()` call, if `total_llm_turns >= 20`,
  returns `status = "failed_budget"`.  The exhaustive path with 2 repairs uses
  at most 14 LLM calls; with the text-turn cap, a text loop adds at most 2
  more = 16.  The 20-turn cap provides a second safety layer at minimal cost.

- **Note on prompt version:**  The nudge wording ("Please call one of the
  available tools, or call submit_answer to finish.") is frozen in v0.  Prompt
  changes to address the text-turn failure mode count as v1 onward (PLAN.md
  4.2).  Prompt v0 gets its full 41-date × 3-run dev evaluation first.

- **Run summaries (run_agent.py) now report:**  per-status counts, rejected
  claims broken down by reason (`invalid_strength`, `invalid_direction`,
  `unknown_entry`, `direction_mismatch`, `verdict_mismatch`, `other`), and
  mean/max seconds per run.  `elapsed_s` and `rejected_reasons` stored per
  record.

- **Test count:** 199 passed (+3 from previous amendment).  New tests:
  `test_i_two_text_turns_fails_no_tool_call`,
  `test_i_text_then_tool_resets_counter`,
  `test_i_20_turn_hard_cap_triggers_failed_budget`.

---

## 4.3 Dev results: prompt v0

**123 runs (41 dev dates × 3 runs), config hash `23ad1ff5`, prompt version `v0`.**
Full report: `evaluation/results/agent_dev_23ad1ff5_report.md`.

### a. Status counts

| status | count |
|---|---|
| ok | 102 |
| failed_no_tool_call | 21 |

Per incident (ok / failed_no_tool_call): I1 6/9, I2 12/3, I3 12/3, I3b 3/0, I4 15/0,
I5 15/0, I6 12/3, I7 15/0, C1 12/3.

### b. Score A (all runs; agent vs engine dev)

All runs count toward the denominator (failed_* treated as failures, PLAN 4.1/5.5).
Per-date: a date passes if ≥1 run passes (= run counts / 3 with deterministic runs).

| incident | target | agent runs | agent dates | engine dev |
|---|---|---|---|---|
| I1 | sessions/channel/Organic Search | 6/15 | 2/5 | 5/5 |
| I2 | conversion_rate/channel/Referral | 12/15 | 4/5 | 5/5 |
| I3 | aov/channel/Direct | **0/15** | 0/5 | 5/5 |
| I3b | aov/channel/Direct | 0/3 | 0/1 | 0/1 |
| I5 | conversion_rate/device/desktop | 12/15 | 4/5 | 4/5 |
| I7 | sessions/channel/Organic Search | 15/15 | 5/5 | 5/5 |
| I7 | conversion_rate/channel/Referral | 15/15 | 5/5 | 5/5 |

I3's 0/15 is the single largest agent failure: all submitted claims
rejected per run (all `invalid_strength`), leaving 0 accepted → Score A/top-1 both fail.
I1 loses 9 runs to `failed_no_tool_call` (3 dates), dropping from 6/6 ok-only to 6/15 all-runs.

### c. Score B (all runs; agent vs engine dev)

| incident | agent pat | agent top-1 | agent top-3 | eng pat | eng top-1 | eng top-3 | note |
|---|---|---|---|---|---|---|---|
| I1 | 6/15 | 6/15 | 6/15 | 5/5 | 3/5 | 4/5 | |
| I2 | 12/15 | 9/15 | 12/15 | 5/5 | 5/5 | 5/5 | |
| I3 | 12/15 | 0/15 | 0/15 | 5/5 | 3/5 | 5/5 | |
| I3b | 3/3 | 0/3 | 0/3 | 1/1 | 0/1 | 0/1 | |
| I4 | 0/15 | 0/15 | 0/15 | 0/5 | 0/5 | 0/5 | broad by construction — engine also 0 |
| I5 | 15/15 | 6/15 | 9/15 | 5/5 | 0/5 | 0/5 | agent beats engine on top-1 (device-dim) |
| I6 | 0/15 | 0/15 | 0/15 | 0/5 | 0/5 | 0/5 | mix by construction — engine also 0 |
| I7 | 15/15 | 15/15 | 15/15 | 5/5 | 4/5 | 5/5 | agent beats engine on top-1 |
| C1 | 0/15 | — | — | 0/5 | — | — | correct pattern=none |

I7 both-targets-in-top-3: agent **15/15**, engine 3/5.

### d. C1: false positives and untested claims

| arm | n runs | false positives | untested claims |
|---|---|---|---|
| agent | 15 | 6 | 0 |
| engine dev | 5 | 1 | 13 |

Agent emits no untested claims (model either accepts a valid entry_id or submits nothing);
engine emits 13 (correctly reports decomposition-only rows as `material_untested`).
Agent false-positive rate (6/15 = 0.4/run) is higher than engine (1/5 = 0.2/run).

### e. Rejected claims

729 total, all `invalid_strength`. The model copies ledger verdict strings verbatim
instead of using the four agent-schema strength words (strong/plausible/untested/exact).

| row verdict (inferred) | submitted strength | count | fix |
|---|---|---|---|
| material_but_uncertain | material_but_uncertain | 465 | row not claimable — omit claim |
| material_untested | material_untested | 216 | use strength='untested' |
| unsupported | unsupported | 33 | row not claimable — omit claim |
| arithmetic | arithmetic | 15 | use strength='exact' |

Per incident: I7=168, I3=120, I4=120, I6=102, C1=63, I2=69, I5=48, I1=33, I3b=6.

### f. Tool calls per run

Every run: test_dimension=9, get_factor_decomposition=1, get_ledger_summary=1 → total
11 tool calls/run, exhaustive coverage every time. Total across 123 runs: td=1107, all=1353.

### g. Determinism

34/34 all-ok dates: 3/3 identical (100%). When the model succeeds, it is perfectly
deterministic. 7 dates have all 3 runs failing `failed_no_tool_call` (3 I1 dates,
1 I2, 1 I3, 1 I6, 1 C1).

---

## 4.2 Dev iteration protocol (pre-registered before any real-model run)

**Written 2026-10-07.  This section is a pre-registration: all rules are
fixed now, before the first real-model evaluation run.**

### Prompt iteration budget

At most **5 system-prompt versions** (v0 through v4).  Once this budget is
exhausted no further prompt changes are permitted before the held-out run.

### Evaluation unit

Every prompt version is evaluated on **all 41 dev dates × 3 runs = 123
runs**.  Debugging runs using `--limit` are permitted at any time but do not
count toward version selection and are not reported.

**Amendment 2026-10-18.** Dev versions v1–v4 use **1 run per date (41 runs per
version)** instead of the pre-registered 3.  Justification: v0 measured 34/34
all-ok dates with 3/3 identical answers at temperature 0 and fixed seed —
repeats add no information on dev.  The final frozen configuration's held-out
run keeps 3 runs per date as pre-registered, and determinism is re-measured
there.  Cross-version comparisons use per-date counts.

### Think-mode comparison

After the winning prompt version is chosen, **one** comparison run of
`think=False` vs `think=True` is performed on that prompt, each over the
full 41 dev dates × 3 runs.  No further variants are tested.

### Selection rule

Rank candidates by (highest total dev Score B top-1 count) → tie-break by
(fewer checker-rejected claims across all runs) → tie-break by (fewer
failed runs).  The winning (prompt version, think setting) pair is used for
the held-out run with no further adjustment.

### Freeze and held-out

The selected configuration is committed and tagged `agent-frozen`.  The
held-out run is then executed exactly once via:

```
python -m evaluation.run_agent --split heldout --i-confirm-heldout
```

No re-runs, no parameter changes after that tag.

### Reporting requirement

Every prompt version's full dev results file is retained in
`evaluation/results/`.  The aggregate counts (Score B top-1, Score A by
incident, failed runs, checker-rejected) for **all** evaluated versions are
reported in this PLAN, not only the winner.

### Motivation constraints

Prompt changes between versions may only be motivated by **dev run traces**
(`result["trace"]` from `run_agent.py`).  Examining or reasoning about the
held-out ground truth or the held-out results file before `agent-frozen` is
tagged is a protocol violation.

### Runtime estimate

Each full dev evaluation is approximately 123 agent runs.  On this machine
with the local model, estimated wall time is **4–6 hours**.  Plan overnight
runs; do not interrupt mid-evaluation.

### Prompt version log

#### v0 → v1 (2026-10-18)

Changes (all motivated by v0 dev traces, no other changes made):

1. **`agent/render.py`: `claim_as` column in `test_dimension` rows.**
   Each non-immaterial row now carries a `claim_as` field: `strong`,
   `plausible`, `untested`, `exact`, or `"-"` for non-claimable rows.
   Derived from `_VERDICT_TO_CLAIM_AS` in `render.py`, which is also
   imported by `loop.py` to build `_STRENGTH_TO_VERDICT` — one source of
   truth, no drift possible.
   *Motivation:* 729/729 rejected claims in v0 were `invalid_strength` —
   the model copied ledger verdict strings verbatim instead of the four
   agent-schema strength words.  I3 Score A: 0/15 (0/5 dates) as a result.

2. **`agent/prompt.py`: replace verbose verdict→strength table; add submit
   instruction.**
   The old instruction (`strength must match the row's verdict: strong ->
   "strong" ...`) replaced with: *"To claim a row, copy its claim_as value
   as the strength. Rows with claim_as '-' cannot be claimed."*
   Added: *"Finish by calling the submit_answer tool. Never write your
   answer as text or as code."*
   *Motivation:* same 729 invalid_strength rejections; 21/123 runs
   (`failed_no_tool_call`, 7 dates) where model wrote text instead of
   calling `submit_answer`.

3. **`agent/loop.py`: nudge text.**
   Changed from `"Please call one of the available tools, or call
   submit_answer to finish."` to `"Your last message was text, not a tool
   call. Call the submit_answer tool now with arguments pattern, claims and
   narrative, or call another tool."`
   *Motivation:* same 21 `failed_no_tool_call` runs.

**[Amendment 2026-10-18: v1 run invalidated by harness bug; re-run required]**

The v1 dev run (`evaluation/results/agent_dev_f854dee9.jsonl`, 41 runs, 1 run
per date) was invalidated by a harness bug and must be re-run in full.

**Root cause (UNVERIFIED).** The most likely hypothesis is that
`agent/config.py` exposed `OPTIONS` as a plain mutable `dict` and
`OllamaClient.chat()` passed it by reference (`options=OPTIONS`) to
`ollama.chat()`, which mutated it in place during the I7 2017-02-08 run (24
submitted claims, 610 s), removing the `model` key.  All 9 subsequent runs
then failed on their first `ollama.chat()` call with HTTP 400 "model is
required" in under 600 ms each.  However, the exact mutation path through the
ollama/pydantic library was not demonstrated: `model` and `options` are
separate request fields, so a mutation of `OPTIONS` alone does not explain
why `model` went missing.  The root cause remains unverified; the shared
mutable reference is the only change that distinguishes the 610 s run from
earlier runs.

**Hardening (commit cf611c8).**  `OPTIONS` is now `MappingProxyType`
(immutable at the source) in `agent/config.py`.  `OllamaClient.__init__`
stores `self._config` as a deeply-frozen `MappingProxyType`.
`OllamaClient.chat()` passes `options=dict(OPTIONS)` — a fresh plain dict
copy on every call, so no library can store or mutate the shared reference.
This is defensive hardening against the hypothesised mutation path, not a
proven fix; the immutability change does not demonstrate that shared-reference
mutation caused the 400.

**Instrumentation (commit after cf611c8).**  To catch any recurrence in the
act: `OllamaClient.chat()` asserts the model tag is a non-empty string before
every call and raises a structured `OllamaRequestError` (with `model_sent`,
`options_sent`, `n_messages`, `request_size_chars`, and `turn_index`) on any
`ollama.ResponseError`.  `run_agent()` appends this context to the error
string and returns it as `error_context` in the result dict.
`evaluation/run_agent.py` writes `error_context` to the JSONL record and
implements a circuit breaker: 2 consecutive `failed_error` runs stop the
runner with a message naming the last error.

**Invalid file retained.** `agent_dev_f854dee9.jsonl` renamed to
`agent_dev_f854dee9_INVALID_harness_bug.jsonl` (kept for audit; not used in
any scoring or reporting).

**Re-run policy.** A harness bug that causes any run to fail with an
infrastructure error (not an agent failure) invalidates the **entire** run
file for that version.  Re-run the full version (all 41 dates) from scratch.
Patching only the failed runs is not permitted — the corrupt shared state may
have affected earlier runs' timing or context in ways that are not visible
from the error alone.

**Held-out rule (new).** If a harness bug corrupts the held-out run, the
entire held-out run is invalidated and discarded.  After fixing the bug,
the held-out run is executed exactly once.  Both the invalidated file and the
replacement file are retained in `evaluation/results/` and both are reported
in PLAN.md, clearly labelled invalid and replacement respectively.

**Tests.**  `test_k_config_frozen_single_client_reused_across_runs` verifies:
(1) `OPTIONS` is `MappingProxyType`; (2) `OllamaClient._config` is
`MappingProxyType`; (3) mutations to either raise `TypeError`; (4) every
`ollama.chat()` call across all unusual paths receives a fresh options copy
(`kw["options"] is not OPTIONS`) with correct values.  The test fails on old
code (plain mutable dict) and passes on the fixed code.
`test_l_pre_call_assertion_fires_on_empty_or_none_model` verifies that the
pre-call guard raises `RuntimeError` when `MODEL_TAG` is `""` or `None`.
`test_l_response_error_produces_error_context_with_turn_index` verifies that
a mocked `ResponseError` produces a result with `error_context` containing all
required fields including `turn_index`.
`test_l_circuit_breaker_stops_on_two_consecutive_failed_error` and
`test_l_circuit_breaker_one_error_then_ok_does_not_stop` verify the circuit
breaker behaviour.

---

## 5. Phase 3 — Evaluation — INCIDENT SET NEEDS REVISION

Not started. Pre-registration discipline and metrics (precision/recall/F1,
top-1/top-3, attribution MAE, unsupported-claim rate) carry over in
principle. **Arm structure decided 2026-09-29: two arms, not three** — the
deterministic engine (Section 3) vs. one evidence-constrained LLM agent
(Section 4.1). A raw-LLM-with-SQL floor is optional, attempted only after
the agent arm works, and reported as an untuned reference point, not a
third scored arm.

**The incident table from the original plan must be rewritten** — it was
written for the Olist tree (Orders × AOV, category/state/payment
segments) and does not map onto the GA360 tree. A revised table needs:

- Factor-level incidents on sessions, conversion_rate, aov (not "orders")
- Segment-localized incidents placed **only in eligible segments**
  (Section 2) for conversion/AOV cases — injecting a conversion drop into
  a non-US country would produce an untestable incident, which tests
  nothing
- At least one **mix-shift** incident (traffic reallocated toward mobile
  with no per-segment rate change) — this is a genuinely new incident
  type the Olist plan had no way to construct, and it is the strongest
  demonstration of what the revised attribution method (Section 2) can do
  that a simpler system cannot
- Black Friday-style control case is **not currently diagnosable** as
  "expected seasonal" per 3.6 — either drop this control case, relabel
  its expected outcome as "detected, not classified as seasonal" (honest
  about the limitation), or scope in a minimal calendar-advisory feature
  before M8
- The negative-control incident (an untouched weekday, no injection) must be
  run through the SAME forced-investigation path as every other benchmark
  incident, not just left to the detector. Its purpose is to measure the
  false-strong rate directly (see the OPEN ISSUE below) — a negative control
  that only checks "did the detector fire" would miss overconfident verdicts
  the validator produces on ordinary noise.

The dev/held-out split and the headline two-arm experiment are defined in
full in Section 5.4.

### 5.1 Pre-registered incident set (dates fixed 2026-09-27, no injector code yet)

Eight incidents, five pre-registered weekday dates each (one gets a sixth,
I3b, deliberately ineligible). Dates are chosen from the 281-day detection
range, excluding: all 13 composed real-incident days, all 4 whale days, a
+/-3 calendar-day buffer around each of those (a clean date next to a real
incident is still contaminated by that incident's own trailing-baseline
window), and Thanksgiving/Black-Friday/Cyber-Monday/pre-Christmas week —
real shopping-season dates that never crossed z>4 in this dataset but would
still confound a synthetic incident with genuine seasonal demand. 143 of the
201 weekdays in range survive this filter.

**General principle: rank+sign+verdict vs. an absolute threshold (added
2026-09-27, after I1/I2/I5/I6/I7 each needed a fix, for three different
underlying reasons that kept getting rediscovered incident by incident).**
Three distinct failure modes, all found empirically while re-screening dates
rather than assumed up front:

1. **Shared, noisy denominator.** A %-of-blend_delta or %-of-composed-move
   statistic divides by a quantity summed across every segment in the
   dimension, including ones the incident never touched. Where 3+ segments
   genuinely compete for that denominator (the channel dimension, always, in
   this dataset - six real channels, none negligible, notes/baseline.md
   "Structural pattern"), one untouched segment's ordinary day-to-day noise
   can dominate the denominator and swing the TARGET's %-share arbitrarily,
   in either direction, for reasons that have nothing to do with the
   target's own signal.
2. **Underpowered significance test.** Independent of (1): a segment's own
   sample size (transactions/day) can be too small for its statistical test
   to reach a given q threshold even when the injected effect is real and
   large in %-terms. This is a property of the injected magnitude and the
   segment's volume, not of which date is chosen.
3. **Structurally vacuous competition - the opposite problem.** When one
   segment structurally owns ~90-100% of a dimension EVERY day, not just on
   noisy days (device: desktop; country: United States -
   notes/baseline.md "Structural pattern" and "Segment viability"), a
   %-of-total cap or a rank-against-competitors check is either trivially
   satisfied (rank) or close to mechanically unsatisfiable for whichever
   segment is trying to clear a floor with almost nothing left to divide
   among the real competitors, since there effectively aren't any.

The fix for (1) and (2) is the same: replace the %-of-shared-denominator
statistic with **rank + sign + verdict** - is the target segment the
largest-\|actual-expected\|-magnitude mover in its dimension (a direct
comparison between two segments' own numbers, never divided by a third,
noisy quantity), correctly signed, and does its OWN statistical test
(BH-corrected q, which already accounts for sample size) clear a verdict bar
chosen to match the test's *actual measured power* at the injected
magnitude - not an assumed q<0.05 (see I2's measured pass-rate sweep below).
The fix for (3) is the opposite: **keep an absolute threshold**, because
there is no meaningful competitor to rank against - the informative question
is "does the known-dominant segment clear a high floor," not "is it bigger
than #2," and #2 there is structurally negligible, not noisy, so ranking
against it would be either vacuous or unanswerable.

Applied to the incidents fixed so far:
- **I1** (channel/sessions): channel dimension, factor (1) → rank+sign+verdict.
- **I2** (channel/conversion_rate): factor (1), same as I1, PLUS factor (2) -
  Referral's ~15-20 txns/day underpowers the two-proportion test for a -40%
  cut specifically (measured below) → rank+sign+verdict, verdict bar lowered
  to plausible to match the measured power, not loosened arbitrarily.
- **I5 device/country axes**: NOT factor (1) - desktop/US are structurally
  ~90-100% of real conversion_rate movement every day, factor (3) →
  absolute threshold, correctly left unchanged.
- **I5 channel axis**: factor (1), same as I1/I2 (channel dimension), PLUS a
  mirror-image of factor (3) - only 2-3 channels are EVER ratio-eligible for
  conversion_rate at all, so a %-of-total cap is close to mechanically
  unsatisfiable given how few real competitors exist, independent of noise →
  rank+sign+verdict (a top-2-magnitude-ratio version, since this is a
  no-single-channel-dominates check, not a single named target).
- **I6 device axis**: neither factor (1) nor rank applies - the question is
  "is THIS device's own apparent rate change real," a single quantity judged
  on its own terms, not against a competing segment or a shared blend_delta
  denominator → **verdict alone**, no rank and no %-share, because BH/q
  already accounts for the real day-to-day CV~0.44 sampling noise
  (notes/baseline.md dispersion table) that a raw %-threshold cannot tell
  apart from a genuine effect.
- **I7**: compound of I1's and I2's own channel-dimension factor-(1)+(2)
  reasoning, independently, for each of its two mechanisms.
- **I3** (channel/aov): factor (1), same as I1/I2 (channel dimension) →
  rank+sign (no verdict bar to tune - AOV's verdict is already forced to
  material_untested regardless of q, so only the within-share half needed
  the fix).
- **I4** (channel/device/country, uniform cut): factor (1) again, but
  **neither of the two available fixes transfers** - rank+sign has no single
  named target for a uniform, everywhere cut, and verdict is vacuous because
  AOV is never testable (verdict is always material_untested/immaterial,
  literally incapable of being "strong" regardless of true cause). This
  needed a fourth technique, not one of the three factors above: **measure
  the real noise distribution directly and check the injected date falls
  within it**, rather than against an assumed threshold - the same move
  Section 3.5 already made for top-level detection, extended one level down
  to segment-level AOV noise. See "I4 fixed" note below for the numbers.

| id | injected change | mechanism | dates |
|---|---|---|---|
| I1 | Organic Search sessions −50% | scale sessions AND transactions (⇒ revenue) by 0.5 for that segment only; conversion_rate, AOV held | 2017-01-26, 2017-03-16, 2017-05-04, 2017-06-13, 2017-07-05 (re-screened, see below) |
| I2 | Referral conversion −40% | scale transactions by 0.6 for that segment only; sessions, AOV held | 2016-10-25, 2016-11-14, 2017-01-17, 2017-03-31, 2017-05-22 (re-screened, see below) |
| I3 | Direct AOV −30%, Direct-eligible dates | scale revenue by 0.7 for that segment only; sessions, transactions held | 2016-11-11, 2016-11-21, 2017-02-27, 2017-03-30, 2017-05-01 (re-screened, see below) |
| I3b | Direct AOV −30%, Direct-INELIGIBLE date (pre-registered contrast case) | same mechanism as I3 | 2017-01-23 (date unchanged; **pass condition corrected 2026-09-27** — see below, was NOT actually verified before, and one clause turned out wrong) |
| I4 | AOV −25% everywhere | scale revenue by 0.75 uniformly across every channel/device/country cell | 2016-10-31, 2016-11-30, 2017-01-09, 2017-02-03, 2017-02-28 (unchanged — transfers cleanly once reworded, see below) |
| I5 | Desktop conversion −50% | scale transactions by 0.5 for device=desktop only, every channel/country; sessions held | 2016-11-02, 2016-12-01, 2017-01-10, 2017-02-06, 2017-03-08 (unchanged — transfers cleanly, see below) |
| I6 | Mix shift: 30% of desktop sessions → mobile | move sessions between device=desktop and device=mobile (30% of desktop's actual sessions), each device's own conversion_rate/AOV held at its real value so its transactions/revenue move proportionally; total sessions unchanged | 2016-11-04, 2016-12-02, 2017-01-11, 2017-02-07, 2017-03-09 (unchanged — transfers cleanly, see below) |
| I7 | Compound: Organic Search sessions −40% + Referral conversion −30%, same day | I1's mechanism (factor 0.6) + I2's mechanism (factor 0.7), independently, same date | 2017-02-08, 2017-05-23, 2017-06-08, 2017-06-28, 2017-07-04 (re-screened, see below; one further swap after the Poisson fix, Section 5.3) |
| C1 | Negative control — no injection, forced investigation anyway | — | 2016-11-10, 2017-01-13, 2017-02-09, 2017-03-13, 2017-04-11 (unchanged by design — see below) |

**I5 redesign, flagged not re-approved:** the original proposal was "desktop
AND US" jointly. Dropped — desktop is 91% of transactions and US is 94%, so
their overlap is nearly the whole business, and injecting into both at once
would be nearly indistinguishable from I4-style broad-based. Injecting into
desktop ALONE is more informative: it tests whether the country and channel
dimensions correctly show a CORRELATED (not independently caused) signal,
since desktop skews heavily toward US and toward no one channel in
particular.

**I1 dates re-screened (logged 2026-09-27), after a dry run exposed a real
noise floor the original exclusion filter did not screen for:** forcing
investigation of I1's originally-committed first date (2016-10-24) showed
Organic Search's injected -50% cut (-262 vs expectation) was outranked by
Social's *unmodified, real* day-to-day session noise (+1,038 vs its own
expectation) — verified by running the same forced investigation on the
unmodified data for that date, with no injection at all, and finding Social
already the dominant channel mover. Checked across all 5 originally-committed
I1 dates on real data only: Social (4/5 dates) or Paid Search (1/5) was
already the largest-magnitude channel mover before any injection, every time.
The 3.5/5.1 exclusion filter only screens the *composed top-level* detector
firing; it says nothing about a single channel's own day-to-day volatility,
which is a materially different and more volatile statistic.

Fix, applied to I1 only so far (PLAN.md 5.2 has the corresponding pass-
condition rewrite): re-derive candidate dates from the 143-weekday pool with
an added screen, then verify the result by actually running the injection,
not just the screen. A %-share screen ("target segment's own real share of
its dimension's composed move stays under 20%") was tried first and produces
a biased pool: since share is a ratio over a denominator summed across every
channel, a day can pass a low-target-share screen precisely *because* another
channel's noise inflated the denominator — the opposite of a clean day. Of
the 58 dates that passed the %-share screen, Organic Search's OWN injected
effect turned out to still be smaller in absolute session count than some
other channel's real noise on the large majority of them once actually
simulated. The screen that holds up is on **absolute magnitude**, checked by
running the actual injection (not inferred from real-data shares alone): a
date qualifies only if, after injection, Organic Search is genuinely the
largest-|actual − expected| channel-dimension segment for sessions. 24 of the
58 %-share-screened dates clear this when simulated; I1's 5 dates above are
drawn from those 24, with the 4 that collide with another incident's already-
committed dates (2016-12-01/I5, 2016-12-02/I6, 2017-02-06/I5, 2017-04-11/C1)
excluded to preserve the no-date-reuse rule. All 5 surviving dates cluster in
2017-01 and 2017-03-07; none of the screened, simulation-verified candidates
fall in Oct-Dec 2016 for this incident — Social's real noise floor is
evidently higher in that window. **Oct-Dec absence checked, is seasonal, not sample noise (logged 2026-09-27):**
Social's real |actual-expected| sessions, split Oct-Dec 2016 (n=25) vs
Jan-Jul 2017 (n=115) across the 141-date eligible pool: median 1,130 vs 159.5,
mean 1,040 vs 260 - a Mann-Whitney U test on the absolute-magnitude split gives
p<0.0001. (Its %-SHARE of the day's composed move does NOT differ by period,
p=0.88 - consistent with %-share being an unstable statistic per the finding
above, and with absolute magnitude being the one that actually separates the
periods.) Social's real session volatility is several times larger in
Oct-Dec than the rest of the year, most plausibly holiday-adjacent paid-social
campaign activity; this is a genuine seasonal pattern in this channel, not an
artifact of the 24-candidate sample being small.

I2, I5, I6, I7 checked the same way (re-screen + simulate the actual
injection, don't infer from real-data shares alone) before assuming I1's fix
transfers:
- **I2** (Referral conversion -40%, channel/conversion_rate): same channel-
  dominance fragility as I1, AND a second, different problem - the two-
  proportion test lacks power to reach q<0.05 for a -40% cut at Referral's
  typical daily volume (~15-20 transactions/day). Confirmed two ways: (a) a
  cut-size sweep on one date - -40% -> material_but_uncertain (q=0.14), -70%
  -> strong (q=0.02), -90% -> strong (q=0.001); (b) the actual measured pass
  rate at -40% across the full 105-date non-colliding candidate pool
  (Referral's own verdict, wherever it was the top-magnitude correctly-signed
  mover): **strong on 9/105 dates (8.6%), plausible-or-better on 19/105
  (18.1%)** - the other 66/105 (62.9%) land on material_but_uncertain
  (contribution clears the 30% high-contribution floor, q never approaches
  0.10). On I2's own 5 final dates specifically: 2/5 reach strong, 5/5 reach
  plausible-or-better. This is a power limit of the injected magnitude at
  Referral's sample size, not fixable by picking a different date - the
  8.6%-vs-18.1% gap is the number behind downgrading the verdict bar, the
  same evidentiary standard as I1's re-screen (measure it, don't assume it).
  Dates re-screened (candidates verified by full simulation, not the %-share
  proxy - see I1's note above for why that proxy is unreliable):
  **2016-10-25, 2016-11-14, 2017-01-17, 2017-03-31, 2017-05-22**.
- **I5** (Desktop conversion -50%): device/country sub-conditions left
  UNCHANGED (no re-screen), passing on 3/5 original dates once actually
  scored (evaluation/score.py) rather than manually spot-checked - see 5.2
  for the corrected count and why the other 2 aren't swapped out. The channel
  sub-condition has the same dominance fragility as I1/I2 but needed no date
  changes either - see 5.2.
- **I6** (mix shift): the device-dimension mix/within decomposition transfers
  cleanly UNCHANGED on all 5 original dates once reworded from a %-threshold
  to a verdict check - see 5.2. Needed a new injector mechanism
  (`move_sessions`), not previously built, plus a largest-remainder rounding
  fix (`_largest_remainder_round`) - per-row `round()` was silently discarding
  most of mobile's added transactions because mobile's real conversion rate
  (~1.2%) makes most individual per-row products round to zero, a systematic
  downward bias, not neutral noise.
- **I7** (compound I1+I2 mechanisms, smaller magnitudes: -40%/-30%): both
  sub-conditions must hold on the SAME date, which is rare - only 7 of 104
  non-colliding candidate dates clear both when actually simulated (I7's
  conversion cut is -30%, even smaller than I2's already power-limited -40%,
  so the conversion_rate side is the bottleneck: 9/104 pass alone, vs 77/104
  for the sessions side). Cross-contamination (checking Organic Search's own
  conversion_rate stayed clean) is downgraded to reported, not gated, for the
  same reason as I1's revenue-factor share - only 3/104 dates clear all three
  as strict gates, too few to fill 5 slots. Re-screened dates (before the
  Poisson fix): 2017-02-02, 2017-02-08, 2017-05-23, 2017-06-28, 2017-07-04.
  **After the Poisson fix (Section 5.3), 2017-02-02 was swapped for
  2017-06-08** - final dates: **2017-02-08, 2017-05-23, 2017-06-08,
  2017-06-28, 2017-07-04**.

**I3 fixed 2026-09-27, same treatment as I1 (factor (1), channel dimension):**
"Direct within-share >= 70%" was simulated on I3's 5 original dates: values
ranged +15% to -607% (sign flips included), and Direct was the top-magnitude,
correctly-signed AOV mover on only 1/5 dates (2017-02-27). Re-screened against
Direct-*eligible* dates specifically (I3 requires eligibility, unlike I3b):
36/100 candidates pass full-simulation verification. Dates: **2016-11-11,
2016-11-21, 2017-02-27, 2017-03-30, 2017-05-01**.

**I4 fixed 2026-09-27 — the hardest of the three, and the ONE genuinely new
technique needed this round:** I4's "+/-10pt of baseline revenue share"
tolerance was simulated on its 5 original dates: max within-share across
channels ranged 44.7%-339.7%, outside +/-10pt on every single date. Worse,
its "`CONCENTRATED` must NOT fire" clause - the more important half of the
condition - **false-fired 5/5 when checked** via the actual full
`investigate()` call, not just the channel-share number. `CONCENTRATED`
(orchestrator.py) uses each dimension's top segment's raw
`contribution >= 0.40`, the same fragile statistic breaking I1/I2/I3 (now
logged as its own risk, Section 8).

Neither the I1-style fix (rank+sign+verdict) nor the I6-style fix (verdict
alone) transfers: rank+sign has no single "target segment" to rank for a
uniform, everywhere-cut incident, and verdict is **vacuous** for AOV
specifically - AOV is never testable (`testable=False` always), so its
verdict is always `material_untested`/`immaterial`, NEVER `strong`, whether
the true cause is broad or concentrated. A verdict-based check would pass
trivially regardless of what actually happened, telling us nothing.

What worked: measure the real (no-injection) noise floor directly, the same
move Section 3.5 already made for top-level detection (measured separability
instead of an assumed threshold), applied one level down to segment-level AOV
noise. Real channel-dimension AOV max-\|share\|, across the 140-date pool: MEDIAN 0.93,
p90 5.07, p95 10.2, p99 52.3, MAX 416 - extreme and heavy-tailed, consistent
with AOV already being the noisiest node in the system (notes/baseline.md).
I4's 5 injected dates land at the 11%/30%/22%/84%/25% percentiles of that
real distribution - ordinary, nowhere near the tail. Device (14-76th
percentile) and country (55-91st percentile) check out the same way. **No
date changes needed** - all 5 original I4 dates already pass this cleanly.

**C1 deliberately NOT re-screened:** its whole purpose is to measure how
often ordinary real noise produces a `strong` verdict (Section 5.3's open
issue). Screening its dates for low noise would bias that measurement toward
zero and defeat the point. C1 keeps its original 5 dates unchanged.

**Untouched-segment convention (all incidents):** every cell not named by an
incident keeps its REAL historical value for that date, not its seasonal-naive
expected value. Noise from untouched segments is real noise, and is exactly
what the "no other segment materially implicated" pass criteria below are
checking survives.

**Forced investigation (all incidents):** every incident runs the
segment/dimension validation for its injected metric(s) regardless of
whether `detect_components` would fire on the modified day. Whether the
detector ALSO fires is recorded as a separate detection-power data point,
per the standing decision (baseline.md, "PRE-REGISTERED before result") that
detection and diagnosis are scored separately. For the AOV incidents
specifically (I3, I3b, I4), forced investigation also bypasses the
single-order concentration suppression gate (3.3) — a behavior change
scoped to forced calls only; ordinary (non-forced) investigation still
suppresses single-order-driven AOV days exactly as before.

**Detector-fired data point, measured 2026-09-27 (evaluation/score.py logs
`detector_fired` per date/component):** across every incident-date pair
checked (36 pairs, 41 individual metric-level checks, I1 through I7 - C1 has
no injection so isn't a magnitude prediction to compare against), the real
z>4 detector never fired, on any forced component, on any date. This is a
**confirmed prediction, not a new surprise**: it matches what
notes/baseline.md's "Detection power — PRE-REGISTERED 'BEFORE' RESULT" table
already predicted before any injector existed - session drops at these
magnitudes (a single channel's share of a -40% to -50% cut, not the whole
metric) cross z>4 on a small minority of days at best, and conversion_rate/
AOV cuts of this size essentially never do (0-5% at -30% to -90%). This is
the pre-registered "before" measurement for M2b.

**Dataset-imbalance caveat (I1, I2, I3, I3b, I7 — single-channel incidents):**
desktop and United States already hold ~90%+ of revenue and transactions in
this dataset (notes/baseline.md, "Structural pattern"). Expect them to
ALSO appear as the top device/country segments for any single-channel
incident almost regardless of which channel is targeted or its magnitude —
this is a restatement of the dataset's known imbalance, not evidence the
engine specifically localized the cause to device/country. The CHANNEL
dimension is the only one that's actually informative for these incidents;
CONCENTRATED firing on device+country+channel together is EXPECTED and not
separately scored as a success.

### 5.2 Scoring rubric (numeric tolerances)

Every incident is scored on the injected metric's channel/device/country
decomposition from a forced `investigate()` call. "Dominant segment" means
the injected segment's contribution share is the largest in its dimension.

**Two different numbers, not one (2026-09-27) - do not read "5/5" as a
diagnostic-accuracy result.** For the four re-screened incidents (I1, I2, I3,
I7), dates were selected FROM a candidate pool BECAUSE they satisfy the
pass condition when simulated. Reporting only "5/5 on the selected dates" is
close to tautological - it mainly confirms the injector+orchestrator pipeline
is wired correctly end to end, not that the engine reliably diagnoses these
incidents. The actual diagnostic-accuracy-relevant number is the
**pre-selection pass rate across the full candidate pool**, before any date
was chosen:

| incident | pre-selection pool pass rate (engine-performance figure) | selected-date pass rate (pipeline consistency check only) |
|---|---|---|
| I1 | 24/58 (41.4%) | 5/5 |
| I2 | 19/105 (18.1%) plausible-or-better [9/105, 8.6% at strong] | 5/5 (2 strong, 3 plausible) |
| I3 | 36/100 (36.0%) | 5/5 |
| I7 | 7/104 (6.7%) | 5/5 |

I4, I5, I6, and C1 were never re-screened (dates unchanged throughout), so
this distinction doesn't apply to them - their pass rates (I4 5/5, I5 3/5,
I6 5/5) ARE the diagnostic-accuracy-relevant numbers, since nothing was
selected for passing.

| incident | pass condition |
|---|---|
| I1 | channel: Organic Search is the largest-|actual-expected|-magnitude segment in the channel dimension for sessions, correctly signed (actual < expected), verdict=strong (implies q<0.05) — **revised from a %-share threshold** (see "I1 dates re-screened" note above: %-share is a ratio over a denominator summed across every channel, so a day can pass a share threshold because ANOTHER channel's noise inflated the denominator, not because the target is clean; magnitude comparison doesn't have that failure mode); revenue factor table: sessions' share of the move is **reported, not gated** — same denominator fragility as the channel share, now demoted from a pass condition to a logged number |
| I2 | channel: Referral is the largest-\|actual-expected\|-magnitude segment in the channel dimension for conversion_rate, correctly signed (actual < expected), **verdict>=plausible (q<0.10), not strong** — downgraded from q<0.05 because the two-proportion test lacks power to reach q<0.05 for a -40% cut at Referral's typical ~15-20 txns/day. **Measured, not assumed**: across the 105-date non-colliding candidate pool, Referral's own verdict (where it was top-magnitude+correctly-signed) reached **strong on only 9/105 dates (8.6%)**, vs **plausible-or-better on 19/105 (18.1%)** — a 2.1x gap; the other 66/105 (62.9%) land on material_but_uncertain (contribution clears the floor, q never approaches 0.10). On I2's own 5 final dates specifically: 2/5 strong, 5/5 plausible-or-better. Also confirmed by a cut-size sweep on one date (-40%→q=0.14, -70%→q=0.02, -90%→q=0.001) — this is a power limit of the injected magnitude, not fixable by date choice; mix_effect magnitude is **reported, not gated** — same denominator fragility as I1's revenue-factor share (own real mix noise is unrelated to injection, since I2 never touches sessions) |
| I3 (eligible dates) | channel: Direct is the largest-\|actual-expected\|-magnitude segment in the channel dimension for AOV, correctly signed (actual < expected) — **revised from a %-share threshold**, same fix and same reason as I1 (factor (1)); verdict=material_untested, basis=decomposition_only **unchanged** (AOV has no valid test regardless of eligibility — forced independent of q, so this half never needed fixing) |
| I3b (ineligible date) | channel: Direct's entry is KEPT (present in the results, not filtered out — checked directly against `eligible=False`, not inferred), verdict=material_untested, **basis=ineligible_segment** (not decomposition_only — this is the exact case the RESUME.md priority judgment call is for: Direct here is both ineligible AND has no valid AOV test in principle, and ineligible must win). **Corrected 2026-09-27**: verified directly on 2017-01-23 rather than inherited from I3's status, per instruction not to assume "unaffected." Confirmed: `eligible_segments("channel","aov",asof=2017-01-23)` = [Organic Search, Referral] — Direct is genuinely out, matching baseline.md's documented flicker window (out 2017-01-21, in 01-27); Direct's row is present with verdict=material_untested, basis=ineligible_segment, exactly as claimed. **But the "~100% +/- normal residual" clause was wrong** — never actually checked before this pass. The channel-dimension AOV contribution sum on this date is 14.49 (1449%), not ~100%: this is the SAME shared-noisy-denominator problem (factor (1) above) hitting AOV's ratio_decomposition exactly as it hit conversion_rate elsewhere. Replaced with the property that's actually true and actually load-bearing: Direct's row is present (kept, not dropped), independent of whatever the day's %-sum happens to be — the %-sum itself is dropped as a claim, not fixed, since per the principle above a %-of-blend_delta number is the wrong kind of statistic to assert here at all. |
| I4 | for EVERY dimension (channel, device, country): the injected date's max \|actual-expected\|-share among that dimension's segments falls within the **empirically measured real-noise distribution** for that dimension (below its ~90th-95th percentile) — **revised** from "+/-10pt of baseline revenue share" and "CONCENTRATED must NOT fire" (both broken: simulated on the 5 original dates, max within-share ranged 44.7%-339.7% on every date, and CONCENTRATED false-fired 5/5, since it uses the same fragile raw `contribution>=0.40` statistic — logged separately as its own risk, Section 8). Neither the I1-style rank+sign fix nor the I6-style verdict-only fix transfers here (no single target to rank for a uniform cut; AOV's verdict is always material_untested, never strong, so a verdict check is vacuous) — this needed measuring the real channel-dimension AOV max-share distribution directly (n=140: median 0.93, p90 5.07, p95 10.2, p99 52.3, max 416) and checking the injected date lands inside it, not against an assumed tolerance. Verified on all 5 original dates: channel percentiles 11%/30%/22%/84%/25%, device 14-76%, country 55-91% — all ordinary, no date changes needed. |
| I5 | device: desktop within-share >= 90%, verdict=strong, q<0.05; country: United States within-share >= 70%, verdict=strong — **left unchanged, per-instruction**: both are structural (desktop/US are ~90-100% of real conversion_rate movement on nearly every day, notes/baseline.md "Structural pattern" and "M3 — Segment viability" — not noise to screen out). **Corrected 2026-09-27 after writing evaluation/score.py**: the actual scorer (not a manual read of printed numbers) shows the device/country pair passes on only **3/5 original dates**, not 4/5 as an earlier manual check claimed. The two failing dates, named exactly, with both values (evaluation/score.py output):
  - **2016-12-01**: device contribution=2.017 (way over the 90% floor) but verdict=material_but_uncertain, not strong; country contribution=2.802 (way over its 70% floor) but verdict=material_but_uncertain, not strong. Fails on VERDICT, not magnitude.
  - **2017-01-10**: device contribution=0.8796 (under the 90% floor) with verdict=strong; country contribution=0.9094 (clears its 70% floor) with verdict=strong. Fails on the raw device SHARE NUMBER alone (87.96% < 90%), despite a passing verdict - a different reason than 2016-12-01, previously missed because only the verdict was checked, not the literal threshold.
  Both are logged as expected result variability (Section 5.3 overdispersion), not swapped out, same reasoning as before - now for 2/5 dates, not 1/5. No threshold or date changes made. channel: **revised** from "no single channel >=40% share AND >=2 channels >=15%" to **the largest-magnitude ratio-ELIGIBLE channel's \|actual-expected\| is less than 2x the second-largest's** (same %-share-denominator fragility as I1; only 2-3 channels are ever ratio-eligible for conversion_rate, so a %-of-total cap is close to mechanically unsatisfiable regardless of true diffuseness, a second, distinct problem from I1's). The earlier manual verification of this ratio (1.34/2.08/1.56/1.05/1.12) did not filter to eligible channels first and was wrong; corrected via evaluation/score.py: **1.21/1.86/1.85/1.05/1.12 — all 5 clear <2.0** (the qualitative conclusion, channel is diffuse on every date, is unchanged; only the quoted numbers were wrong). No date changes made to I5 despite the corrected 3/5 - per the same "don't date-shop away noise" reasoning as C1. CONCENTRATED still expected NOT to fire (unchanged reasoning). |
| I6 | device: mix verdict=arithmetic, basis=identity (unchanged — exact identity, always true by construction). **Revised** from "\|within_total\|/\|blend_delta\| < 10%; desktop/mobile within-share each < 10pt" to a **verdict check: neither desktop's nor mobile's own within-effect reaches verdict=strong** — the %-thresholds are unstable because device-level conversion_rate has real day-to-day CV ~0.44 (notes/baseline.md dispersion table) even with NO injection at all (confirmed: desktop's real, uninjected within-share was already 73%-199% of blend_delta on all 5 original dates); the verdict-based version absorbs that noise correctly since BH/q already discounts it. Verified via full injection simulation on all 5 original dates: desktop lands on material_but_uncertain (never strong), mobile on material_untested/immaterial — **no date changes needed**. channel/country within-effects still EXPECTED to show a plausible-looking rate change (unchanged limitation, no pass/fail check there). Needed a new injector mechanism (`move_sessions`) and a largest-remainder rounding fix (`_largest_remainder_round`) — per-row `round()` was silently discarding most of mobile's added transactions, since mobile's ~1.2% real conversion rate makes most individual per-row products round to zero, a systematic bias not neutral noise. |
| I7 | sessions x channel: Organic Search is the largest-magnitude, correctly-signed, verdict=strong segment (same I1 fix); conversion_rate x channel: Referral is the largest-magnitude, correctly-signed segment, **verdict>=plausible** (same I2 power-limit fix — I7's own conversion cut is -30%, even smaller than I2's -40%, so this is the binding constraint: only 9/104 non-colliding candidate dates clear it alone, vs 77/104 for the sessions side); Organic Search's own conversion_rate within-share, and the revenue-factor table's aov/sessions/conversion_rate shares, are **reported, not gated** (same denominator fragility as I1's revenue-factor share; strict-gating all three left only 3/104 viable dates, too few to fill 5 slots). **2017-02-08 is a genuine boundary case post-Poisson-fix** (sessions q=0.048, verdict=strong by a thin margin) — kept, not swapped again, same "don't date-shop away every marginal case" reasoning as I5's two flagged dates below. |
| C1 | not pass/fail — a MEASUREMENT. **Measured 2026-09-27 via evaluation/score.py.** Denominator is MATERIAL TESTED pairs only (testable AND eligible AND contribution clears the materiality floor - i.e. exactly the set that entered validator.py's BH family); immaterial and material_untested pairs are excluded, since counting them would understate the rate among pairs that actually could have come back significant. Across C1's 5 dates x 2 dimensions-of-metric {sessions, conversion_rate} (aov is n/a - not forced for C1): **overall 39/78 material-tested pairs reached verdict=strong (50.0%)**. Per-metric breakdown is sharply uneven: **sessions 38/54 (70.4%)**, **conversion_rate 1/24 (4.2%)** - the false-strong problem is overwhelmingly concentrated in `_poisson_p` (the count-based test), not `_prop_p` (the proportion test), sharpening Section 5.3's overdispersion concern into a specific, testable claim rather than a general one. Given the extreme q-values already observed on real fired dates (q as small as 4.8e-111), **it is pre-registered that some false-strongs are expected** — this measurement exists to quantify the anticonservative-test issue (Section 5.3, below), not to confirm the tests are clean. **Deliberately not re-screened** (see 5.1) — screening for low noise would bias this measurement toward zero and defeat its purpose. |

Ground truth (injected magnitude, target segment, expected share range,
expected verdict/basis per the table above) goes into a file the engine
never reads or imports — planned as `evaluation/ground_truth/<incident_id>.json`,
one file per incident, consumed only by the (not-yet-written) scoring
script. Neither `detection/` nor `investigation/` will ever reference this
path; the values above ARE that file's eventual content, written here first
so the rubric can be reviewed before any code exists.

### 5.3 Open issue

**OPEN ISSUE, not yet fixed (logged 2026-09-26):** the two segment-level
significance tests both assume no overdispersion beyond what their model
predicts — `_poisson_p` (sessions/transactions) assumes session arrivals are
a pure Poisson process (variance = mean); `_prop_p` (conversion_rate) assumes
a fixed per-session conversion probability with no day-to-day or
between-session correlation. Neither assumption has been checked against
this data, and there is direct evidence they are wrong: q-values as extreme
as 4.8e-111 and 2.6e-103 appeared on ordinary segment tests in the reference
dates above. Real q-values that small are implausible for daily
business-metric segments with realistic day-to-day correlation; the more
likely explanation is that both tests are anticonservative here, understating
the true variance and therefore **over-awarding the `strong` verdict**. This
has not been fixed — fixing it (e.g. a dispersion-corrected test, or an
empirical/bootstrap null instead of the closed-form one) is its own scoped
piece of work, not a one-line change. The M3 benchmark (Section 5) should
measure its practical impact directly: the negative-control incident's
false-strong rate is the number that tells us whether this is a paper
concern or one that changes the reported verdicts.

Any `q<0.05` pass condition above (I1, I2, I5, I7) should therefore be read
alongside C1's measured false-strong rate, not treated as independent
confirmation that the localization is correct.

**Measured 2026-09-27 (evaluation/score.py, Section 5.2's C1 row):** the
false-strong rate is NOT evenly split between the two tests. Among material-
tested pairs, `_poisson_p` (sessions) reaches `strong` on 70.4% (38/54) of
tests, while `_prop_p` (conversion_rate) reaches it on only 4.2% (1/24) - a
17x difference, not a matter of degree. **This means `_poisson_p`
specifically is broken, not that both tests need the same fix.** `_prop_p`'s
two-proportion model is, on this evidence, roughly doing what it should:
4.2% is close to the nominal 5% false-positive rate a correctly-calibrated
q<0.05 test should produce on pure noise. `_poisson_p`'s pure-Poisson
variance=mean assumption is the one carrying the anticonservative-test risk
almost entirely on its own. A fix is scoped to `_poisson_p` alone; `_prop_p`
is not implicated and should not be touched.

**FIXED 2026-09-27** (`investigation/dispersion.py`, `_poisson_p` generalized
to a dispersion-corrected Negative Binomial test in `investigation/
validator.py`). Dispersion (phi) is measured PER SEGMENT from a trailing
window strictly before the test date (asof) - never a fixed historical
window, the same leak-free discipline already established for eligibility -
because a single pooled constant would be wrong for most segments: measured
Var(actual)/expected ranges 3.4x (Japan) to 475x (Vietnam) across real
segments, pooled ~85x. Three-tier fallback (own segment / dimension-pooled /
global-pooled), each tier used only once its sample size clears a
bootstrap-measured reliability threshold (own: n>=84, median CV 18.2%;
dimension: n>=300, median CV 18.2%).

**The threshold took two attempts, both measured, not assumed - the second
attempt is the one that shipped:**
1. First: hard cutoff at n_own>=120 (a tighter bootstrap CV, 13.4% median).
   Broke on re-verification: Organic Search on two I7 dates had n_own=96-106
   (just under 120), fell entirely to the dimension-pooled prior (phi~150-200,
   dominated by far noisier channels like Social/Direct) instead of its own
   much calmer history (phi~20-40 once measurable), inflating q to 0.66-0.86
   on a genuinely strong injected signal.
2. Second: empirical-Bayes shrinkage (phi = weighted blend of own and pooled,
   prior strength k=120). Reduced but did not fix the two dates above (q down
   to 0.53-0.83, still nowhere near 0.05), AND introduced NEW regressions on
   dates that were already fine under (1) - a solid n_own~150-250 estimate
   still got pulled substantially toward the much-larger pooled prior.
3. **What shipped**: simply lower the hard cutoff to n_own>=84 - reusing the
   SAME 12-week window already established elsewhere in this codebase
   (`COMPONENTS["sessions"]["weeks"]`, the eligibility window), not a
   separately-tuned number. Noisier per the bootstrap (median CV 18.2% vs
   13.4%) but a noisy OWN estimate beats a confidently-wrong POOLED one.
   Verified head-to-head against both prior attempts on the same dates:

   | design | I1 (5 dates) | I7 sessions-side (6 candidate dates) | C1 sessions false-strong |
   |---|---|---|---|
   | hard cutoff, n>=120 | 4/5 | 4/6 strong | 3/54 (5.6%) |
   | shrinkage, k=120 | 4/5 | 2/6 strong | 0/54 (0.0%) |
   | **hard cutoff, n>=84 (shipped)** | **5/5** | **5/6 strong** | **4/54 (7.4%)** |

   **Not a win on every axis - corrected 2026-09-27, an earlier version of
   this note overclaimed.** I1 and I7 were the data used to DISCOVER the
   n>=120 defect and to CHOOSE n=84 over the alternatives - they are
   selection data here, not an independent test, so their pass counts
   (5/5, 5/6) confirm the fix behaves as designed but are not independent
   evidence it's well-calibrated. **C1 is the only independent check**
   (never used to pick the cutoff), and on C1, n>=84 has the WORST
   false-strong rate of the three (7.4% vs 5.6% vs 0.0%), not the best.
   At n=54 material-tested pairs, 90%-CI binomial ranges for 3/54, 0/54,
   4/54 are [1.5%,13.7%], [0.0%,5.4%], [2.6%,16.2%] - heavily overlapping,
   so C1 alone cannot distinguish these three designs at this sample size;
   the difference should be read as noise, not as n=84 recalibrating worse.
   The actual basis for choosing n=84 is the mechanism-level argument above
   (a noisy own estimate beats a confidently-wrong pooled one built from
   different, far more volatile segments), not the C1 count. **C1 sessions
   false-strong events, broken out by which tier fired** (shipped design):
   own 2/34, dimension 1/9, global 1/11 - spread across tiers, not
   concentrated in one, which is at least consistent with no single tier
   being uniquely broken.

   Both I1 and I7 needed no date changes beyond what re-verification already
   required (I7 swapped its one genuinely-unfixable date, 2017-02-02, for
   2017-06-08 - see Section 5.1/5.2 and the date-retention rule below;
   2017-02-08 is kept, a real boundary case at q=0.048).

**Date-retention rule (stated once, applied uniformly - covers both of
I7's contested dates the same way):** if a candidate clears its required
verdict bar under the CURRENT design, as actually measured, keep it and
report the margin, however thin - do not swap it away in search of a safer
q. Only replace a candidate that fails its bar outright. Applied to I7's two
disputed dates: **2017-02-02** (q=0.1215, verdict=material_but_uncertain)
does NOT clear the "strong" bar -> replaced (with 2017-06-08).
**2017-02-08** (q=0.048, verdict=strong) DOES clear it, by 0.002 -> kept,
margin logged rather than hidden. This is the same principle already applied
to I5's two flagged dates and to C1's dates generally: report what the
measurement actually says, don't curate away genuine boundary cases in
either direction.

**I7's pre-selection pool pass rate, corrected 2026-09-27 - an earlier note
compared the wrong two numbers.** Two conditions were ever measured on the
104-candidate pool, and they must not be crossed:
- **Final design (cross-contamination reported, not gated)** - the ACTUAL
  pass condition (Section 5.2): sess_pass AND cr_pass, nothing else gated.
  Old (uncorrected) pipeline: **7/104 (6.7%)** - this is the figure M3k
  originally reported. New (dispersion-corrected) pipeline: **6/104 (5.8%)**.
  **7/104 -> 6/104 is the correct before/after comparison**: a small
  DECREASE, consistent with the mechanism (dispersion correction makes the
  sessions test appropriately harder to satisfy: sess_pass alone dropped
  77/104 -> 64/104; cr_pass alone is unchanged at 9/104, `_prop_p` untouched).
- **All-three-strict-gated (cross-contamination included as a gate)** - an
  EARLIER, ABANDONED condition design, from before cross-contamination was
  downgraded to reported-only (Section 5.2's revision note): **3/104**, old
  pipeline only, never re-measured post-fix because that design was already
  dropped before I7's dates were chosen.

  An earlier version of this note incorrectly compared the NEW final-design
  figure (6/104) against this OLD, different-condition figure (3/104),
  producing a false "up from 3 to 6" - the real comparison is 7/104 -> 6/104,
  a decrease, and the "3/104" number is not a "pre-fix" measurement of the
  same thing at all.

**The Referral conversion_rate pass-rate question - named cause, not just
"they agree now."** `_prop_p` was never touched, so it cannot explain
anything that changed - and it demonstrably didn't: measured on the SAME
104-candidate POPULATION both times, before vs. after 2017-02-28 is 9.1%
(pre-fix) vs 8.8% (post-fix), and 8.5% (pre-fix) vs 8.6% (post-fix) - stable
across the pipeline change, as expected.

**What the earlier "3/67" figure actually was, precisely reconstructed:**
NOT a third measurement of the same population rate - it counted REMAINING,
UNCLAIMED candidates only. Of the 71 candidates in the 104-pool's
after-2017-02-28 slice, 6 have `cr_pass=True`: 2017-05-17, 05-18, 05-23,
06-08, 06-28, 07-04. Four of those six (05-23, 06-08, 06-28, 07-04) were
already I7's own kept/chosen dates at that point in the search - not
available to "find" again - and 3 of THOSE four are themselves cr_pass=True
hits, so removing them removes 3 hits from a 6-hit, 71-candidate slice,
leaving 3 hits in a ~65-candidate remaining pool (the exact "67" quoted
earlier differs by a small, immaterial bookkeeping delta in which other
incidents' dates were excluded at that point in the session - not a second
population, just a slightly different snapshot of the same one). **The
per-day probability of a candidate clearing cr_pass never changed** (~8.5-
9% throughout); the visible count of REMAINING opportunities shrank because
most of the pool's hits had already been claimed, which is expected and not
evidence of anything code- or calendar-related.

**Full re-verification after the fix, evaluation/score.py:**
- C1, full (not just sessions): overall 5/78 material-tested pairs strong
  (6.4%) - sessions 4/54 (7.4%), conversion_rate 1/24 (4.2%, **unchanged**,
  confirming `_prop_p` was untouched as intended). **[2026-09-28] Further
  improved to 3/78 (3.8%), sessions 2/54 (3.7%)** by a second dispersion fix
  (OWN_MIN_N lowered 84->75, investigation/dispersion.py) - see the module
  docstring for the measurement; kept here as the pre-second-fix number.
- I1: 5/5 (unchanged from before the fix - all "own" tier, q ranging
  6.6e-6 to 3.0e-3).
- I2: 5/5 (unaffected - conversion_rate uses `_prop_p`, not `_poisson_p`).
- I3: 5/5 (unaffected - AOV is never tested at all, `testable=False`).
- I7: 5/5 after the one-date swap above (was 3/5 mid-fix, before the n>=84
  correction and the swap).
- TOTAL across all 8 incidents: **34/36**, identical to the pre-fix total,
  now resting on a dispersion-corrected test instead of an anticonservative
  one - the fix changed WHICH numbers are strong and why, not the headline.

**Fallback-tier usage, logged to the ledger's `detail` field on every
sessions/transactions segment row** (`phi=<value> tier=<own|dimension|
global|poisson>`) so it's auditable per-row, not just in aggregate: across
all 315 sessions/transactions segment-tests run in this benchmark, **272
(86.3%) used "own"**, 19 (6.0%) "dimension", 24 (7.6%) "global", and **0
ever fell to "poisson"** (phi=1, i.e. the original uncorrected test) - the
global pool always had enough history by the time any benchmark date
occurs. Dimension/global usage is concentrated almost entirely in C1
(42 of the 43 non-"own" rows) - C1's dates start earliest in the range
(2016-11-10), before most segments individually clear 84 trailing
observations; I1 and I7's dates are late enough that all but one row used
"own" directly.

**Trailing-window exclusion, verified directly rather than only read from
the code (2026-09-27):** confirmed on 2017-03-04 that the asof date itself
never appears in its own trailing history (`piv.index < asof` is strict);
confirmed the 2016-08-03 data gap (EXCLUDE_DATES) never contributes a valid
(actual, expected) pair to any segment's dispersion history - its own
`expected` is always NaN (it falls inside the 12-week warm-up), and any
LATER date whose 12-lag window happens to include 2016-08-03 (verified for
2016-10-26, exactly 12 weeks after) also gets `expected=NaN` for that date,
so it's excluded too. Both exclusions inherited from
`metrics.segments.segment_expectation`'s existing masking, not new logic
added in dispersion.py - nothing to duplicate or get out of sync.

**Before/after, the three real reference dates (2016-12-26, 2017-03-04,
2017-04-27), sessions x segment, forced investigation - the same dates
already used to validate the eligibility fix, notes/baseline.md):**

*2016-12-26 (real Christmas traffic collapse):*

| segment | contribution | phi | tier | q (old, phi=1) | verdict (old) | q (new) | verdict (new) |
|---|---|---|---|---|---|---|---|
| channel=Social | 64.5% | 175.5 | dimension | 0.0000 | strong | 0.0000 | **strong (unchanged)** |
| channel=Organic Search | 18.3% | 175.5 | dimension | 1.0e-39 | strong | 0.463 | unsupported |
| country=Other | 30.8% | 279.5 | dimension | 2.4e-103 | strong | 0.381 | material_but_uncertain |
| country=Vietnam | 13.5% | 279.5 | dimension | 3.0e-111 | strong | 0.381 | unsupported |
| country=Thailand | 10.5% | 279.5 | dimension | 2.5e-86 | strong | 0.381 | unsupported |
| country=Turkey | 5.9% | 279.5 | dimension | 3.4e-39 | strong | 0.560 | unsupported |
| device=desktop | 102.7% | 229.2 | global | 0.0000 | strong | 1.0e-4 | **strong (unchanged)** |

Vietnam/Thailand/Turkey here directly supersede notes/baseline.md's
"strong (unchanged)" claim for the same three segments (the M3i eligibility-
fix verification table, line ~323) - that claim was correct about the
ELIGIBILITY fix (it didn't touch their eligibility status) but is now stale
about their CURRENT verdict, since this later fix does change it. Annotated
in baseline.md with a footnote pointing back here rather than rewritten, to
keep that table's historical record of the eligibility fix intact.

*2017-03-04 (real conversion_rate +564.4% incident):*

| segment | contribution | phi | tier | q (old) | verdict (old) | q (new) | verdict (new) |
|---|---|---|---|---|---|---|---|
| channel=Organic Search | 62.9% | 25.9 | own | 0.0511 | plausible | 0.737 | material_but_uncertain |
| country=Other | 86.4% | 108.0 | own | 4.0e-6 | strong | 0.837 | material_but_uncertain |
| device=desktop | 104.2% | 411.1 | own | 1.1e-4 | strong | 0.908 | material_but_uncertain |

*2017-04-27:*

| segment | contribution | phi | tier | q (old) | verdict (old) | q (new) | verdict (new) |
|---|---|---|---|---|---|---|---|
| channel=Direct | 63.5% | 75.8 | own | 1.6e-38 | strong | 0.340 | material_but_uncertain |
| country=United States | 47.7% | 50.8 | own | 9.4e-10 | strong | 0.530 | material_but_uncertain |
| country=Germany | 16.1% | 6.9 | own | 3.9e-14 | strong | **0.019** | **strong (unchanged)** |
| device=desktop | 57.5% | 296.6 | own | 3.0e-7 | strong | 0.666 | material_but_uncertain |

Pattern across all three: almost every segment that was "strong" under the
old test downgrades once dispersion is corrected - most to
`material_but_uncertain` or `unsupported`, not to nothing (contribution
still counts, it's the confidence that changes). A few survive comfortably
(Social and desktop on 2016-12-26; Germany on 2017-04-27) - these are the
segments whose signal is large enough to clear even the honest, higher
variance bar, which is the intended distinction: the fix should separate
"real and large" from "real-looking only because the test understated its
own uncertainty," not flatten everything to the same verdict.

**Residual limitation, not fully resolved (logged here and in Section 8 -
do not read the fix above as closing this out):** the mechanism that broke
I1/I7's early dates - a segment whose OWN dispersion is much lower than its
DIMENSION pool's, falling back to that pool before its own history clears
84 observations - still exists structurally for any segment/date
combination where that gap is large enough and the own-history sample size
is small enough. Lowering the threshold from 120 to 84 fixed the specific
cases found in this benchmark; it narrows the window in which the defect can
occur, it does not remove the defect's underlying mechanism. A future
incident targeting an early-history date, or a segment whose own dispersion
happens to be unusually far from its dimension's, could reproduce the same
overcorrection. This is logged as a known, residual risk (Section 8), not
claimed as closed.

### 5.4 Held-out set (rule frozen 2026-09-29; dates frozen 2026-10-01 — see end of section)

**Dev set = the existing 41 benchmark dates (Section 5.1).** All agent
development — prompt iteration, tool debugging, any human judgment about
whether an output "looks right" — happens only against these 41 dates.
They are not held out and were never intended to be: they were re-screened
and swapped by hand repeatedly throughout M3/M3m specifically because they
needed to reliably exercise the pass conditions being designed.

**Held-out set = a seeded random draw from the 113-date screened pool
(Section 3.11's `USFederalHolidayCalendar` rebuild) minus the 41 dates
already used**, subject to:
- the same holiday / composed-day / whale-day ±3-day buffers already
  built into the 113-date pool;
- the same per-incident eligibility constraints as the dev set (e.g. I3
  requires its target segment eligible as-of that date; I3b requires it
  ineligible as-of that date; every incident's mechanism-specific
  structural requirement, Section 5.1);
- the same real-data empirical check used throughout M3m: uninjected data
  checked in BOTH directions for the target segment/metric — a real effect
  that would sabotage a true pass, or one that would manufacture a false
  one — not just calendar proximity to a known date or holiday.

**No screening on engine pass or fail.** The dev set WAS screened this
way, explicitly and repeatedly (Section 5.1), and its "5/5" figures are
documented as a pipeline-consistency check, not a diagnostic-accuracy
number, precisely because of that screening (Section 5.2's "two different
numbers, not one"). Screening the held-out set the same way would make its
headline number exactly as circular as the number it exists to correct.

**Not yet decided — count per incident, the seed, and whether dates may be
shared across incidents** (I7's dev-set precedent shares a date across two
mechanisms by logic, not by reusing another incident's date; whether that
transfers to the held-out draw is undecided). These are decided only after
measuring what the 113-minus-41 pool actually supports — I7's compound
condition alone leaves very few candidates even in the dev pool (Section
5.1: 6.7%, 7/104) — and the frozen result is written to
`evaluation/heldout_select.py` **before any agent code exists**, the same
measure-then-freeze discipline used for every other design choice in this
project (Sections 3.5, 3.11, 5.1).

**Headline numbers are held-out only, reported as counts, not
percentages** — the pool is small enough, once eligibility and no-reuse
constraints are applied across 9 incidents, that a percentage would imply
more precision than the sample size supports.

**Held-out set (frozen 2026-10-01).** Note: the request that produced this
freeze asked for it under a new "Section 5.2" — that number is already
`### 5.2 Scoring rubric`, so this is appended here instead, to the section
that already exists for exactly this purpose, rather than creating a
second, colliding 5.2.

- **Seed:** `20261001`. **k = 9** dates per incident (I3b excepted, which
  has exactly one clean date by construction) — the search tried 9 first
  and it was immediately feasible, so 8 and 7 were never needed.
  **Total: 73 dates** (8×9 + 1).
- **Method:** bipartite max-matching (hand-implemented Kuhn/DFS augmenting
  paths, no new dependency), not greedy — each incident is 9 interchangeable
  slots; edges run to every date that survived its own eligibility +
  contamination filter; slot order is fixed, and each slot's candidate
  order follows the single `numpy.random.default_rng(20261001)`-shuffled
  date sequence, so the seed (not iteration order) decides which valid
  dates are picked. Full detail and the contamination-rule quote are in
  the file itself, not duplicated here.
- **File:** `evaluation/heldout/heldout_dates.json`.
  **SHA-256:** `82012176af32e9677f0a08af33243166b972c14adf15cfb11dc539f1e3ccfe54`.
- **No-replacement rule:** held-out dates are never swapped after
  freezing. If a held-out date later turns out to be awkward for any
  reason, that is reported as-is, not fixed by re-drawing — the dev set
  was re-screened repeatedly and the held-out set exists specifically so
  at least one set in this project wasn't.
- **The engine has not been run on these dates.** `evaluation/
  heldout_select.py` calls `investigate()` only on real, uninjected data,
  only to rebuild the screened pool and reproduce the eligibility/
  contamination filters already measured by hand (2026-09-30) — never with
  any incident's injector mechanism applied, and never scored. Both halves
  of M5 (freeze the set; score the engine on it) are listed as separate
  steps in Section 7 for exactly this reason.

### 5.5 Arm-neutral scoring (pre-registered before engine runs on held-out)

Both arms (deterministic engine, agent) are scored by the SAME two rubrics
below, implemented once in `evaluation/arm_neutral.py`, independent of
`evaluation/score.py`'s existing per-incident pass conditions (Section
5.2) — that file is not imported or modified here, by instruction, so the
two scorers can disagree and the disagreement is itself informative rather
than hidden by shared code.

**Vocabulary note, stated once:** the ledger's `kind` field only ever
takes the values `segment` / `mix` / `concentration` (grepped, confirmed —
there is no literal `"within"` anywhere in the data model). A claim's
`kind="within"` (Section 4.1's answer schema) maps onto a ledger entry
with `kind="segment"` — a per-segment row, as distinguished from the
whole-dimension `kind="mix"` row. `kind="mix"` in a claim maps onto
`kind="mix"` in the ledger directly, unchanged.

**Schema amendment, found necessary while implementing Score A, not
anticipated in 4.1:** I3b's pass condition needs `basis`, which 4.1's
7-field claim schema (`metric, dimension, segment, direction, kind,
evidence_ids, strength`) does not carry, and `score_a`'s signature
(`score_a(answer, incident_id)`) has no ledger to resolve it from
separately. **`Claim` gains an eighth field, `basis`**, populated directly
from the ledger entry a claim is built from (the engine always has this
for free; the agent's `test_dimension` tool already returns `basis` per
row, Section 4.1, so this costs the agent arm nothing either).

**Score A — within-dimension localization.** For incidents with a single
named target segment:

| incident | target (metric, dimension, segment) |
|---|---|
| I1 | sessions, channel, Organic Search |
| I2 | conversion_rate, channel, Referral |
| I3 | aov, channel, Direct |
| I3b | aov, channel, Direct |
| I5 | conversion_rate, device, desktop |
| I7 | **both** — sessions/channel/Organic Search AND conversion_rate/channel/Referral, scored separately |

Pass (per target): the arm's TOP-RANKED claim within that exact
(metric, dimension) has `segment` = the target, `direction="down"`,
`kind="within"`, and `strength` in `{strong, plausible}` — except AOV
(I3, I3b), where `strength` must be `material_untested` (AOV's verdict is
structurally never anything else, PLAN.md 3.7). **I3b additionally
requires `basis="ineligible_segment"`** (not `decomposition_only` — the
whole point of I3b is that Direct is BOTH ineligible and untestable on
this date, and ineligible must win, matching `Ledger.record`'s own
precedence). **I4, I6, C1 have no Score A** — none of them have a single
named target segment to localize to.

**Score B — root-cause identification.** Each arm returns `pattern in
{localized, broad, mix, none}` plus ranked claims.

| incident | correct `pattern` | correct cause |
|---|---|---|
| I1, I2, I3, I3b, I5 | `localized` | the Score A target above |
| I7 | `localized` | top-1 correct if it matches **either** target; **separately** checked: both targets appear somewhere in the top-3 |
| I4 | `broad` | metric = `aov` |
| I6 | `mix` | dimension = `device`, metric = `conversion_rate` — quoted directly from `evaluation/ground_truth/I6.json`'s `"forced_metrics": ["conversion_rate"]` (single element, unambiguous; the loader asserts length 1 and fails loudly otherwise) |
| C1 | `none` | — |

Reported as top-1 and top-3 accuracy (counts, not held-out — see below).

**Engine answer rule ("engine + naive selection rule"), built only from
existing outputs, no new thresholds:**
1. A qualifying row = a channel-dimension, `kind="within"` row with
   `strength` in `{strong, plausible}`, OR `strength="material_untested"`
   (any `basis`).
2. If no qualifying row exists for any of the three forced metrics:
   `pattern="none"`, no claims.
3. Otherwise `pattern="localized"`. Primary metric = the revenue factor
   with the largest **absolute log-space contribution** (`investigate()`'s
   own `factors` table, already log-space by construction, Section 2 —
   no new computation) among metrics that have at least one qualifying
   row. Claims = that metric's qualifying channel rows ranked by
   `|contribution|`, then the other qualifying metrics' own TOP row each,
   ranked by their factor contribution, up to 3 claims total.
4. **The engine never outputs `broad` or `mix`.** It therefore fails I4's
   and I6's Score B `pattern` by construction (the two incidents whose
   correct pattern is `broad`/`mix`). **Amended 2026-10-01 — I5's top-1/
   top-3 also fail by construction**, for a different reason: I5's
   `pattern` IS correctly `localized`, but its target is device-dimension
   and `primary` (the rule below) is channel-only by design, so the
   target can never appear among `primary`'s ranked claims even though
   the pattern itself is right. (The request that produced this section
   originally named I5 among the `broad`/`mix` pattern failures, which
   doesn't match this section's own Score B table — I6 was used instead,
   flagged, not silently substituted; this amendment is the corrected,
   complete list of by-construction Score B failures, not a reversal of
   that earlier correction.) None of this is a claim about the engine's
   diagnostic quality — Score A is the quality claim; Score B's
   structural failures are a known property of a rule that only ever
   looks at one dimension (channel) at a time.

**Amendment, 2026-10-01 — Score A fixed on dev, before any held-out
run.** Score A was being scored from `primary` (the engine answer rule's
capped, channel-only output above) instead of from the full evidence the
arm actually has available — conflating "what can localize the target"
with "what the naive rule happens to assert." On the 41 dev dates, this
traced to **6 of 7** disagreements against `evaluation/score.py`'s
existing, independent `SCORERS`: I5's 3 dates (device-dimension target,
channel-only rule — structurally can never appear) and 3 more (I1
2017-05-04, I7 2017-06-28, I7 2017-02-01 — the target metric had a
qualifying row that the 3-claim cap then dropped, because forcing all
three metrics ranks by factor contribution, not by which metric the
incident actually injected into). The 7th (I3b 2017-01-23) is a genuine
rank disagreement, unrelated to this bug, and stays as-is (see below).

**Fixed by splitting the answer into two fields, used for two different
questions, for BOTH arms identically:**
- **`claims`** — every qualifying row in every forced `(metric,
  dimension)`, not just channel, ranked by `|contribution|` **within**
  each `(metric, dimension)` group. **Score A reads `claims`**: filter to
  the target `(metric, dimension)`, take the first (already ranked by the
  arm's own ordering within that group). Pass conditions above are
  otherwise unchanged.
- **`primary`** — the engine answer rule exactly as specified above,
  unchanged: channel-dimension only, one primary metric, capped at 3.
  **Score B still reads `primary`** — Score B is checking what the arm
  actually asserted as ITS answer, which is a different question from
  Score A's "can the evidence localize the target," and is correctly
  unaffected by this fix.

**The baseline rule (`primary`) is deliberately NOT amended after seeing
the dev-date disagreements** — tuning it now, having already seen which
dev dates it gets wrong, would make dev-date Score B numbers meaningless
as anything other than a fitted result. I4/I6's pattern failures and I5's
top-1/top-3 failures (point 4 above) stand as pre-registered, known,
structural properties of the naive rule, not bugs to close before the
held-out run.

**I3b's 0/1 Score A is a genuine rank result, kept as-is** — on
2017-01-23, some other ineligible channel segment's `|contribution|`
exceeds Direct's that day, so it (not Direct) is the top-ranked claim in
the `(aov, channel)` group; this has nothing to do with the `claims`/
`primary` bug above and is not evidence of a remaining implementation
error.

**False positives, both arms, C1 dates:** a claim with `strength` in
`{strong, plausible}` is a false positive by definition (C1 has no
injection). Claims with `strength="material_untested"` (or any other
non-confident value) are counted and reported **separately**, not folded
into the false-positive count — an untested claim isn't a confident wrong
answer, it's the agent/engine correctly declining to assert one.

**Agent nondeterminism (forward-looking — no agent exists yet):** the
agent runs 3 times per date (Section 4.1). Primary numbers are counts over
date × run, not per-date; per-date agreement across the 3 runs is reported
alongside as a separate, secondary number.

**Checker:** Section 4.1's checker (`evaluation/arm_neutral.py`'s
`check_answer`) runs on both arms' answers unchanged. The engine's claims
are built directly from ledger entries 1:1, so they pass trivially by
construction — stated here as the expected result, verified in the
commit that adds the code.

**Headline numbers are held-out only, reported as counts** — same reason
as Section 5.4. The 41-dev-date numbers reported when this scorer is
built are a code-correctness check (does the scorer run, does the checker
pass, do the two independent scorers roughly agree), not a result.

### 5.6 Held-out engine results (M5)

Engine frozen at tag `engine-frozen-m5`. **The first held-out attempt
aborted on a scorer crash (`evaluation/score.py`'s `_dim_segments`,
`KeyError: 'metric'`) before any held-out result was written or viewed** —
fixed in a dedicated commit (`1e5f19f`, "score.py: guard empty
investigate() results") that touches only `evaluation/score.py`, verified
to change no `detection`/`investigation`/`metrics` code and to leave every
dev-date number (SCORERS 34/36; `arm_neutral`'s Score A/B/C1 counts)
byte-identical to the pre-fix run, before the held-out run was reattempted.

**Root cause, documented as an engine limitation, not fixed (engine is
frozen):** `metrics/segments.py`'s `segment_expectation` correctly masks a
segment's expectation to NaN when any of its `weeks` same-weekday lags is
missing — but does so **silently**, with no "insufficient history" signal
that reaches the caller. Across the full 281-day detection range this
affects exactly one date, for `weeks=12` metrics (sessions, aov) only: the
single Wednesday whose 12th lag lands exactly on the excluded 2016-08-03
data-gap date. One held-out date (I1) hit it. A non-silent "insufficient
history" result (rather than a bare empty frame) is noted here as a
**post-M7 engine improvement** — not done now, because the engine is
frozen for this held-out run and must stay that way until M7's two-arm
comparison is complete.

**Score A (held-out):**

| target | pass |
|---|---|
| I1 (sessions, channel, Organic Search) | 6/9 |
| I2 (conversion_rate, channel, Referral) | 2/9 |
| I3 (aov, channel, Direct) | 2/9 |
| I3b (aov, channel, Direct) | 0/1 |
| I5 (conversion_rate, device, desktop) | 8/9 |
| I7 (sessions, channel, Organic Search) | 6/9 |
| I7 (conversion_rate, channel, Referral) | 1/9 |

**Existing `SCORERS` (held-out), next to the dev pre-selection rates
(Section 5.2 — the diagnostic-accuracy-relevant number, since held-out
dates were never screened for passing, Section 5.4):**

| incident | held-out | dev pre-selection |
|---|---|---|
| I1 | 6/9 | 24/58 (41.4%) |
| I2 | 2/9 | 19/105 (18.1%) |
| I3 | 2/9 | 36/100 (36.0%) |
| I3b | 1/1 | — |
| I4 | 9/9 | — (not re-screened) |
| I5 | 4/9 | — (not re-screened) |
| I6 | 9/9 | — (not re-screened) |
| I7 | 0/9 | 6/104 (5.8%) |

**Score B (pattern / top-1 / top-3):**

| incident | pattern | top-1 | top-3 |
|---|---|---|---|
| I1 | 8/9 | 3/9 | 4/9 |
| I2 | 9/9 | 1/9 | 1/9 |
| I3 | 9/9 | 1/9 | 4/9 |
| I3b | 1/1 | 0/1 | 0/1 |
| I4 | 0/9 | 0/9 | 0/9 |
| I5 | 9/9 | 0/9 | 0/9 |
| I6 | 0/9 | 0/9 | 0/9 |
| I7 | 9/9 | 2/9 | 4/9 |
| C1 | 0/9 | 0/9 | 0/9 |

I7 both-targets-in-top-3: 0/9.

**C1:** false_positives = 1, untested_claims = 26 (of 27 total `primary`
claims across the 9 held-out C1 dates).

**Checker:** 1171 claims checked (`claims` + `primary`, both arms-eligible
lists), **0 rejections**.

**`no_results` (the Section 5.1 diagnosis above, counted per incident, no
dates revealed):** I1 = 1, every other incident = 0.

No interpretation beyond the numbers above - M7 is where the held-out
engine result gets compared against the agent and written up.

---

## 6. Repository structure — matches what exists

```
signaltrace/
├── data/
│   ├── raw -> symlink to SSD (BigQuery CSV exports)
│   └── signaltrace.duckdb        (internal disk — exFAT SSD unsafe for DuckDB)
├── ingest/
│   ├── load_ga.py
│   └── characterize.py
├── metrics/
│   ├── dependency_graph.py       (declared, not learned)
│   └── segments.py               (eligibility gating)
├── detection/
│   ├── config.py                 (all empirically-measured settings + flagged judgment calls)
│   ├── expectation.py
│   └── anomaly.py                (component detection + concentration check)
├── investigation/
│   ├── hypotheses.py             (eligibility-gated generation)
│   ├── attribution.py            (additive / ratio-mix / multiplicative)
│   ├── validator.py              (contribution-first, BH, seven verdicts)
│   ├── evidence.py               (append-only ledger)
│   └── orchestrator.py           (end-to-end, no LLM)
├── agent/            [M6 Part 1 — config.py (frozen model pin), smoke_test.py,
│   ├── config.py      [M6 Part 1 — frozen model pin]
│   ├── smoke_test.py  [M6 Part 1 — pending SSD reconnect]
│   ├── tools.py       [M6 Part 1 — ToolSession: get_factor_decomposition,
│   │                   test_dimension, get_ledger_summary; 127/127 tests pass]
│   ├── llm.py         [M6 Part 2 — ChatResult, OllamaClient (lazy), FakeClient]
│   ├── prompt.py      [M6 Part 2 — SYSTEM_PROMPT VERSION="v0"]
│   └── loop.py        [M6 Part 2 — run_agent(session, client, budget=12) → dict]
├── tests/
│   ├── test_agent_tools.py  [M6 Part 1 — 127 tests: a/b equivalence, c scoping,
│   │                          d checker rejection, e invalid inputs, f idempotency,
│   │                          g grep; all on 41 dev dates]
│   └── test_agent_loop.py   [M6 Part 2 — 14 tests: a scripted Score A/B,
│                              b checker rejects, c budget, d malformed repair,
│                              e resume, f heldout guard, g grep; FakeClient only]
├── evaluation/        [built — injector.py, score.py, arm_neutral.py,
│                       run_agent.py (M6 Part 2), ground_truth/*.json]
├── app/                [built — dashboard.py (M4, commit 4b7a950)]
├── notes/
│   ├── baseline.md               (all empirical findings, with numbers)
│   ├── RESUME.md                 (session resume point)
│   └── PLAN.md                   (this file)
├── tests/              [not started]
├── EXPERIMENT.md      [not started]
└── README.md            [not started]
```

No `agent/prompts.py`, no `Dockerfile` yet — both still apply from the
original plan once their phases start.

---

## 7. Milestones — status

| Milestone | Original gate | Status |
|---|---|---|
| M1 | Series correct, variance understood | **DONE.** Identity exact; noise structure measured; usable range locked |
| M2 | Zero alerts on normal periods; Black Friday handled | **DONE, gate renegotiated.** Separability gate instead; Black Friday not classifiable (3.6) |
| M3 | Dev incidents 1–6 correctly diagnosed | **DONE.** Injected benchmark (Section 5) built and scored: 34/36 |
| M4 | Streamlit dashboard | **DONE.** commit 4b7a950 |
| M5 | Held-out set pre-registered and frozen, engine scored on it | Not reached — stop-safe checkpoint |
| M6 | Evidence-constrained agent, developed on the existing 41 benchmark dates only | Not started |
| M7 | Two-arm evaluation on held-out, README, writeup | Not started |
| M8 (optional) | Raw-LLM floor, chat interface, Olist generalization | Not started |

---

## 8. Known risks and failure modes — updated

| Risk | Status |
|---|---|
| Trend inflates naive z-scores | Resolved — no trend exists in this dataset (measured, not assumed) |
| Injected incidents are self-designed ground truth | Unchanged concern; held-out set still the mitigation |
| Multi-cause incidents reduced to one driver | Mitigated by per-dimension ranking + `CONCENTRATED` cross-dimension note |
| Slow drift missed by point detection | Unchanged concern — no incident tests this yet |
| Segment sparsity → spurious anomalies | **Resolved and generalized** — eligibility gating (Section 2) now applies at every validation, not just detection time; ineligible segments are marked (kept for their share) rather than dropped, and never significance-tested |
| Scope creep into infrastructure | Holding — no scope creep so far |
| Agent hallucinates ungrounded causes | Unchanged concern — sharpened by `material_untested`/`decomposition_only`/`ineligible_segment`, which the agent must learn to narrate correctly |
| **NEW: revenue-level detection is unreliable** | Resolved by moving detection to components (3.4–3.5) |
| **NEW: naive statistical tests can be invalid for the metric type** | Found and fixed (AOV/proportion-test mismatch, 3.7) — worth stating explicitly as a lesson, since it is exactly the kind of error an LLM-only "just ask the model to test it" approach would not have caught |
| **NEW: eligibility gate existed but was never wired in** | Found and fixed (fifth defect, notes/baseline.md) — segments 2% ratio-eligible were reporting `strong` verdicts |
| **NEW: eligibility gate itself leaked future data** | Found and fixed — computed over the full 365-day range instead of a trailing window, the same class of leak `expectation.py` already guards against one layer down. Fixed with an 84-day trailing `asof` window (notes/baseline.md) |
| **NEW: significance tests likely anticonservative (overdispersion)** | **IMPROVED for `_poisson_p` (sessions/transactions), NOT fully closed — residual limitation logged, see Section 5.3.** Measured, not assumed: `_poisson_p` was reaching `strong` on 70.4% of material-tested pure-noise pairs (C1), vs 4.2% for `_prop_p`, isolating the defect to the Poisson assumption specifically. A per-segment, leak-free, dispersion-corrected Negative Binomial test (investigation/dispersion.py) brings C1's sessions rate to 7.4% — but that's the WORST of three designs measured on C1 (vs 5.6% and 0.0% for two rejected alternatives; the three are statistically indistinguishable at C1's n=54, per a 90%-CI check). The design was chosen on mechanism grounds and on I1/I7 (selection data, not an independent check), not because it measurably improved C1. A structural gap remains: a segment whose own dispersion is much lower than its dimension pool's, tested before its own history clears 84 observations, can still overcorrect — the threshold change narrows this window, it doesn't remove the mechanism. `_prop_p` (conversion_rate) was not touched — confirmed unimplicated by the same measurement, and confirmed still unaffected after the fix (4.2% before, 4.2% after). |
| **NEW: pooled cross-dimension ranking double-counts** | Found and fixed (3.7) |
| **NEW: seasonality is undetectable with 12 months of data** | Found, addressed by scope cut rather than a fix (3.6) — the strongest "document the limitation" item in the project |
| **NEW: `CONCENTRATED` false-fires on ordinary noise, and a percentile fix cannot work** | **OPEN, not fixed at time of logging (originally found 2026-09-27; measured further and found structural 2026-09-28).** Originally found on I4's uniform AOV cut (fired 5/5 on genuinely non-concentrated dates). Measured broader: the fixed `>=0.40` rule fires on **77.7% (327/421) of ordinary, untouched metric-days** across sessions/conversion_rate/aov — not a rare signal misfiring occasionally, essentially always-on. Tried the same percentile-of-real-null-distribution fix already used for I4 (Section 5.2): **does not work, at any percentile from p75 to p95.** The 4 known whale days fire on **0/4** regardless of percentile, and the (now 15, post-M2b) real composed-incident days fire on at most 2/26 metric-day pairs even at the loosest percentile tried (p75), 0/26 at p90-p95. Root cause is structural, not a threshold value: device and country are near-constant (desktop/US structurally hold ~90-100% of the business on almost any day, whale or not, notes/baseline.md "Structural pattern") so no percentile can discriminate a whale day from an ordinary one on those two legs; channel's own null distribution is separately so heavy-tailed (AOV p95 = 1020% share) that almost nothing clears it except pool outliers. AND-ing three legs where two are trivially-always-satisfied and one is nearly-never-satisfiable produces a rule that cannot discriminate anything real, regardless of calibration. Confirmed inert before any fix: `CONCENTRATED`'s only effect anywhere in the codebase is a `print()` inside `investigate()`'s verbose block - no ledger write, no return-dict field, no downstream consumer (grepped, not assumed). **FIXED 2026-09-28** (`investigation/orchestrator.py`) with the I5 split, not a percentile: device/country get I5's already-validated absolute thresholds (desktop>=90%, US>=70% - structural, not noisy, so a fixed floor is right); channel gets the rank+sign+verdict pattern from I1/I2/I3/I7, specifically `verdict in {strong, plausible}` for the top channel segment - matching the bar those incidents actually used (never `material_but_uncertain`, measured to be a much looser, less faithful choice: including it gives 21.0% pool rate and 8/15 real-incident recall vs {strong,plausible}'s 2.4% pool rate and 3/15 recall; picked the latter to stay consistent with established precedent rather than maximize recall). Verified: pool-wide firing rate 77.7% -> 2.4%; the 4 whale days still don't fire (0/4) - **structurally, not a residual gap**, since AOV's channel verdict can never be `strong`/`plausible` at all (`testable=False` forces `material_untested`, PLAN.md 3.7), so `CONCENTRATED` can now only ever fire for sessions/conversion_rate, never AOV - a deliberate, documented consequence, not an oversight; the (now 15) real composed-incident days fire on 3/15 (all sessions, the Dec 9/13/16 Social+desktop+US cluster) - a real, meaningful, rare signal again, not the previous always-on noise. Full M3 benchmark unaffected (34/36, unchanged), confirming `CONCENTRATED` remains fully inert to scoring - this was a narration-quality fix, not a behavioral one. |
| **NEW: `CONCENTRATED` false-fires on ordinary noise, and a percentile fix cannot work** | Superseded — merged into the row above. |
| **Holiday screening gap (Section 5.1, item 5)** | **FIXED 2026-09-28.** The original exclusion filter only ever covered Thanksgiving/Black-Friday/Cyber-Monday/pre-Christmas-week — never checked against the rest of the US federal calendar, so 8 of the 41 pre-registered dates sat within 3 days of an unscreened holiday (Veterans Day, MLK Day, Presidents Day, Memorial Day, Independence Day). Replaced with `pandas.tseries.holiday.USFederalHolidayCalendar`, same ±3-day buffer as every other exclusion. Pool shrank 136→113 of 201 weekdays (see 5.1 reconciliation note on the 141-vs-136 discrepancy — both numbers were correct for the composed-day count active when each was computed, not a bug). Per-date empirical check (real, uninjected data, not just calendar proximity) found 4 of the 8 flagged dates showed a measurable real effect and 4 did not; only the 4 with a real effect (or an exact holiday collision) were swapped — see 5.1 for the full per-date table and 5.3 for the sharper I7 framing below. **Sharper finding, not just "proximity is a gap":** I7's 2017-07-04 (exact Independence Day collision) shows a contaminated date can inflate an apparent pass by having a real, unrelated background effect coincidentally point in the SAME direction as the injected mechanism — real, uninjected data alone already put Referral at the real top channel mover on both forced metrics (sessions verdict=strong, conversion_rate verdict=plausible, top-level conversion_rate z=-3.65 = the 99th percentile of the ordinary-day distribution) — not merely adding uncorrelated noise that a large enough injection would swamp regardless. A date can pass its pass condition for the wrong reason, not just fail for the wrong reason; a contamination check needs to look for both. All 4 replacement dates re-verified 5/5 pass on the rebuilt 113-date pool; full M3 unchanged at 34/36; C1 false-strong rate 3/78 (3.8%) → 3/75 (4.0%, same 3 false-strongs, smaller denominator — not a regression, see 5.3). |
| **`detection/config.py:137`'s `"bh_family": ("metric",)` is never read** | **Found 2026-09-29 (read-only verification, no code change).** The comment states the intended Benjamini-Hochberg family is per metric, pooled across all three dimensions. Grepped: this key is referenced nowhere else in the codebase — one hit, its own definition. The family the code actually applies is one level finer, **per (metric, dimension)**, set structurally by `orchestrator.py`'s `for dim in DIMENSIONS: ... validate_segments(m, dim, ...)` loop — each call runs its own independent BH correction (`validator.py:277-282`) over only that dimension's segments, never pooled with the other two. **Deliberately not wired in, not an oversight to fix casually**: pooling all three dimensions into one family before correcting would change the family size `n` in every q-value's `p_i * n / rank_i`, which would change verdicts and would need to be re-verified against the full M3 benchmark (currently 34/36) before shipping — exactly the same "a config change needs a re-run, not just an edit" discipline as every other threshold in this file. The comment-vs-code mismatch itself is a separate, code-only fix, tracked for its own commit — not bundled into this docs-only pass. |
| **Small local LLM may be weak at multi-step tool use (Section 4.1)** | **Anticipated, not yet measured — logged 2026-09-29 alongside the Ollama decision.** `qwen3:8b`/`qwen3:14b` are far smaller than a frontier hosted model and have not been benchmarked on agentic tool-calling loops at this project's task shape (3 fixed tools, exhaustive coverage only 9 calls, but the model must still choose which calls to make, in what order, and read structured JSON results correctly across turns). If M6's smoke test shows the model cannot reliably drive the loop, that is a finding about this model choice, not about evidence-constrained agents generally. **The headline claim this project can support is scoped accordingly: "evidence constraint enables a small local model to [do X]" — not a general claim about LLM agent capability.** M8's optional raw-LLM-with-SQL floor and any later hosted-model comparison are the way to separate "the constraint helped" from "the model was just too small," if pursued. |

---

## 9. Explicitly out of scope for v1 (unchanged)

Kafka, PostgreSQL, Airflow, Kubernetes, React frontend, Slack integration,
multi-tenancy, learned dependency graphs, automated remediation, more than
one business domain (GA360 only — Olist as a possible post-M5 cross-
dataset generalization test remains optional, not committed).

---

## 10. Portfolio positioning — updated

> **SignalTrace — Autonomous KPI Root-Cause Investigation**
> Built an evidence-grounded analytical engine on real Google Analytics
> session data (GA360) that detects statistically significant deviations
> in sessions, conversion rate, and AOV; localizes drivers within a
> declared metric dependency graph across channel, device, and country;
> and separates within-segment effects from traffic-mix effects using a
> within/mix decomposition — correctly attributing a Simpson's-paradox
> case where blended conversion moves with no per-segment cause. Every
> conclusion is contribution-ranked, Benjamini-Hochberg corrected within
> its hypothesis family, and traceable to an append-only evidence ledger
> that retains negative results and distinguishes tested claims from
> decomposition-only ones. Four real defects — an invalid statistical
> test, a cross-dimension double-count, a pandas API break, and a
> sign-display bug — were found by running the engine against real
> detected anomalies rather than synthetic cases. A fifth — the
> eligibility gate existed but was never wired into the orchestrator,
> so untestable segments could report significance-based verdicts — was
> found in a code/notes audit and confirmed by running on real data.

The mix-shift/Simpson's-paradox handling and the honest scope cut on
seasonality are the two strongest, most specific claims this project can
make that a generic "AI anomaly agent" cannot.

Complementarity with the existing portfolio is unchanged from the
original plan.
