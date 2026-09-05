# Experiment provenance and endpoint definitions

Audit date: 2026-09-06. Source revision:
`da935366521a00c25ae940ea0581ac9800064782`.
Related [paper](https://github.com/Jeuners/Time_Dilation_in_LLM_Agent_Systems)
and [implementation contract](../docs/TIME-DILATION.md).

These are historical scripts and result files, not an unattended benchmark
suite. Running a script makes real model calls against a live server, may spawn
agents, and overwrites its named result file. Preserve the original data and use
a separate experimental checkout and server for a new run.

## Published runs

| Paper section | Script / raw data | Trials | Result with / without temporal information |
| --- | --- | --- | --- |
| §6.4 pilot | [dragon3.py](dragon3.py), [data](dragon3-results.json) | 20 | Execution success: 5/10 vs 3/10. |
| §6.5 scaled run | [dragon4.py](dragon4.py), [data](dragon4-results.json) | 60 | Execution success: 18/30 vs 21/30. |
| §6.6 neutral roles | [dragon5.py](dragon5.py), [data](dragon5-results.json) | 200 | Oracle agreement: 100/100 vs 55/100; execution success: 95/100 vs 57/100. |

The older `dragon.py` and `dragon2.py` files are retained as exploratory
artifacts. The pilot data's stored summary reports oracle agreement on winnable
trials of 9/10 vs 7/8; the paper's earlier 7/7 vs 3/5 subgroup statement is not
reproduced by this committed file and needs a separately identified source if
retained as a historical result.

## What dragon5 measures

- Neutral names are assigned to fast/slow backends randomly per trial;
  presentation order is also randomised. Treatment/control assignment strictly
  alternates. The seed is 2026.
- The treatment supplies rolling median seconds per action over up to 12
  observations per backend class. `live_rates` records CDC values separately;
  the prompt does not use those rates or τ.
- The oracle chooses the lower estimated cost, using the same medians supplied
  to treatment. `correct` means agreement with that estimate, not independent
  proof that the selected action meets its deadline.
- `survived` checks `exec_s <= deadline_s`. Calibration and decision latency
  are outside this endpoint. A geometric-mean deadline separates estimated
  costs but cannot guarantee immunity to execution variance.
- The decision parser accepts the first actor name mentioned and defaults to
  the first presented name if neither appears. A future run should report
  unparsable responses separately.

Recounting the 200 committed trial records reproduces both published primary
counts. A descriptive check using the **rounded stored values** and
`decision_s + exec_s <= deadline_s` yields 93/100 vs 54/100. This is a post-hoc
alternative endpoint, not a new experiment or a replacement for the original
endpoint. It still excludes calibration and other preceding work.

## Statistical precision

The stored Fisher p-values in `dragon5-results.json` are `0.0` because
`dragon5.py` rounds them to six decimal places. They are not mathematical zero.
Recomputing the two-sided hypergeometric tail from the trial-count tables gives:

| Endpoint | Table, successes/failures | Two-sided Fisher p |
| --- | --- | --- |
| Oracle agreement | `[100, 0]`, `[55, 45]` | `8.927340409087128e-17` |
| Execution success | `[95, 5]`, `[57, 43]` | `1.2688661009449438e-10` |

Keep the historical files unchanged. New result writers should retain precision
and render very small values in scientific notation.

## Requirements for a new run

Record the code revision, full agent configuration, actual model identifiers
and digests when available, provider, hardware, concurrency, prompt templates,
sampling settings, seed, warm-up policy, measurement clock, raw durations and
errors. Exclude credentials from the manifest. The script's `DECIDER` is a
runtime agent ID; its comment is not evidence of the model actually configured
under that ID. Confirm the actual agent before starting.

Pre-specify the oracle, invalid-response handling and whether the deadline
includes decision time. Store full-precision measurements and keep the new run
under a unique ID. Use an independent outcome measure alongside agreement with
the same estimates shown to treatment. Model substitutions constitute a new
experimental condition, not automatic replication of the historical run.
