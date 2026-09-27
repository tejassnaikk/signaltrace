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
anomalies but not for conversion or AOV. The hypothesis generator gates
on this before emitting a test; untestable hypotheses are never generated.

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
moved), segment hypotheses (gated by the eligibility table in Section 2 —
untestable hypotheses are never generated), and a `mix` hypothesis for
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

Held-out set, in-distribution/held-out split, and the headline three-arm
experiment are otherwise unchanged from the original plan.

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
| Segment sparsity → spurious anomalies | **Resolved and generalized** — eligibility gating (Section 2) now applies at hypothesis-generation time, not just detection time |
| Scope creep into infrastructure | Holding — no scope creep so far |
| Agent hallucinates ungrounded causes | Unchanged concern — sharpened by `material_untested`/`decomposition_only`, which the agent must learn to narrate correctly |
| **NEW: revenue-level detection is unreliable** | Resolved by moving detection to components (3.4–3.5) |
| **NEW: naive statistical tests can be invalid for the metric type** | Found and fixed (AOV/proportion-test mismatch, 3.7) — worth stating explicitly as a lesson, since it is exactly the kind of error an LLM-only "just ask the model to test it" approach would not have caught |
| **NEW: pooled cross-dimension ranking double-counts** | Found and fixed (3.7) |
| **NEW: seasonality is undetectable with 12 months of data** | Found, addressed by scope cut rather than a fix (3.6) — the strongest "document the limitation" item in the project |

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
> sign-display bug — were found and fixed by running the engine against
> real detected anomalies rather than synthetic cases.

The mix-shift/Simpson's-paradox handling and the honest scope cut on
seasonality are the two strongest, most specific claims this project can
make that a generic "AI anomaly agent" cannot.

Complementarity with the existing portfolio is unchanged from the
original plan.
