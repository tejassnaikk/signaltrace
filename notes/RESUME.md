# Resume point — 2026-08-25

## Shell
    cd ~/signaltrace && source .venv/bin/activate

The venv does NOT auto-activate in a restored terminal session. Check for
`(.venv)` in the prompt before running anything.

## Where we are
M1, M2, M3 complete. The deterministic engine runs end to end:

    python investigation/orchestrator.py 2016-12-26 2017-03-04 2017-04-27

Warnings about all-NaN slices are expected (segments with too few lags).

## Next
1. (optional) write M3 findings into notes/
2. M4 = Streamlit dashboard
3. M5 = stop-safe point: Phase 1 is complete and presentable on its own

## Do not relitigate
- Seasonal module was deliberately deleted, not forgotten. Its thresholds were
  invented and unvalidatable with 12 months of data. See notes/baseline.md.
- AOV segments cannot be significance-tested. The daily export lacks
  within-segment order variance. They are decomposition_only by design.
- Ranking is per-dimension, never pooled. Pooling summed to 287%.

## Open limitations (documented, not bugs)
- Cannot distinguish seasonality from incidents
- Device and country dimensions are near-vacuous (one segment IS the business)
- Judgment-call numbers: materiality 0.05, high_contribution 0.30,
  q 0.05/0.10, concentration note 0.40, concentration check max/median
  order ratio 20x (reasoned, not measured: sits between thin days ~1x and
  the marginal whale 2016-11-26 at 45x)
