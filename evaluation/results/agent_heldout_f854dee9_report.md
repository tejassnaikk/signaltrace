# M7 held-out report — frozen agent (v1, think off, `f854dee9`) vs engine (`engine-frozen-m5`)

Source files: `agent_heldout_f854dee9.jsonl` (219 runs = 73 dates × 3, all `status: ok`),
`heldout_engine.json` (M5, 73 dates; I3b has 1 date, every other incident 9).
Dev comparators: `agent_dev_f854dee9.jsonl` (41 runs, 1 run per date) and engine dev
(`arm_neutral.py`, 41 dates; numbers as in `agent_dev_f854dee9_report.md`).

The agent and engine read the same ledger. The agent only chooses which
`test_dimension` calls to make and which rows to cite.

## Step 0. Integrity check (PASSED — no harness mismatch)

The held-out file stores each run's submitted claims and rejection *counts*, but not the rendered
tool outputs. To answer "was the cited id shown to the model?", every one of the 219 held-out runs
(and the 41 dev runs) was replayed through the **deterministic tool layer only** (`ToolSession` with
the v1 renderers, using each run's recorded `test_dimension` calls, then the frozen
`_check_claims` on the recorded `submit_answer` args). No model call, no agent re-run, no change to
`agent/` or the engine. Replay fidelity: the replayed rejection counts equal the stored counts in
**219/219 held-out runs**, and total rejection counts are equal in all 260 replays.

### 0a. `unknown_entry`: every claim classified

Categories: **A** id not in the harness ledger at all (model typo/hallucination); **B** id is in the
ledger and was shown to the model, on a row whose `claim_as` was `-` (not claimable); **C** id is in the
ledger but was never shown; **D** id was shown to the model as a *claimable* row but the checker did
not know it (**harness mismatch**).

| scope | runs | unknown_entry claims | per run | runs with ≥1 | A | B | C | **D** |
|---|---|---|---|---|---|---|---|---|
| dev v1 (replayed with frozen checker) | 41 | 46 | 1.12 | 6 | 0 | 46 | 0 | **0** |
| held-out session 1 (first 194 runs) | 194 | 246 | 1.27 | 30 | 0 | 246 | 0 | **0** |
| held-out session 2 (last 25 runs, all C1) | 25 | 54 | 2.16 | 9 | 0 | 54 | 0 | **0** |
| held-out total | 219 | 300 | 1.37 | 39 | 0 | 300 | 0 | **0** |

By incident (held-out, all in category B): I2 105, I3 87, I6 42, I7 12, C1 54; I1, I3b, I4, I5 = 0.
Session 1 holds 246 (I2 105, I3 87, I6 42, I7 12); session 2 holds 54 (C1 only). Session 1 had 2 C1 runs
and they had 0. On C1 alone: dev 8/5 runs = 1.6 per run, held-out session 2 54/25 = 2.16 per run.

Cited rows were `material_but_uncertain` (267) or `unsupported` (33). The model's strength word on
them was `untested` (279) or `plausible` (21). All 300 rows were shown with `claim_as: "-"`.

**The premise "dev v1 had 0 unknown_entry" is a labelling difference, not a behaviour difference.**
The dev v1 run (commit `2ff263c`) predates the loop's "context rows are invisible" check (added in
`20f8c06`, before the freeze). In that older checker, a claim on a non-claimable row with a
claimable-looking strength word was recorded as `verdict_mismatch` (46 in dev). The frozen checker
labels the same claims `no ledger entry` (→ `unknown_entry`). Replaying dev with the frozen checker
gives 46 `unknown_entry` and 0 `verdict_mismatch`, so the dev `verdict_mismatch` count (46) maps to
the same 46 claims. The label "no ledger entry" is misleading for this case (the entry exists and was
shown). It is a checker wording issue, not a ledger/render inconsistency. In both arms the claim is
rejected either way, so no accepted answer changes.

It is also not confined to the last 25 runs: session 1 contributes 246 of the 300. Session 2's higher
per-run rate comes from it being C1-only (C1 dates have many `material_but_uncertain` rows, and dev C1
was already 1.6 per run).

### 0b. `direction_mismatch`

6 claims, all C1: 2 dates × 3 runs (deterministic), 1 claim each.

| row | shown direction | actual / expected | checker direction | model claimed |
|---|---|---|---|---|
| E032 (date 2017-04-17) | up | 0.0111 / 0.0 | up | down |
| E035 (date 2017-05-16) | down | 0.0279 / 0.0394 | down | up |

The direction shown to the model and the checker's definition both come from `_row_direction`, and
they agree on all 1,638 rejected-claim rows checked (0 disagreements). Both mismatches are model sign
errors on near-zero deltas (the row says one thing and the model claimed the opposite).

**Verdict: 0 harness-mismatch claims. Held-out run is valid under PLAN 4.2.**

## Step 1. Final comparison (agent = frozen v1, 3 runs/date; engine = M5)

### Score A (primary, pre-registered): agent as passes over date × run

| incident / target | agent runs | agent dates 3/3 | ≥2/3 | 0/3 | engine dates |
|---|---|---|---|---|---|
| I1 sessions/channel/Organic Search | 18/27 | 6 | 6 | 3 | 6/9 |
| I2 conversion_rate/channel/Referral | 6/27 | 2 | 2 | 7 | 2/9 |
| I3 aov/channel/Direct | 6/27 | 2 | 2 | 7 | 2/9 |
| I3b aov/channel/Direct | 0/3 | 0 | 0 | 1 | 0/1 |
| I5 conversion_rate/device/desktop | 21/27 | 7 | 7 | 2 | 8/9 |
| I7 sessions/channel/Organic Search | 18/27 | 6 | 6 | 3 | 6/9 |
| I7 conversion_rate/channel/Referral | 3/27 | 1 | 1 | 8 | 1/9 |

Every date's 3 runs agree (see determinism), so "dates 3/3" equals "dates ≥2/3" equals "dates ≥1/3".
Per target the agent matches the engine on 54 of 55 date-targets; the single difference is one I5
date the engine passes and the agent fails.

### Score B: pattern / top-1 / top-3 (agent runs of 27; I3b of 3; engine dates of 9; I3b of 1)

| incident | agent pattern | agent top-1 | agent top-3 | engine pattern | engine top-1 | engine top-3 |
|---|---|---|---|---|---|---|
| I1 | 27/27 | 18/27 | 18/27 | 8/9 | 3/9 | 4/9 |
| I2 | 27/27 | 3/27 | 6/27 | 9/9 | 1/9 | 1/9 |
| I3 | 27/27 | 6/27 | 15/27 | 9/9 | 1/9 | 4/9 |
| I3b | 3/3 | 0/3 | 0/3 | 1/1 | 0/1 | 0/1 |
| I4 | 0/27 | n/a | n/a | 0/9 | n/a | n/a |
| I5 | 27/27 | 6/27 | 18/27 | 9/9 | 0/9 | 0/9 |
| I6 | 0/27 | n/a | n/a | 0/9 | n/a | n/a |
| I7 | 27/27 | 18/27 | 21/27 | 9/9 | 2/9 | 4/9 |
| C1 | 0/27 | n/a | n/a | 0/9 | n/a | n/a |

I7 both-targets-in-top-3: agent 0/27 (0/9 dates), engine 0/9.

Dates passing (3/3, since runs agree): agent top-1 17 dates, engine top-1 7 dates. On the 46 dates that
have a top-1 target, both get top-1 on 5, agent only on 12, engine only on 2, neither on 27.

The agent outputs `pattern=localized` on all 219 runs. That is why I4 (broad), I6 (mix) and C1 (none)
are 0 on pattern. The engine is also 0 on those three.

### Total top-1 (incidents with a named cause: I1, I2, I3, I3b, I5, I7)

| arm | top-1 | top-3 |
|---|---|---|
| agent (runs) | 51/138 = 37.0% | 78/138 = 56.5% |
| engine (dates) | 7/46 = 15.2% | 13/46 = 28.3% |

### C1 (negative control, 9 dates)

| | agent (27 runs) | engine (9 dates) |
|---|---|---|
| tested false positives (strong/plausible in `primary`) | 24 (4 dates × 3 runs: 3+2+2+1 per run) | 1 |
| untested claims in `primary` | 24 | 26 |
| pattern = none | 0/27 | 0/9 |

Of the 9 C1 dates, the agent makes ≥1 confident claim on 4, untested-only claims on 3, and no claims
on 2.

### Rejected claims, by reason

| incident | invalid_strength | unknown_entry | direction_mismatch | verdict_mismatch | total |
|---|---|---|---|---|---|
| I1 | 42 | 0 | 0 | 0 | 42 |
| I2 | 186 | 105 | 0 | 0 | 291 |
| I3 | 135 | 87 | 0 | 0 | 222 |
| I3b | 0 | 0 | 0 | 0 | 0 |
| I4 | 165 | 0 | 0 | 0 | 165 |
| I5 | 126 | 0 | 0 | 0 | 126 |
| I6 | 201 | 42 | 0 | 0 | 243 |
| I7 | 150 | 12 | 0 | 0 | 162 |
| C1 | 81 | 54 | 6 | 0 | 141 |
| **total** | **1086** | **300** | **6** | **0** | **1392** |

174 of 219 runs had ≥1 rejected claim. 921 claims were accepted; 24 runs (8 dates × 3: I2 2, I3 2, I4 2, C1 2) ended with zero
accepted claims; all `status: ok`. `invalid_strength` is 4.96 per run
(dev v1: 200/41 = 4.88). The engine's checker rejected 0 of 1,171 claims.

### Determinism

73/73 dates give identical answers across the 3 runs (same pattern, same claims with entry_id,
direction and strength, and same `primary`). No date disagrees.

### Efficiency (`test_dimension` calls per run)

| calls | runs |
|---|---|
| 9 | 210 |
| 12 | 3 (I2, one date) |
| 10 | 3 (I6, one date) |
| 6 | 3 (I5, one date) |

Dev v1: 40 runs at 9, 1 run at 8. The typical run is 11 tool calls (1 factor decomposition, 9
`test_dimension`, 1 ledger summary) plus submit. Mean wall time 105.8 s per run (session 1: 103.3 s,
session 2: 124.9 s; C1-only).

## Dev → held-out carry-over

Dev agent = 1 run/date on 5 dates (I3b: 1). Held-out agent = 3 runs on 9 dates. Dev dates were
re-screened and swapped during benchmark construction (PLAN 5.1, 5.6 "dev pre-selection"); held-out dates
were never screened for passing (PLAN 5.4, 5.6). A fall for *both* arms is
therefore expected, and the agent-vs-engine gap is the cleaner read.

### Score A (pass rate)

| target | agent dev | agent held-out | engine dev | engine held-out |
|---|---|---|---|---|
| I1 sessions/Organic Search | 5/5 (100%) | 18/27 (67%) | 5/5 (100%) | 6/9 (67%) |
| I2 cr/Referral | 4/5 (80%) | 6/27 (22%) | 5/5 (100%) | 2/9 (22%) |
| I3 aov/Direct | 4/5 (80%) | 6/27 (22%) | 5/5 (100%) | 2/9 (22%) |
| I3b aov/Direct | 0/1 | 0/3 | 0/1 | 0/1 |
| I5 cr/desktop | 4/5 (80%) | 21/27 (78%) | 4/5 (80%) | 8/9 (89%) |
| I7 sessions/Organic Search | 5/5 (100%) | 18/27 (67%) | 5/5 (100%) | 6/9 (67%) |
| I7 cr/Referral | 5/5 (100%) | 3/27 (11%) | 5/5 (100%) | 1/9 (11%) |

### Score B

| metric | agent dev | agent held-out | engine dev | engine held-out |
|---|---|---|---|---|
| total top-1 | 16/26 (61.5%) | 51/138 (37.0%) | 15/26 (57.7%) | 7/46 (15.2%) |
| total top-3 | 20/26 (76.9%) | 78/138 (56.5%) | 19/26 (73.1%) | 13/46 (28.3%) |
| I7 both-in-top-3 | 5/5 | 0/27 | 3/5 | 0/9 |
| pattern correct (I1–I3b, I5, I7) | 26/26 | 138/138 | 26/26 | 45/46 (engine 1 `no_results` date) |

| C1 | agent dev (5 runs) | agent held-out (27 runs) | engine dev (5) | engine held-out (9) |
|---|---|---|---|---|
| tested false positives | 4 | 24 | 1 | 1 |
| untested claims | 3 | 24 | 13 | 26 |

### Reading the numbers (descriptive)

- Score A, the pre-registered primary, is essentially the same for both arms: 54/55 date-targets
  agree, and the pass rates fall by the same amount from dev to held-out. The agent adds no Score A
  benefit over the engine on held-out.
- Score B top-1 is where the arms differ: the agent keeps 17 top-1 dates to the engine's 7. Agent-only top-1
  dates: I1 3, I2 1, I3 1, I5 2, I7 5 (12 total). Engine-only: I2 1, I7 1 (2 total). On dev the two arms were 16/26 vs 15/26, so the dev gap (+1) understated the held-out gap.
- The agent's confident claims on C1 null dates are worse than the engine's (24 vs 1 false positives;
  4 of 9 dates vs 1 of 9). It never outputs `none`, `broad` or `mix`.
- The agent's I7 both-in-top-3 drops from 5/5 on dev to 0/27 on held-out, driven by the
  conversion_rate/Referral target (3/27).

## Reporting unit and paired test (added 2026-10-22)

All 73 held-out dates give identical answers on all 3 runs, so the 3 runs add no information and the
**date is the effective sample**: agent top-1 **17/46 dates** vs engine **7/46 dates** (named-cause
incidents I1, I2, I3, I3b, I5, I7). Per-date pairs: both 5, agent only 12, engine only 2, neither 27.
Paired exact sign test (McNemar) on the 14 discordant dates: two-sided **p = 0.0129**
(2 × P(X ≤ 2 | n = 14, p = 0.5) = 2 × 106/16384). The run counts elsewhere in this report (51/138 etc.)
are 3 × the date counts and should not be read as independent trials.

The engine's top-1 comes from the **pre-registered naive selection rule** (PLAN 5.5,
`arm_neutral.engine_answer`), not from the engine's own diagnosis; the engine's own `primary`
output is not what is scored here. The comparison is agent vs that rule on the same ledger.

## Think finding re-check (dev, `agent_dev_cda463a5`, v1 think=True)

PLAN 4.9.6 stated that thinking "cited nonexistent evidence ids". The same replay classifies all 8
`unknown_entry` claims in that file: **A (absent from ledger) 0, C (never shown) 0, D (harness
mismatch) 0, B (shown on a non-claimable row, `claim_as: "-"`) 8** — all `material_but_uncertain`
rows (I3b 1, I6 2, I7 5). Thinking did not invent ids; it cited visible rows the harness marks
non-claimable, the same behaviour as think=False (46 such claims, relabelled
`unknown_entry` by the frozen checker). The think-on/off selection (top-1 16 vs 10) is unaffected.

## Reproduction

```
python -I evaluation/integrity/replay.py evaluation/results/agent_heldout_f854dee9.jsonl --split-at 194
python -I evaluation/integrity/replay.py evaluation/results/agent_dev_f854dee9.jsonl evaluation/results/agent_dev_cda463a5.jsonl
python -I evaluation/integrity/analyze.py
```
