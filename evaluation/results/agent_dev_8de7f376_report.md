# Agent run analysis — agent_dev_8de7f376.jsonl

Total records: 41  |  Prompt version: v2  |  Config hash: 8de7f376

## a. Status counts

**Overall:** ok: 41

| incident | ok |
|---|---|
| I1 | 5 |
| I2 | 5 |
| I3 | 5 |
| I3b | 1 |
| I4 | 5 |
| I5 | 5 |
| I6 | 5 |
| I7 | 5 |
| C1 | 5 |

## b. Score A (within-dimension localization)

Score A counts `pass/total` for **all runs** (failed_* runs count as failures, PLAN 4.1/5.5). Per-date: a date passes if ≥1 run passes; with deterministic runs this equals run counts / 3. Engine dev numbers are from evaluation/arm_neutral.py on the same 41 dev dates.

| incident | target | agent runs | agent dates | engine dev |
|---|---|---|---|---|
| I1 | sessions/channel/Organic Search | 2/5 | 2/5 | 5/5 |
| I2 | conversion_rate/channel/Referral | 3/5 | 3/5 | 5/5 |
| I3 | aov/channel/Direct | 0/5 | 0/5 | 5/5 |
| I3b | aov/channel/Direct | 0/1 | 0/1 | 0/1 |
| I4 | — (no Score A) | — | — | — |
| I5 | conversion_rate/device/desktop | 3/5 | 3/5 | 4/5 |
| I6 | — (no Score A) | — | — | — |
| I7 | conversion_rate/channel/Referral | 2/5 | 2/5 | 5/5 |
| I7 | sessions/channel/Organic Search | 3/5 | 3/5 | 5/5 |
| C1 | — (no Score A) | — | — | — |

## c. Score B (root-cause pattern / top-1 / top-3)

Engine dev = arm_neutral.py naive rule. I4/I5/I6 fail engine Score B top-1/top-3 by construction (see PLAN.md 5.5). Flag I4, I5, I6, C1 below.

| incident | metric | agent pattern | agent top-1 | agent top-3 | engine pattern | engine top-1 | engine top-3 | note |
|---|---|---|---|---|---|---|---|---|
| I1 | sessions | 1/5 | 1/5 | 1/5 | 5/5 | 3/5 | 4/5 |  |
| I2 | conversion_rate | 0/5 | 0/5 | 0/5 | 5/5 | 5/5 | 5/5 |  |
| I3 | aov | 0/5 | 0/5 | 0/5 | 5/5 | 3/5 | 5/5 |  |
| I3b | aov | 0/1 | 0/1 | 0/1 | 1/1 | 0/1 | 0/1 |  |
| I4 | aov (broad) | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 | broad by construction; engine never outputs broad → 0 engine top-1/top-3 |
| I5 | conversion_rate | 0/5 | 0/5 | 0/5 | 5/5 | 0/5 | 0/5 | device-dim target; engine channel-only rule → 0 engine top-1/top-3 |
| I6 | conversion_rate (mix) | 5/5 | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 | mix by construction; engine never outputs mix → 0 engine top-1/top-3 |
| I7 | sessions+cr | 0/5 | 0/5 | 0/5 | 5/5 | 4/5 | 5/5 |  |
| C1 | none | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 | negative control; correct pattern=none |

I7 both-targets-in-top-3: agent 0/5  |  engine 3/5

## d. C1: tested-claim false positives and untested claims

| arm | false positives (strong/plausible) | untested claims |
|---|---|---|
| agent (5 runs, 5 dates) | 1 | 14 |
| engine (5 dev dates) | 1 | 13 |

## e. Rejected claims

**Overall:** 0 rejected claims across all runs.

**By reason (all runs):**

| reason | count |
|---|---|

**Per incident:**

| incident | invalid_strength | total |
|---|---|---|
| I1 | 0 | 0 |
| I2 | 0 | 0 |
| I3 | 0 | 0 |
| I3b | 0 | 0 |
| I4 | 0 | 0 |
| I5 | 0 | 0 |
| I6 | 0 | 0 |
| I7 | 0 | 0 |
| C1 | 0 | 0 |

**Top submitted-strength values (from llm_response trace):**

| submitted strength | count | valid? | correct word |
|---|---|---|---|
| exact | 136 | ✓ | exact ✓ |
| strong | 33 | ✓ | strong |
| untested | 31 | ✓ | untested ✓ |
| plausible | 4 | ✓ | plausible |

**Top 5 (row verdict, claimed strength) mismatches:**

All 729 rejected claims are `invalid_strength`. The model copies ledger verdict strings directly instead of using the agent-schema strength words. Inferred (row_verdict, claimed_strength) pairs by submitted value:

| rank | row verdict (inferred) | claimed strength | count | fix |
|---|---|---|---|---|

## f. Tool calls per run

**test_dimension calls distribution (all runs):**

| td calls | run count |
|---|---|
| 7 | 1 |
| 9 | 40 |

Total test_dimension calls: 367

**Total tool calls (all tools) distribution:**

| total tc | run count |
|---|---|
| 9 | 1 |
| 11 | 40 |

Total tool calls across all runs: 449

## g. Determinism

Among 41 (incident, date) groups:
- **All-3-ok, identical:** 41/41 (when all 3 runs succeed, they agree 100% of the time)
- **Consistently failing** (all 3 fail same status): 0
- **Mixed** (some ok, some failed): 0

No dates with all 3 ok but differing answers — perfectly deterministic when successful.


## h. Failure analysis

### I1

- Score B top-1: agent 1/5 (20%) vs engine 3/5 (60%)
- Score B pattern: agent 1/5 (20%) vs engine 5/5 (100%)
- Score A sessions/channel/Organic Search: agent 2/5 (40%) vs engine 5/5 (100%)

**Failure reasons:**

- `ok_but_wrong_answer`: 4 run(s)

**Trace excerpts:**

```
excerpt 1: pattern=mix; claims=[Organic Search sessions strong, None conversion_rate arithmetic]; submit_answer: pattern=mix n_claims=5 n_accepted=5 n_rejected=0
```

```
excerpt 2: pattern=mix; claims=[None conversion_rate arithmetic, None conversion_rate arithmetic]; submit_answer: pattern=mix n_claims=3 n_accepted=3 n_rejected=0
```

### I2

- Score B top-1: agent 0/5 (0%) vs engine 5/5 (100%)
- Score B pattern: agent 0/5 (0%) vs engine 5/5 (100%)
- Score A conversion_rate/channel/Referral: agent 3/5 (60%) vs engine 5/5 (100%)

**Failure reasons:**

- `ok_but_wrong_answer`: 5 run(s)

**Trace excerpts:**

```
excerpt 1: pattern=mix; claims=[None conversion_rate arithmetic, None conversion_rate arithmetic]; submit_answer: pattern=mix n_claims=3 n_accepted=3 n_rejected=0
```

```
excerpt 2: pattern=mix; claims=[None conversion_rate arithmetic, desktop conversion_rate strong]; submit_answer: pattern=mix n_claims=6 n_accepted=6 n_rejected=0
```

### I3

- Score B top-1: agent 0/5 (0%) vs engine 3/5 (60%)
- Score B pattern: agent 0/5 (0%) vs engine 5/5 (100%)
- Score A aov/channel/Direct: agent 0/5 (0%) vs engine 5/5 (100%)

**Failure reasons:**

- `ok_but_wrong_answer`: 5 run(s)

**Trace excerpts:**

```
excerpt 1: pattern=mix; claims=[None conversion_rate arithmetic, None conversion_rate arithmetic]; submit_answer: pattern=mix n_claims=5 n_accepted=5 n_rejected=0
```

```
excerpt 2: pattern=mix; claims=[None conversion_rate arithmetic, None conversion_rate arithmetic]; submit_answer: pattern=mix n_claims=6 n_accepted=6 n_rejected=0
```

### I3b

- Score B pattern: agent 0/1 (0%) vs engine 1/1 (100%)

**Failure reasons:**

- `ok_but_wrong_answer`: 1 run(s)

**Trace excerpts:**

```
excerpt 1: pattern=mix; claims=[None conversion_rate arithmetic, desktop conversion_rate strong]; submit_answer: pattern=mix n_claims=6 n_accepted=6 n_rejected=0
```

### I5

- Score B pattern: agent 0/5 (0%) vs engine 5/5 (100%)
- Score A conversion_rate/device/desktop: agent 3/5 (60%) vs engine 4/5 (80%)

**Failure reasons:**

- `ok_but_wrong_answer`: 5 run(s)

**Trace excerpts:**

```
excerpt 1: pattern=mix; claims=[None conversion_rate arithmetic, None conversion_rate arithmetic]; submit_answer: pattern=mix n_claims=3 n_accepted=3 n_rejected=0
```

```
excerpt 2: pattern=mix; claims=[None aov arithmetic, United States aov material_untested]; submit_answer: pattern=mix n_claims=5 n_accepted=5 n_rejected=0
```

### I7

- Score B top-1: agent 0/5 (0%) vs engine 4/5 (80%)
- Score B pattern: agent 0/5 (0%) vs engine 5/5 (100%)
- Score A sessions/channel/Organic Search: agent 3/5 (60%) vs engine 5/5 (100%)
- Score A conversion_rate/channel/Referral: agent 2/5 (40%) vs engine 5/5 (100%)

**Failure reasons:**

- `ok_but_wrong_answer`: 5 run(s)

**Trace excerpts:**

```
excerpt 1: pattern=mix; claims=[None conversion_rate arithmetic, None conversion_rate arithmetic]; submit_answer: pattern=mix n_claims=5 n_accepted=5 n_rejected=0
```

```
excerpt 2: pattern=mix; claims=[Organic Search sessions strong, United States conversion_rate plausible]; submit_answer: pattern=mix n_claims=3 n_accepted=3 n_rejected=0
```


## i. Candidate v1 prompt changes

_Not implemented — see failure analysis above for motivating patterns._

