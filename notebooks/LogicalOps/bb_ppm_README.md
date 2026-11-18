# Gross-code PPM notebook

[bb_ppm.ipynb](bb_ppm.ipynb) is the single BB PPM demonstration. It contains
the saved construction tables, the selected operation sequence, and the complete
three-point LER results. All Markdown cells are titles; usage details live here.

The protocol and compiler live in `lightstim/`. Sampling, checkpointing, and
plotting live in [the BB PPM benchmark](../../benchmarks/logical_ops/README_bb_ppm.md).
There is no separate executed or diagnostic notebook in the repository.

## Install and open

Use Python 3.11 or 3.12 and run these commands from the repository root:

```bash
python3 -m venv venv
venv/bin/python -m pip install -e '.[plot]'
venv/bin/python -m pip install -r notebooks/LogicalOps/requirements-bb-ppm.txt
venv/bin/python -m ipykernel install --user --name lightstim --display-name LightStim
```

Open the notebook in a Jupyter-compatible editor and select the **LightStim**
kernel. To refresh its saved outputs without creating a second notebook:

```bash
venv/bin/jupyter-execute notebooks/LogicalOps/bb_ppm.ipynb --inplace --timeout=-1
```

The default `RUN_SAMPLING = False` regenerates the reports and reads the bundled
reference counts. It performs no physical sampling or decoding and works without
the original server's files. Only the compact results and their provenance are
bundled; the original raw samples and per-shot checkpoints remain in the local
experiment archive.

## Cells and editable parameters

| Cell | Contents |
|---|---|
| Setup | Noise points, shot budgets, frozen decoder parameters, paths, and run switch |
| 1 | The 15 basic LPU measurements and stabilizer definitions |
| 2 | The 36 shifts and 540 native measurements |
| 3 | The 245 nontrivial rotation axes, with `axis (A)` and `chosen_joint (Q₁A)` headers |
| 4 | Target PPM, search path, and complete operation table |
| 5 | Optional benchmark call, resource and parameter tables, LER results and plot |

Change the target at the beginning of Cell 4:

```python
ppm_sequence = [BBPPMStep(((2, "X"), (7, "Z"), (12, "X")))]
```

Cell 4 embeds a PNG with fixed row height `h = 40`. Each group occupies exactly
its number of rows times `h`; the brace and centered label do not affect row
layout. GitHub can scale the entire image but cannot reflow its rows.

Generated reports go to the ignored
`benchmarks/logical_ops/results/bb_ppm_reports/` directory. Printed paths are
relative. Stabilizer definitions use the `stabilizer_definition/` subdirectory.

## Run a new experiment from the notebook

Set `RUN_SAMPLING = True` in Setup, choose `SHOTS_BY_P` and `WORKER_PROCESSES`,
and choose a new `RUN_DIR` for a changed target or configuration. Choose a new
`SAMPLE_SEED` for an independent experiment; the same seed and batching repeat
the same physical sample streams. Re-execute
Setup and Cells 1–5. Cell 5 calls the same `run_campaign` function as the CLI.
The recorded experiment used 192 worker processes, each with one decoder
thread; lower the worker count for a smaller machine.

An interrupted run can resume from the same directory with the same experiment
configuration. Completed physical samples and decoded shots are reused only
after their identities are checked. Interrupting the notebook stops its
foreground run; for an unattended run, use the benchmark CLI in a persistent
terminal session. There is no automatic Git publication or notebook-writing
background process.

With `RUN_SAMPLING = False`, `RESULTS_PATH` can point to a completed local run's
CSV. The display validates its manifest against the compiled plan, noise,
boundaries, rounds, decoder settings, and shot budgets. Changing the target
does not relabel the bundled results as measurements of the new target.

## Recorded result

The target `X2 Z7 X12` is a complete circuit of seven measurements and four
shifts, using the original LightStim `circuit_level_with_idling` noise model,
ten extraction rounds per basic measurement, uniform X preparation/readout,
and noiseless initial/final boundaries. All five physical noise rates equal p.

| p | Shots | Logical failures | LER | Nonconverged |
|---|---:|---:|---:|---:|
| 0.0001 | 100,000 | 0 | 0 observed | 4 |
| 0.0005 | 10,000 | 2 | 0.0002 | 6 |
| 0.001 | 10,000 | 8 | 0.0008 | 72 |

The displayed Relay-BP parameters were selected for this full circuit on
training samples, then frozen before these independent fixed-shot runs. They
are **not the paper's original hyperparameters**. Historical 50-shot samples
are not pooled into these counts.

LER is the fraction of shots with any mismatch among the circuit's ten declared
LightStim Observables. An unsatisfied syndrome is counted separately as
nonconvergence; it is not automatically a logical failure. The table and plot
use exact 95% binomial intervals, including a nonzero bound for zero observed
failures. The three-point line is a visual guide, not a fit.

See [BB PPM API](../../docs/api/bb_ppm.md) for protocol semantics and limits of
comparison with isolated-instruction results in the paper.
