# Gross-code PPM benchmark

This benchmark measures complete in-module Pauli product measurement circuits
built by `lightstim.protocols.bb_ppm.BBPPMExperiment`. It uses the existing
LightStim noise injector and upstream Relay-BP detailed decoding, with fixed
shot budgets and separate logical-failure and nonconvergence counts.

The [single demonstration notebook](../../notebooks/LogicalOps/logical_PPM_BB.ipynb)
shows the recorded `Y1` results from 1,000,000 shots each at `p = 0.0005` and
`0.001`, and 100,000 shots at `p = 0.002`.

## Install and open the notebook

Use Python 3.11 or 3.12. From the repository root:

```bash
python3 -m venv venv
venv/bin/python -m pip install -e '.[plot]'
venv/bin/python -m pip install 'relay-bp[stim]==0.2.2' 'nbclient>=0.10' 'nbformat>=5.10' 'ipykernel>=6'
venv/bin/python -m ipykernel install --user --name lightstim --display-name LightStim
```

Open `notebooks/LogicalOps/logical_PPM_BB.ipynb` in a Jupyter-compatible editor and select
the **LightStim** kernel. `RUN_SAMPLING = False` displays the bundled Y1
results without requiring the server's raw samples. To execute a new experiment,
set `RUN_SAMPLING = True`, choose a new `RUN_DIR`, and run the notebook in order.
The target is `ppm_sequence` in Cell 4; noise points, shot counts, worker count,
and decoder parameters are in the setup cell. The `Y1` example keeps ten
extraction rounds per basic measurement and the frozen full-circuit decoder
parameters.

The circuit figure uses Stim's `detslice-with-ops-svg` diagram of the entire
noiseless experiment. For Y1 it shows all 378 physical qubits in each of the
176 time panels. The [Y1 circuit archive](reference/bb_ppm/y1_large_20261124/full_circuit.zip)
contains the original `full_circuit.svg` for zooming and the Notebook's smaller
SVG, which reuses repeated shapes. The Notebook selects this display copy by
the SHA-256 of the original drawing.

To refresh the same notebook's saved outputs:

```bash
venv/bin/jupyter-execute notebooks/LogicalOps/logical_PPM_BB.ipynb --inplace --timeout=-1
```

## Run

From the repository root, using the LightStim virtual environment:

```bash
# New five-shot example with the recorded target and frozen parameters:
venv/bin/python benchmarks/logical_ops/run_bb_ppm.py \
  --target 'Y1' --p-values 0.0001 0.0005 0.001 \
  --shots 5 --workers 5 \
  --run-dir benchmarks/logical_ops/results/bb_ppm_run \
  --output benchmarks/logical_ops/results/bb_ppm_results.csv

# A small execution check, not a low-LER estimate:
venv/bin/python benchmarks/logical_ops/run_bb_ppm.py --quick --workers 2
```

The gross code has fixed parameters `[[144,12,12]]`; `--quick` reduces the shot
budget rather than inventing a smaller code distance. Use `--help` for the
complete CLI. A single `--shots` value applies to every requested p; multiple
values give one budget per p in the same order. The run ends at the fixed sample
count, with no error-count stopping or postselection.

| Setting | Meaning |
|---|---|
| `--target` | Logical Pauli product, such as `X2 Z7 X12` |
| `--p-values` | Physical noise rates; each sets all five LightStim noise probabilities |
| `--shots` | Fixed shot count, or one count per noise point |
| `--workers` | Worker processes, with one decoder thread each |
| `--run-dir` | Sampled inputs, scientific identity, and per-shot checkpoints |
| `--output` | Completed-point CSV and adjacent manifest |
| `--seed` | Physical sampling seed namespace, recorded in the run configuration |
| `--quick` | Small execution check; explicit budgets override its defaults |

The Python entry point is `benchmarks.logical_ops.bb_ppm_runner.run_campaign`.
It accepts a `sequence` of `BBPPMStep` objects and optional precompiled `plans`;
the notebook passes its displayed plans directly to the physical experiment.
Use a new run directory for a different target, noise grid, budget, circuit,
or decoder configuration. The source identity is also checked on resume.
The physical seed policy is `SeedSequence([seed, point_index, batch_index])`.
Repeating the seed, point order, and batch size repeats the sample streams;
use a new `--seed` for an independent experiment.

A run samples the physical circuit, saves those inputs, and decodes each shot
using a fresh Relay-BP decoder. Checkpoint recovery reuses the same physical
samples and completed decoded shots. It does not generate replacement samples
or merge independent experiments. Runtime worker counts can be changed when
resuming without changing the scientific configuration.

The CLI runs in the foreground. For unattended execution, use a persistent
terminal session. The program writes neither Notebook files nor Git commits.
Use `--workers` to fit the CPU allocation. Each worker uses one decoder thread.
The notebook uses up to 32 workers when sampling is enabled.

## Experiment and metrics

The notebook's `Y1` target uses one native measurement and no shifts. The
benchmark CLI still defaults to the composite target `X2 Z7 X12`, which expands
to seven native measurements and four shifts. Both examples use ten extraction
rounds per native measurement and uniform X preparation/readout bases with
noiseless boundaries. The saved resource table reports the executed circuit's
qubits, ticks, detectors, and Observables. Noise is the unmodified LightStim
`circuit_level_with_idling` model. The strict DEM retains correlated faults
without graphlike decomposition.

The BB implementation uses the unchanged upstream `CircuitBuilder` and
`SyndromeTracker`. Conditional Pauli corrections are tracked classically.
Native Bell readouts are expressed as their joint parity plus the independent
ancilla result for tracking, then mapped back to the physical measurement
records. Shift measurements retain the records needed by subsequent rounds.
Detectors and Observables are generated by the existing tracker.

For direct library use, sweep noise points with
`experiment.build_noisy_circuit(noise_params)` after `experiment.build()`.
The benchmark saves the experiment's `noise_schedule` and uses it when adding
noise. This preserves idle-noise placement from the audited schedule even
though the returned physical circuit has no conditional Pauli gates.

The frozen full-circuit Relay-BP profile is:

```python
{
    "alpha": 0.8, "gamma0": 0.5,
    "pre_iter": 320, "num_sets": 600, "set_max_iter": 60,
    "gamma_dist_interval": (-0.1, 0.5), "stop_nconv": 5,
    "stopping_criterion": "nconv", "logging": False, "seed": 0,
}
```

These are tuned full-circuit parameters, not the paper's original profile.
The decoder predicts the circuit's automatic Observable flips. Any mismatch
counts as one logical failure. Syndrome consistency is independently verified
from the returned correction and detector matrix; it is reported as convergence
and is not substituted for the logical-failure criterion. This detailed
benchmark does not change the generic `SimulationPipeline` failure policies.

The `Y1` demo retains uniform X initialization/readout and the frozen tuned
Relay-BP profile, checking only the automatic Observables available for those
boundaries; it is not a reproduction of Table 2. The paper uses a Bell-reference
construction sensitive to both X and Z logical errors, with 23 logical checks
for a gross-code measurement and ideal initial/final boundaries
([Appendix A.7](https://arxiv.org/html/2506.03094v1#A7)). Its gross-code in-module
Table 2 estimates come from fits to sampled failure spectra, alongside direct
circuit-noise Monte Carlo checks
([Section 2.6 and Figure 7](https://arxiv.org/html/2506.03094v1#S2.SS6)).

## Results and plot

Each completed fixed-size noise-point cohort is written immediately to the
CSV. An adjacent manifest binds the CSV hash to the experiment, parameters,
source/input identities, and requested shot budgets. Partial runs expose only
completed cohorts, never incomplete fixed-N estimates.

```bash
# Plot a completed local run (the default CSV):
venv/bin/python -m benchmarks.logical_ops.plot_bb_ppm

# Reproduce the recorded three-point figure from a fresh clone:
venv/bin/python -m benchmarks.logical_ops.plot_bb_ppm \
  --input benchmarks/logical_ops/reference/bb_ppm/y1_large_20261124/results.csv \
  --output benchmarks/logical_ops/results/fig_bb_ppm.png
```

The [recorded Y1 figure](reference/bb_ppm/y1_large_20261124/ler.png) uses
a linear physical error rate axis and a logarithmic LER axis. Error bars
show exact 95% Clopper–Pearson intervals. For datasets with zero observed
failures at any point, the LER axis uses a linear scale to show zero and its
upper confidence bound. Lines connect measured points as a guide to the eye.

| CSV field | Meaning |
|---|---|
| `p`, `shots` | Physical noise rate and completed fixed sample count |
| `logical_failures`, `ler` | Any Observable mismatch count and its fraction |
| `nonconverged`, `nonconvergence_rate` | Unsatisfied syndrome count and fraction |
| `converged_correct`, `converged_logical_failure` | The two converged outcome categories |
| `unconverged_correct_observables`, `unconverged_logical_failure` | The two nonconverged outcome categories |
| `ler_95_exact_two_sided_low`, `ler_95_exact_two_sided_high` | Exact two-sided 95% binomial interval |
| `ler_95_one_sided_upper` | Exact one-sided 95% upper bound |
| `sum_decode_cpu_seconds` | Sum of per-shot decoder timings, not total wall-clock runtime |
| `source_manifest_sha256`, `decoder_manifest_sha256` | Archived input and decoder identity hashes |

`reference/bb_ppm/y1_large_20261124/` contains the 2.1-million-shot Y1 CSV and
manifest used by the notebook. Its parent directory contains the earlier
five-shot-per-point example. Raw samples and per-shot
checkpoints remain in the local experiment archive, outside Git. A new run uses
the current runner's recorded seed policy and is a new statistical experiment.
