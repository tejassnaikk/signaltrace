# SignalTrace: status (2026-10-22)

**The project is complete (M1 to M7).** Nothing is pending except the optional M8 items in PLAN
Section 7.

## What this project is

A deterministic KPI root-cause engine plus an evidence-constrained LLM agent that operates it.
The engine detects revenue anomalies, decomposes them into factor contributions
(sessions x conversion_rate x aov), and validates every conclusion against a significance-tested
evidence ledger. The agent calls three tools (get_factor_decomposition, test_dimension,
get_ledger_summary) plus submit_answer, and only cites what the engine revealed.
See `README.md` for the summary and `PLAN.md` for architecture, decisions and protocol.

## Milestone status

| Milestone | Commit | Notes |
|---|---|---|
| M1 | `d1313e7` | GA360 ingest, metric series, baseline characterization |
| M2 | `0d1e33a` | Component detection, concentration check, separability gate |
| M3 | `43f231d` | Benchmark incidents (34/36 pass), holiday screening |
| M4 | `4b7a950` | Streamlit dashboard |
| M5 | `5d0e956` | Engine scored on the held-out set; tags `engine-frozen-m5`, `heldout-frozen` |
| M6 | `faad934` | Agent frozen (v1, think off, config hash `f854dee9`); tag `agent-frozen` |
| M7 | `18cd61a`, `282e292` | Held-out run, integrity check, comparison, sign test, README |

## Where the results are

- Held-out report: `evaluation/results/agent_heldout_f854dee9_report.md`
- PLAN Section 7.1: counts, integrity result, process notes
- Integrity scripts: `evaluation/integrity/` (`replay.py` prints `INTEGRITY: PASS`)

## Frozen boundaries (still apply)

- Engine frozen at `engine-frozen-m5`: do not modify `detection/`, `investigation/`, `metrics/`.
- Agent frozen at `agent-frozen`: v1, think off, config hash `f854dee9`. Do not change `agent/`.
- The held-out run was executed once (219 runs, 73 dates x 3). Do not run `--split heldout` again.
- The held-out files may now be read; they must not be used to tune anything.

## Known follow-ups (not done, would need unfreezing)

- `run_agent.py` defaults to `--version v4`; the frozen agent is v1.
- The checker reason `no ledger entry` also covers rows that exist but are not claimable.
- The engine returns no explicit "insufficient history" result for the one date hit in M5 (PLAN 5.6).
