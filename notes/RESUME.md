# SignalTrace — resume point (2026-10-07)

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
| M6 | `eabbd2f` | Agent tool layer + loop + resumable runner; fake-model tested (142/142 tests) |

---

## Frozen boundaries

**Engine frozen at `engine-frozen-m5`.**  Do not modify:
- `detection/`, `investigation/`, `metrics/`

**Held-out data frozen at `heldout-frozen`.**  Do not read:
- `evaluation/heldout/`
- `evaluation/results/heldout_engine.json`

Violation of either boundary invalidates the benchmark.

---

## What is forbidden during agent development

- Reading or reasoning about held-out ground-truth files (above)
- Selecting or tuning anything based on held-out outcomes
- Running `--split heldout` before tagging `agent-frozen`
- Changing detection/, investigation/, or metrics/ for any reason
- Evaluating on a subset of dev dates (--limit runs are for debugging only,
  never count toward prompt selection — see PLAN.md Section 4.2)

---

## Starting Ollama (external SSD required)

The local model lives on the external SSD.  Use the shell functions defined in
`~/.zshrc` (or wherever they were added):

```
ollama-start    # mounts SSD if needed, starts the Ollama server
ollama-stop     # stops the server
```

The server must be running before any real-model step.  Confirm with:

```
ollama list
```

When the SSD is disconnected, the server cannot start.  All agent code is
safe to import and test with FakeClient — nothing connects at import time.

---

## Next pending steps (in order)

1. **Reconnect SSD** and run `ollama-start`.

2. **Pin model digest** — edit `agent/config.py` to record the exact
   `sha256:…` digest of the running model (guards against silent updates).
   Run `agent/smoke_test.py` to confirm the model responds and tool-calling
   works end-to-end.

3. **Plumbing run** — `python -m evaluation.run_agent --split dev --limit 3`
   to verify the runner produces valid JSONL, scores compute, and no import
   errors surface before committing to a full overnight run.

4. **Dev iteration protocol** (PLAN.md Section 4.2) — up to 5 prompt
   versions (v0–v4), each evaluated on all 41 dev dates × 3 runs ≈ 123 runs
   (~4–6 hours).  Plan overnight runs.  Selection rule: highest dev Score B
   top-1 count; tie-break fewer rejected claims, then fewer failed runs.

5. **think=False vs think=True comparison** on the winning prompt, same full
   dev run.

6. **Freeze and held-out** — commit and tag `agent-frozen`, then run once:
   `python -m evaluation.run_agent --split heldout --i-confirm-heldout`

See PLAN.md Sections 4.1 and 4.2 for full details.
