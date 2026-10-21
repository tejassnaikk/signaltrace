# SignalTrace — resume point (2026-10-20)

## What this project is

A deterministic KPI root-cause engine plus an evidence-constrained LLM agent
that operates it.  The engine detects revenue anomalies, decomposes them into
factor contributions (sessions × conversion_rate × aov), and validates every
conclusion against a significance-tested evidence ledger.  The agent calls
three tools (get_factor_decomposition, test_dimension, submit_answer) and
never performs analysis itself — it only cites what the engine revealed.
See PLAN.md for architecture, design decisions, and evaluation protocol.

---

## Milestone status

| Milestone | Commit | Notes |
|---|---|---|
| M1 | `d1313e7` | GA360 ingest, metric series, baseline characterization |
| M2 | `0d1e33a` | Component detection, concentration check, separability gate |
| M3 | `43f231d` | Benchmark incidents (34/36 pass), holiday screening |
| M4 | `4b7a950` | Streamlit dashboard |
| M5 | `5d0e956` | Engine evaluated on held-out set; tagged `engine-frozen-m5` and `heldout-frozen` |
| M6 | complete | Agent frozen at `agent-frozen` tag (commit below); held-out run is next |

**M6 final commit:** see `git log --oneline -1` (tagged `agent-frozen`)

**Post-freeze bug fix:** held-out runner was building work list from dev dates (caught
on the `work: 123 runs (41 dates × 3)` line before any held-out date ran); fixed to
load `evaluation/heldout/heldout_dates.json` with SHA-256 verification.
Agent code is byte-identical to `agent-frozen` (`git diff agent-frozen -- agent/` empty).

---

## Frozen boundaries

**Engine frozen at `engine-frozen-m5`.**  Do not modify:
- `detection/`, `investigation/`, `metrics/`

**Held-out data frozen at `heldout-frozen`.**  Do not read:
- `evaluation/heldout/`
- `evaluation/results/heldout_engine.json`

**Agent frozen at `agent-frozen`.**  Frozen configuration:
- Version: v1 (`agent/versions.py` `FROZEN_AGENT = V1_CFG`)
- think: False (`FROZEN_THINK = False`)
- Config hash: `f854dee9`
- Do not change agent code or configuration between the `agent-frozen` tag and
  the held-out run

Violation of any boundary invalidates the benchmark.

---

## Next step: held-out run

M6 is complete.  The agent is frozen.  Run held-out exactly once:

```
python -m evaluation.run_agent --split heldout --i-confirm-heldout
```

Requirements before running:
1. External SSD connected (`ollama-start`)
2. Model loaded: qwen3:8b digest
   `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`
3. `agent-frozen` tag on HEAD (already created)
4. Script enforces version=v1 and think=False automatically; aborts if
   config_hash ≠ f854dee9

Expected: 73 dates × 3 runs = 219 runs.  No re-runs after this.

---

## What is forbidden

- Reading or reasoning about held-out ground-truth files (above)
- Selecting or tuning anything based on held-out outcomes
- Changing detection/, investigation/, or metrics/ for any reason
- Running `--split heldout` more than once
- Changing the agent configuration (prompt, version, think setting) after
  the `agent-frozen` tag
