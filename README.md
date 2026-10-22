# SignalTrace

SignalTrace is an engine that finds why a business metric moved, and a local LLM agent that may only cite the engine's evidence.

## The result

Held-out comparison of the frozen agent against the engine, counted by date. All 73 held-out dates gave identical answers on all 3 agent runs, so a date is the unit. Top-1 is scored on the 46 dates whose incident has a named cause (I1, I2, I3, I3b, I5, I7).

| measure | agent | engine |
|---|---|---|
| Top-1 cause correct | 17/46 dates | 7/46 dates |
| C1 null dates with a confident false claim | 4 of 9 | 1 of 9 |
| Broad, mix or none pattern ever produced | never | never |

Paired by date: the agent alone was right on 12 dates, the engine alone on 2 (exact two-sided sign test, p = 0.013). The two arms gave the same pass/fail on 54 of 55 date-targets of Score A (right segment, right direction), which is expected because both read the same ledger.

The engine's top-1 comes from a naive selection rule that was fixed before the engine ran on held-out data (PLAN 5.5), not from the engine's own diagnosis. So my claim is that the agent is better at picking the top cause from the same evidence. I do not claim that it is a better diagnostician.

Full tables, the integrity check and the dev comparison are in `evaluation/results/agent_heldout_f854dee9_report.md` and `notes/PLAN.md` section 7.1.

## What it does

**The engine** is deterministic and has no LLM in it.
- It models Revenue = Sessions x Conversion Rate x AOV, and the identity holds exactly by construction.
- It detects anomalies at the component level, then tests every segment of every dimension (channel, device, country).
- Count-based tests use a per-segment dispersion correction with a negative-binomial form, because session counts are far more overdispersed than a Poisson model assumes.
- The validator is contribution-first: a segment must carry a material share of the move before its significance matters.
- Every result goes into an append-only evidence ledger, including the negative ones.

**The agent** is qwen3:8b served by Ollama, so it runs locally and costs nothing per call.
- Its tools return engine output only: a factor decomposition, a per-dimension test, and a ledger summary.
- It cites rather than transcribes. Each claim names an evidence id, and the harness fills in the metric, segment, direction and verdict from the ledger.
- A checker rejects any claim it cannot match to a ledger entry, and any claim whose direction or strength does not match that entry.

## How it was evaluated

- **Data:** the GA360 sample (Google Merchandise Store), 12 months, aggregated to date x channel x device x country.
- **Incidents:** nine incident types with known answers. Eight are injected into real data (I1, I2, I3, I3b, I4, I5, I6, I7) and one is a null control (C1).
- **Dev and held-out:** 41 dev dates were used for all tuning. 73 held-out dates (9 per incident, 1 for I3b) came from a seeded draw (seed 20261001) and were frozen before any agent code existed (tag `heldout-frozen`, SHA-256 recorded in PLAN 5.4).
- **Freezes:** the engine was frozen (tag `engine-frozen-m5`) and the agent was frozen (tag `agent-frozen`) before the single held-out run.
- **Selection rule:** at most 5 prompt versions, and the rule for choosing among them was written down before the first real run. Every version's dev results are kept, including the failures.

| candidate (dev) | top-1 (of 26 named-cause dates) |
|---|---|
| prompt v0 | 12 |
| prompt v1 (selected) | 16 |
| prompt v2 | 1 |
| prompt v3 | 13 |
| prompt v4 | 15 |
| v1 with think mode | 10 |
| engine | 15 |

Prompt v2 collapsed to a top-1 of 1. Think mode lowered top-1 from 16 to 10. On dev the two arms were close (16 vs 15). On held-out the gap widened (17 vs 7 dates). Dev dates were screened and re-swapped during benchmark construction and held-out dates were not, so both arms score lower on held-out.

## What went wrong, and how it was handled

- **Two invalidated held-out attempts.** The first loaded dev dates instead of held-out dates. The second hit the injector's dev-date guard. I caught both before any model call on a held-out date, kept the files without analyzing them, and fixed the runner (PLAN 4.10.1, 4.10.2).
- **Truncated context.** An early run lost the `submit_answer` schema because Ollama loaded the model with an 8192 context. I fixed it with compact tool output, a context guard that fails loudly, and a token cap (PLAN 4.1 amendment).
- **Shared-config bug.** A mutable options dict was mutated by the client library mid-run, and nine later runs failed with HTTP 400. I made the config immutable, invalidated that dev run and redid it.
- **The `--version` default.** The runner defaults to v4 and the frozen agent is v1. The held-out guard correctly refused the default, so the run used `--version v1`. I have not changed the default.
- **"No ledger entry" label.** The checker uses this reason for rows that exist and were shown to the model but are marked not claimable. I checked all 300 held-out claims with this label by replaying the tool layer, and none were harness mismatches. I left the misleading label as it is because the agent code is frozen.
- **A corrected finding.** PLAN 4.9 said thinking "cited nonexistent evidence ids". The replay showed all 8 were non-claimable rows. I corrected the finding in place and kept the original text.
- **One interrupted run.** The held-out run stopped after 194 runs, and I resumed it with the same command for the last 25. Both sessions ran the frozen configuration.

The integrity scripts are in `evaluation/integrity/`. `replay.py` prints `INTEGRITY: PASS` when no claim is a harness mismatch, and `analyze.py` reproduces the comparison tables and the sign test.

## Limitations

- Only 12 months of data, so there is no seasonality model.
- AOV segments cannot be significance-tested with daily aggregates.
- Device and country are each dominated by one segment.
- The agent never identifies broad, mix or none. It outputs `localized` on every run.
- The agent over-claims on normal days.
- One small local model was used.
- The benchmark incidents are self-designed. I wrote the ground truth myself, so it is not independent.

## Run it locally

Python 3.13 is the version used here.

1. Create the environment.
   ```
   python -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
2. Get the data. Run `sql/ga360_export.sql` in BigQuery and save its two result sets as `data/raw/ga_sessions_daily.csv` and `data/raw/order_concentration.csv`. I reconstructed that query from the loader's expected schema, because the original export query was not saved. The engine also needs the `order_concentration` table, and there is no loader for it in this repo.
3. Build the DuckDB file (this drops and recreates the tables).
   ```
   .venv/bin/python ingest/load_ga.py
   ```
4. Run the dashboard.
   ```
   .venv/bin/streamlit run app/dashboard.py
   ```
   It serves on http://localhost:8501.
5. Run the engine benchmark on the dev dates.
   ```
   .venv/bin/python -m evaluation.score
   .venv/bin/python -m evaluation.arm_neutral
   ```
6. Install Ollama, pull the model, and run the agent on dev. The pinned model is `qwen3:8b` (digest in `agent/config.py`). Use `--version v1` for the frozen agent, and add `--limit 2` for a short trial.
   ```
   ollama pull qwen3:8b
   .venv/bin/python -m evaluation.run_agent --split dev --version v1 --limit 2
   ```
7. Run the tests and the integrity check.
   ```
   .venv/bin/python -m pytest tests
   .venv/bin/python -I evaluation/integrity/replay.py evaluation/results/agent_heldout_f854dee9.jsonl --split-at 194
   ```

The held-out split of `run_agent` needs the `agent-frozen` tag, `--version v1` and `--i-confirm-heldout`. It was run once and should not be run again.

## Repo map

| path | contents |
|---|---|
| `ingest/` | GA360 load into DuckDB, and the baseline characterization |
| `metrics/` | the metric dependency graph and segment eligibility |
| `detection/` | expectation model, component anomaly detection, all measured settings |
| `investigation/` | hypotheses, attribution, dispersion, validator, evidence ledger, orchestrator |
| `agent/` | frozen agent: model pin, tools, prompt versions, rendering, loop and checker |
| `evaluation/` | incident injector, ground truth, held-out selection, scorers, runners |
| `evaluation/results/` | every dev and held-out result file and report |
| `evaluation/integrity/` | the replay and analysis scripts behind the integrity check |
| `app/` | Streamlit dashboard |
| `sql/` | reconstructed BigQuery export query for the two input CSVs |
| `tests/` | tests for the agent tools, loop and runner |
| `notes/` | `PLAN.md` (design, decisions, protocol, results), `baseline.md`, `RESUME.md` |

Start with `notes/PLAN.md` for the decisions and `evaluation/results/agent_heldout_f854dee9_report.md` for the held-out numbers.
