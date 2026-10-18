# Agent run analysis — agent_dev_23ad1ff5.jsonl

Total records: 123  |  Prompt version: v0  |  Config hash: 23ad1ff5

## a. Status counts

**Overall:** failed_no_tool_call: 21  ok: 102

| incident | failed_no_tool_call | ok |
|---|---|---|
| I1 | 9 | 6 |
| I2 | 3 | 12 |
| I3 | 3 | 12 |
| I3b | 0 | 3 |
| I4 | 0 | 15 |
| I5 | 0 | 15 |
| I6 | 3 | 12 |
| I7 | 0 | 15 |
| C1 | 3 | 12 |

## b. Score A (within-dimension localization)

Score A counts `pass/total` for **all runs** (failed_* runs count as failures, PLAN 4.1/5.5). Per-date: a date passes if ≥1 run passes; with deterministic runs this equals run counts / 3. Engine dev numbers are from evaluation/arm_neutral.py on the same 41 dev dates.

| incident | target | agent runs | agent dates | engine dev |
|---|---|---|---|---|
| I1 | sessions/channel/Organic Search | 6/15 | 2/5 | 5/5 |
| I2 | conversion_rate/channel/Referral | 12/15 | 4/5 | 5/5 |
| I3 | aov/channel/Direct | 0/15 | 0/5 | 5/5 |
| I3b | aov/channel/Direct | 0/3 | 0/1 | 0/1 |
| I4 | — (no Score A) | — | — | — |
| I5 | conversion_rate/device/desktop | 12/15 | 4/5 | 4/5 |
| I6 | — (no Score A) | — | — | — |
| I7 | conversion_rate/channel/Referral | 15/15 | 5/5 | 5/5 |
| I7 | sessions/channel/Organic Search | 15/15 | 5/5 | 5/5 |
| C1 | — (no Score A) | — | — | — |

## c. Score B (root-cause pattern / top-1 / top-3)

Engine dev = arm_neutral.py naive rule. I4/I5/I6 fail engine Score B top-1/top-3 by construction (see PLAN.md 5.5). Flag I4, I5, I6, C1 below.

| incident | metric | agent pattern | agent top-1 | agent top-3 | engine pattern | engine top-1 | engine top-3 | note |
|---|---|---|---|---|---|---|---|---|
| I1 | sessions | 6/15 | 6/15 | 6/15 | 5/5 | 3/5 | 4/5 |  |
| I2 | conversion_rate | 12/15 | 9/15 | 12/15 | 5/5 | 5/5 | 5/5 |  |
| I3 | aov | 12/15 | 0/15 | 0/15 | 5/5 | 3/5 | 5/5 |  |
| I3b | aov | 3/3 | 0/3 | 0/3 | 1/1 | 0/1 | 0/1 |  |
| I4 | aov (broad) | 0/15 | 0/15 | 0/15 | 0/5 | 0/5 | 0/5 | broad by construction; engine never outputs broad → 0 engine top-1/top-3 |
| I5 | conversion_rate | 15/15 | 6/15 | 9/15 | 5/5 | 0/5 | 0/5 | device-dim target; engine channel-only rule → 0 engine top-1/top-3 |
| I6 | conversion_rate (mix) | 0/15 | 0/15 | 0/15 | 0/5 | 0/5 | 0/5 | mix by construction; engine never outputs mix → 0 engine top-1/top-3 |
| I7 | sessions+cr | 15/15 | 15/15 | 15/15 | 5/5 | 4/5 | 5/5 |  |
| C1 | none | 0/15 | 0/15 | 0/15 | 0/5 | 0/5 | 0/5 | negative control; correct pattern=none |

I7 both-targets-in-top-3: agent 15/15  |  engine 3/5

## d. C1: tested-claim false positives and untested claims

| arm | false positives (strong/plausible) | untested claims |
|---|---|---|
| agent (15 runs, 5 dates) | 6 | 0 |
| engine (5 dev dates) | 1 | 13 |

## e. Rejected claims

**Overall:** 729 rejected claims across all runs.

**By reason (all runs):**

| reason | count |
|---|---|
| invalid_strength | 729 |

**Per incident:**

| incident | invalid_strength | total |
|---|---|---|
| I1 | 33 | 33 |
| I2 | 69 | 69 |
| I3 | 120 | 120 |
| I3b | 6 | 6 |
| I4 | 120 | 120 |
| I5 | 48 | 48 |
| I6 | 102 | 102 |
| I7 | 168 | 168 |
| C1 | 63 | 63 |

**Top submitted-strength values (from llm_response trace):**

| submitted strength | count | valid? | correct word |
|---|---|---|---|
| material_but_uncertain | 465 | ✗ | not claimable |
| material_untested | 216 | ✗ | untested (not 'material_untested') |
| strong | 198 | ✓ | strong |
| plausible | 33 | ✓ | plausible |
| unsupported | 33 | ✗ | not claimable |
| untested | 15 | ✓ | untested ✓ |
| arithmetic | 15 | ✗ | exact (not 'arithmetic') |

**Top 5 (row verdict, claimed strength) mismatches:**

All 729 rejected claims are `invalid_strength`. The model copies ledger verdict strings directly instead of using the agent-schema strength words. Inferred (row_verdict, claimed_strength) pairs by submitted value:

| rank | row verdict (inferred) | claimed strength | count | fix |
|---|---|---|---|---|
| 1 | material_but_uncertain | material_but_uncertain | 465 | row not claimable — omit claim |
| 2 | material_untested | material_untested | 216 | use strength='untested' |
| 3 | unsupported | unsupported | 33 | row not claimable — omit claim |
| 4 | arithmetic | arithmetic | 15 | use strength='exact' |

## f. Tool calls per run

**test_dimension calls distribution (all runs):**

| td calls | run count |
|---|---|
| 9 | 123 |

Total test_dimension calls: 1107

**Total tool calls (all tools) distribution:**

| total tc | run count |
|---|---|
| 11 | 123 |

Total tool calls across all runs: 1353

## g. Determinism

Among 41 (incident, date) groups:
- **All-3-ok, identical:** 34/34 (when all 3 runs succeed, they agree 100% of the time)
- **Consistently failing** (all 3 fail same status): 7
- **Mixed** (some ok, some failed): 0

No dates with all 3 ok but differing answers — perfectly deterministic when successful.

**Consistently-failing dates** (all 3 runs fail the same way):

| incident | date | status |
|---|---|---|
| C1 | 2016-11-10 | failed_no_tool_call |
| I1 | 2017-05-04 | failed_no_tool_call |
| I1 | 2017-06-13 | failed_no_tool_call |
| I1 | 2017-07-05 | failed_no_tool_call |
| I2 | 2016-10-25 | failed_no_tool_call |
| I3 | 2017-05-01 | failed_no_tool_call |
| I6 | 2017-01-11 | failed_no_tool_call |

## h. Failure analysis

### I1

- Score B top-1: agent 6/15 (40%) vs engine 3/5 (60%)
- Score B pattern: agent 6/15 (40%) vs engine 5/5 (100%)
- Score A sessions/channel/Organic Search: agent 6/15 (40%) vs engine 5/5 (100%)

**Failure reasons:**

- `failed_no_tool_call`: 9 run(s)

**Trace excerpts:**

```
excerpt 1: loop ended at nudge (no submit_answer)
```

### I2

- Score B top-1: agent 9/15 (60%) vs engine 5/5 (100%)
- Score B pattern: agent 12/15 (80%) vs engine 5/5 (100%)
- Score A conversion_rate/channel/Referral: agent 12/15 (80%) vs engine 5/5 (100%)

**Failure reasons:**

- `failed_no_tool_call`: 3 run(s)
- `ok_but_wrong_answer`: 3 run(s)

**Trace excerpts:**

```
excerpt 1: loop ended at nudge (no submit_answer)
```

```
excerpt 2: pattern=localized; claims=[Organic Search sessions strong, Referral conversion_rate strong]; submit_answer: pattern=localized n_claims=4 n_accepted=4 n_rejected=0
```

### I3

- Score B top-1: agent 0/15 (0%) vs engine 3/5 (60%)
- Score B pattern: agent 12/15 (80%) vs engine 5/5 (100%)
- Score A aov/channel/Direct: agent 0/15 (0%) vs engine 5/5 (100%)

**Failure reasons:**

- `ok_but_wrong_answer`: 12 run(s)
- `failed_no_tool_call`: 3 run(s)

**Trace excerpts:**

```
excerpt 1: loop ended at nudge (no submit_answer)
```

```
excerpt 2: pattern=localized; claims=[(no claims)]; submit_answer: pattern=localized n_claims=10 n_accepted=0 n_rejected=10
```


## i. Candidate v1 prompt changes

_Not implemented — see failure analysis above for motivating patterns._

