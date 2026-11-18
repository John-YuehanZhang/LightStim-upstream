# Gross-code PPM benchmark

This benchmark measures complete in-module Pauli product measurement circuits
built by `lightstim.protocols.bb_ppm.BBPPMExperiment`. It uses the existing
LightStim noise injector and upstream Relay-BP detailed decoding, with fixed
shot budgets and separate logical-failure and nonconvergence counts.

The [single demonstration notebook](../../notebooks/LogicalOps/bb_ppm.ipynb)
imports the benchmark functions. It defaults to displaying the recorded
120,000-shot result and can also start a new experiment. See its
[installation guide](../../notebooks/LogicalOps/bb_ppm_README.md).

## Run

From the repository root, using the LightStim virtual environment:

```bash
# New experiment with the recorded target, noise points, budgets, and parameters:
venv/bin/python benchmarks/logical_ops/run_bb_ppm.py \
  --target 'X2 Z7 X12' --p-values 0.0001 0.0005 0.001 \
  --shots 100000 10000 10000 --workers 192 \
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

## Experiment and metrics

The default target `X2 Z7 X12` expands to seven native measurements and four
shifts, with ten extraction rounds per native measurement. Preparation and
readout use uniform X bases with noiseless boundaries. The clean circuit has
378 physical qubits, 1,115 ticks, 15,510 detectors, and ten declared Observables.
Noise is the unmodified LightStim `circuit_level_with_idling` model. The strict
DEM retains correlated faults without graphlike decomposition.

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
  --input benchmarks/logical_ops/reference/bb_ppm/results.csv \
  --output benchmarks/logical_ops/results/fig_bb_ppm.png
```

The plot uses the repository's paper style and exact 95% Clopper–Pearson
intervals. Zero-error points remain at zero with a nonzero confidence bound.
Lines connect measured points as a guide to the eye, not a fitted model.

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

`reference/bb_ppm/` contains only the recorded summary CSV and compact provenance
manifest. Its counts remain 0/100,000, 2/10,000, and 8/10,000 at p=0.0001,
0.0005, and 0.001, respectively. The original raw samples, per-shot records,
and frozen legacy tools are retained in the local experiment archive, not in
Git. A new run uses the current runner's recorded seed policy; it is a new
statistical experiment, not a continuation of that historical archive.
