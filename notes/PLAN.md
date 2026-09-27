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

---

## 4. Phase 2 — Agent layer (unchanged from original plan)

Not started. Tool list, LangGraph orchestration, evidence-ledger
constraint, and the "SignalTrace Engine vs SignalTrace Agent" naming all
carry over unchanged. One addition given 3.7: the agent's tools should
expose the `basis` field (identity / statistical_test / decomposition_only
/ threshold) so its narration can distinguish a tested claim from a
decomposition-only one rather than reporting both with equal confidence.

---

## 5. Phase 3 — Evaluation — INCIDENT SET NEEDS REVISION

Not started. Pre-registration discipline, three-arm structure, and
metrics (precision/recall/F1, top-1/top-3, attribution MAE, unsupported-
claim rate) all carry over unchanged in principle.

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

Held-out set, in-distribution/held-out split, and the headline three-arm
experiment are otherwise unchanged from the original plan.

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
| I7 | Compound: Organic Search sessions −40% + Referral conversion −30%, same day | I1's mechanism (factor 0.6) + I2's mechanism (factor 0.7), independently, same date | 2017-02-02, 2017-02-08, 2017-05-23, 2017-06-28, 2017-07-04 (re-screened, see below) |
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
  as strict gates, too few to fill 5 slots. Re-screened dates: **2017-02-02,
  2017-02-08, 2017-05-23, 2017-06-28, 2017-07-04**.

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
| I7 | sessions x channel: Organic Search is the largest-magnitude, correctly-signed, verdict=strong segment (same I1 fix); conversion_rate x channel: Referral is the largest-magnitude, correctly-signed segment, **verdict>=plausible** (same I2 power-limit fix — I7's own conversion cut is -30%, even smaller than I2's -40%, so this is the binding constraint: only 9/104 non-colliding candidate dates clear it alone, vs 77/104 for the sessions side); Organic Search's own conversion_rate within-share, and the revenue-factor table's aov/sessions/conversion_rate shares, are **reported, not gated** (same denominator fragility as I1's revenue-factor share; strict-gating all three left only 3/104 viable dates, too few to fill 5 slots) |
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
tests, while `_prop_p` (conversion_rate) reaches it on only 4.2% (1/24). This
sharpens the concern above from general to specific: the anticonservative
behavior is overwhelmingly a property of the pure-Poisson assumption in
`_poisson_p`, not of `_prop_p`'s proportion model. A dispersion fix, if
built, should be scoped and prioritized accordingly - `_poisson_p` first.

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
├── agent/            [not started — Phase 2]
├── evaluation/        [not started — Phase 3, incident set needs rewrite]
├── app/                [not started — M4]
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
| M3 | Dev incidents 1–6 correctly diagnosed | **Functionally done, gate NOT YET TESTED.** Pipeline runs correctly on real anomalies; the injected benchmark incidents from Section 5 don't exist yet, so the stated exit criterion is unverified |
| M4 | Streamlit dashboard | Not started |
| M5 | Phase 1 complete, held-out incidents evaluated | Not reached — stop-safe checkpoint |
| M6 | LangGraph agent reproduces M3 conclusions | Not started |
| M7 | Chat interface | Not started |
| M8 | Three-arm evaluation, README, writeup | Not started |

**Before M4, consider closing the M3 gap:** build a minimal incident
injector and run the six development incidents (revised per Section 5)
against the orchestrator. This is cheap relative to the value — it is the
first real test of whether the engine's diagnoses are *correct*, not
merely well-formatted, and M4's dashboard will be more meaningful once
there is a known-correct incident to show in it.

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
| **NEW: significance tests likely anticonservative (overdispersion)** | **OPEN, not fixed** — see Section 5. q-values as small as 1e-100+ observed; `strong` is likely over-awarded |
| **NEW: pooled cross-dimension ranking double-counts** | Found and fixed (3.7) |
| **NEW: seasonality is undetectable with 12 months of data** | Found, addressed by scope cut rather than a fix (3.6) — the strongest "document the limitation" item in the project |
| **NEW: `CONCENTRATED` false-fires on ordinary AOV noise** | **OPEN, not fixed** (logged 2026-09-27, found while building the I4 benchmark rubric, Section 5) — `CONCENTRATED`'s check (orchestrator.py) is each dimension's top segment reaching raw `contribution >= 0.40`, the same fragile shared-denominator statistic identified in I1/I2/I3 (Section 5's "General principle"). Measured: on I4's 5 dates (a genuinely uniform, non-concentrated AOV cut), `CONCENTRATED` fired 5/5. The real (no-injection) channel-dimension AOV max-\|share\| distribution across 140 dates is itself extreme (median 0.93, p90 5.07, p95 10.2, max 416) — far past the 0.40 threshold on most ordinary days regardless of any injection. This affects every real investigation that fires on AOV, not just this benchmark; a fix (e.g. the same measured-percentile approach used for I4's own condition, Section 5.2) is logged here but not implemented — out of scope for finishing the M3 evaluation rubric. |

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
