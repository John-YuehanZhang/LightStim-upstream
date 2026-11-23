# BB In-Module Pauli Measurement API

**Modules:** `lightstim.protocols.bb_ppm`, `lightstim.qec_code.BB_code.ppm`

This protocol implements pure in-module Pauli product measurements (PPMs) in the
`[[144, 12, 12]]` gross code. It uses the physical logical representatives from
[Tour de gross](https://arxiv.org/abs/2506.03094), the shift construction, and the
logical processing unit (LPU) measurement construction. Detector generation,
logical observable export, and noise injection use the existing LightStim
infrastructure. The benchmark uses Stim sampling and the upstream Relay-BP
detailed decoder API to retain independent convergence diagnostics.

The demonstration is `notebooks/LogicalOps/bb_ppm.ipynb`. After a setup cell,
its five numbered cells export the 15 basic constructions, inspect 36 shifts
and 540 native measurements, inspect 245 rotation axes, display a selected
compilation path, and display recorded LER results or run that exact plan through
the BB PPM benchmark.
The default target is `X2 Z7 X12`: one rotation transforms it to `+Y7`, with
direct central measurement `Y7` and 13 logical operation records overall.
The runnable benchmark is `benchmarks/logical_ops/run_bb_ppm.py`; its result
reader and plotter are in `benchmarks/logical_ops/plot_bb_ppm.py`. The notebook
uses those same functions and does not contain a second sampling implementation.

## Scope and logical indices

Logical indices are **one-based, 1 through 12**, in the paper-specific basis.
Use `build_tour_de_gross_code()`; the pre-existing BB memory-code preset has a
different logical basis and cannot be substituted without recompiling the
logical action of every shift.

The 15 basic LPU measurements act on logical qubits 1 and 7. Their shift
conjugates form the native measurement set of Eq. (14), which may involve other
logical qubits. For general synthesis, logical
qubit 1 is a disposable pivot and qubits 2 through 12 hold data. The compiler
rejects non-native targets involving an unknown pivot input; it does not claim
to measure arbitrary products on twelve unknown data qubits with these resources.

Supported experiment inputs are PPM sequences, including noncommuting sequences.
The current protocol does not implement T gates, inter-module operations, atom
loss, or arbitrary independent logical state preparation.

## `BBPPMStep`

```python
from lightstim.protocols import BBPPMStep

step = BBPPMStep(targets=((2, "X"), (8, "X"), (9, "X")))
```

`targets` is a sequence of `(logical_index, Pauli_letter)` pairs. Each logical
index may occur once. Letters are normalized to uppercase; identity factors
are removed. An empty or all-identity request is rejected. This request means
one joint measurement of `X2 X8 X9`, not three independent measurements.

## `BBPPMExperiment`

```python
BBPPMExperiment(
    code,
    ppm_sequence,
    *,
    plans=None,
    initial_states="Z",
    final_measure_states=None,
    rounds=10,
    rounds_init=1,
    rounds_final=1,
    idle_rounds=0,
    noiseless_boundaries=True,
    noise_params=None,
    noise_model="circuit_level_with_idling",
    logical_tracking="slabs",
)
```

| Argument | Meaning |
|---|---|
| `code` | `build_tour_de_gross_code()` result, or `None` to construct it. |
| `ppm_sequence` | Sequence of `BBPPMStep` objects, executed in order. |
| `plans` | Optional existing `BBPPMPlan` objects, one per requested PPM. The same objects are executed without invoking the compiler. Targets, measurement keys, and record references are validated. |
| `initial_states` | Uniform physical-data preparation basis, `"X"` or `"Z"`. |
| `final_measure_states` | Uniform physical-data readout basis; defaults to the initial basis. |
| `rounds` | Merged syndrome-extraction rounds for **each native LPU measurement**, including those used inside rotations. |
| `rounds_init`, `rounds_final` | Standalone syndrome-extraction rounds before/after the sequence; each at least one. |
| `idle_rounds` | Extra standalone syndrome-extraction rounds before each requested PPM. |
| `noiseless_boundaries` | Default `True`: prepare an error-free codestate and use a noise-free final QEC/readout boundary, as in paper Appendix A.7. Set `False` for noisy boundaries. |
| `noise_params` | Existing `NoiseConfig`, or `None` for a clean circuit. |
| `noise_model` | Existing noise-injector model name. |
| `logical_tracking` | Currently requires the record-aware `"slabs"` tracker. |

`initial_states` and `final_measure_states` are not dictionaries assigning
arbitrary states/bases to twelve independent logical qubits. Uniform physical
CSS preparation/readout is the supported experiment boundary.

```python
from lightstim.protocols import BBPPMExperiment, BBPPMStep
from lightstim.qec_code.BB_code.ppm.shift import build_tour_de_gross_code

experiment = BBPPMExperiment(
    build_tour_de_gross_code(),
    [BBPPMStep(((2, "X"),))],
    initial_states="X",
    final_measure_states="X",
    rounds=10,
)
clean = experiment.build()
```

- `build()` rebuilds the physical experiment and returns a `stim.Circuit`.
- `plans` holds one logical lowering plan per requested PPM.
- `ppm_outcomes` is populated during `build()`, keyed by step index.
- `builder`, `tracker`, and `system` expose the constructed LightStim objects.
- `build_noisy_circuit(noise_params, noise_model=None)` applies an existing
  LightStim noise model to the most recently built schedule.
- `noise_schedule` returns a copy of that schedule, including identity markers
  used only to preserve idle-noise placement. External noise injection must use
  this schedule instead of the marker-free return from `build()`.

For a noise sweep on one compiled experiment:

```python
from lightstim.noise.config import NoiseConfig

noisy = experiment.build_noisy_circuit(NoiseConfig(
    p_1q=1e-3, p_2q=1e-3, p_meas=1e-3, p_reset=1e-3, p_idle=1e-3,
))
```

The tracker follows the uncorrected physical state. `ppm_outcomes` already
includes classical frame corrections. Raw final data readouts or intermediate
state expectations must likewise be interpreted in that frame; physical state
vectors need not equal those from a circuit with explicit feedback gates.

After the initial extraction, a classical Pauli frame puts the known random
syndrome signs into the +1 code sector while preserving the prepared logical
eigenvalues. Conditional Pauli corrections update this frame; they do not add
physical correction gates. This preparation frame is necessary: the
paper's shift maps are equalities modulo stabilizers and would otherwise
carry syndrome-dependent signs. Subsequent noisy rounds are not idealized or
corrected by an extra oracle decoder.

The protocol registers the LPU, changes active checks, measures the merged
checks, reads out the LPU edge qubits, and updates the resulting Pauli frame.
The physical lowering lives in `lightstim/qec_code/BB_code/ppm/`; the protocol
module is its public import facade. It uses the existing builder and tracker
without changes. Complete extraction rounds keep check records local in time.
For Bell readouts, the tracker uses the joint parity and the independent random
bit, then their record coordinates are translated back to the physical readout.
Shift measurements retain the state needed by subsequent extraction rounds.
`DETECTOR` and `OBSERVABLE_INCLUDE` are generated automatically by LightStim;
the BB layer only translates the coordinates of those existing record parities.

## Measurement results versus LER observables

```python
BBPPMOutcome(step, targets, records, constant=False)
```

`records` contains absolute Stim measurement-record indices. XOR their sampled
bits with `constant` to recover the protocol result: bit 0 means eigenvalue +1,
and bit 1 means eigenvalue -1. The helper accepts a single sample or a sample
batch:

```python
raw = clean.compile_sampler(seed=1).sample(32)
result_bits = experiment.ppm_outcomes[0].evaluate(raw)
```

A random PPM result of -1 is not a logical failure. LER compares decoder predictions with the circuit's automatic
observable flips. With default settings, any incorrectly predicted observable
counts that shot as one failure. There is no postselection in this demo.

## Logical compiler

```python
from lightstim.qec_code.BB_code.ppm.lowering import BBPPMCompiler
from lightstim.qec_code.BB_code.ppm.shift import logical_shift_tableaus

compiler = BBPPMCompiler(logical_shift_tableaus())
plan = compiler.compile(((2, "X"), (8, "X"), (9, "X")), prefix="ppm0")
```

`shift_maps[(x, y)]` is a `stim.Tableau` representing `W P W†`, derived from the
physical representatives. The compiler checks identity/inverse availability
and deduplicates equal logical shifts. Native measurements follow Eq. (14).
General synthesis searches the transvection graph generated by the valid
Eq. (15) rotation axes; this bidirectional search is an implementation choice,
not a claim that the paper specifies this exact search algorithm.

`BBPPMPlan.operations` is in **time order**:

| Operation | Semantics |
|---|---|
| `Shift(x, y)` | Apply the selected physical shift automorphism. |
| `Measure(p1, p7, key)` | Run one native LPU measurement and assign its corrected result to `key`. |
| `PauliFrameUpdate(pauli, parity)` | Apply or track the indicated logical Pauli conditioned on a record parity. |

`RecordParity(keys, constant)` denotes XOR, including cancellation of repeated
keys. A consumer must implement the frame updates; ignoring them changes the
operation. The protocol propagates a classical Pauli frame through its physical
Clifford circuit and converts returned PPM results to raw measurement parities.

For the Eq. (16) rotation with middle measurement `X_a R`, the compiler measures
`Z_a`, `X_a R`, and `Y_a`, with result bits `a, m, s`. To implement
`exp(+i pi R/4)`, it applies/tracks `R` when `a XOR m XOR s XOR 1` is one. The
opposite rotation uses `a XOR m XOR s`. The analogous cyclic choices implement
middle measurements `Y_a R` and `Z_a R`. All three measurement bits are retained.

The pre-measurement rotations transform the requested data Pauli to a native
data axis `R`. If `R` itself is in the native measurement catalogue, the middle
measures it directly, including any required shift conjugation. Otherwise the
middle uses a prepared pivot and native `Q_a R`; its result then includes the
pivot-preparation bit. Both branches include the signed endpoint and native
measurement signs in `plan.output`. The rotations are then undone in reverse
time order, including their Pauli corrections. `plan.base_measurement` names
the actual central measurement, either `R` or `Q_a R`. This shortcut does not
change the search path or the ancilla needed to implement its rotations.

`plan.path` contains `PauliPathStep(before, axis, after, direction)` records
produced during the compiler's actual signed conjugations. `before` and
`after` retain the sign and all twelve Pauli letters. This is the selected
path, not a transcript of BFS expansions. Cached search fronts can select a
different valid path on later requests, so the teaching example uses a fresh
compiler. Negative requested targets retain their sign in `plan.target`.

Pass the displayed plans directly to the physical experiment:

```python
sequence = [BBPPMStep(((2, "X"), (7, "Z"), (12, "X")))]
compiler = BBPPMCompiler(logical_shift_tableaus())
plans = tuple(compiler.compile(step.targets, prefix=f"ppm{i}")
              for i, step in enumerate(sequence))
experiment = BBPPMExperiment(None, sequence, plans=plans,
                             initial_states="X", rounds=10)
assert all(a is b for a, b in zip(experiment.plans, plans))
clean = experiment.build()
```

The mixed target has a random ideal result under uniform X initialization;
neither outcome alone is a failure. Noiseless detector/observable checks and
the existing pipeline's failure accounting remain unchanged.

### Rotation-set counting

The Eq. (16) gadget requires **nonidentity pivot support** in `Q_a R`. Removing
the pivot factor from every native Pauli, including those with `Q=I`, is not a
valid way to construct this gadget set. The authors' compiler also filters
`has_pivot_support()` when constructing its search generators, although its
rotation-count test erases the pivot without that filter:

- [Actual search generator construction](https://github.com/qiskit-community/bicycle-architecture-compiler/blob/main/crates/bicycle_cliffords/src/decomposition.rs)
- [Unfiltered rotation-count test](https://github.com/qiskit-community/bicycle-architecture-compiler/blob/main/crates/bicycle_cliffords/src/measurement.rs)

The current physical-basis table gives 540 distinct native measurements.
Of these, 256 have nonidentity pivot support, three have identity data support,
and therefore 253 are valid nontrivial `Q_a R` candidates. Deduplicating their
data parts gives 245 distinct nontrivial rotation axes. The exported catalogue
retains all 253 witnesses and identifies the compiler-selected witness per axis.
These axes
are sufficient: tests construct every data-qubit X/Z quarter-turn and a
connected chain of ZZ quarter-turns, which generate the full data Clifford
group. No synthetic multiqubit measurement is assumed to implement an axis.

## Construction and compilation reports

The notebook writes generated artifacts under
`benchmarks/logical_ops/results/bb_ppm_reports/`. The library exposes:

```python
from lightstim.qec_code.BB_code.ppm.native_reporting import export_basic_measurements
from lightstim.qec_code.BB_code.ppm.reporting import (
    export_shift_and_native_catalogs, export_rotation_catalog, export_plan_report,
)

basic_rows = export_basic_measurements(root / "stabilizer_definition")
catalogs = export_shift_and_native_catalogs(compiler, code, root)
rotations = export_rotation_catalog(compiler, root)
report = export_plan_report(compiler, plans, root)
```

Basic-measurement exports contain 15 JSON/TXT stabilizer definitions and a
manifest. They preserve the signed target, all 144 original and deformed BB
checks, signed vertex/cycle checks, physical data IDs and site labels, and
edge-qubit labels. They omit readout instructions, Bell preparation, gauge-out
feedback, gate scheduling, and raw circuit components. Re-exporting removes
only known legacy circuit filenames registered in the previous manifest;
unrelated files are retained. Use `BBPPMExperiment` to construct the complete
tracked experiment.

The 144 listed original BB checks are redundant: the 72 X checks and 72 Z
checks each have GF(2) rank 66. The report computes these ranks from the
original check operators, giving 132 independent stabilizers and
144 - 132 = 12 encoded logical qubits. These ranks describe the original BB
code, not the enlarged system during a measurement. All 144 check definitions
are retained. Export validation checks pairwise commutation and the signed
product of the vertex checks; it does not certify fault distance.

Other exporters return tables in `rows` and absolute filenames in `paths`.
JSON retains the full structure; CSV provides readable catalogues and the
time-ordered operation list. Reports inspect supplied plans without searching.
They distinguish the data base `R`, the actual `central_measurement`, and its
basic `P0`/shift implementation. `central_method` is `direct` when measuring
`R` itself and `prepared_pivot` when preparing `Q_a` then measuring `Q_a R`;
`native_joint` records only the latter. The notebook shows the selected path
and expanded instructions without a separate, duplicate rotation-witness table.

`BBPPMPlan.search_trace` records initialization and each completed layer of the
actual bidirectional search. Each record includes the expanded side, newly
discovered and cumulative distinct nodes on each side, the remaining frontier
sizes, and the first selected meeting Pauli. The smaller frontier is expanded
each round; the two sides do not necessarily expand alternately. The native
side starts from the 245 data axes, and may reuse its expanded frontier across
targets. A cached target retains its original trace with `search_status="cached"`;
direct native measurements and identity plans have no search rounds.
`export_plan_report` returns these records in `rows["search"]` and saves
`search_progress.csv` and the trace in `plans.json`. Search diagnostics are
excluded from the notebook's executable-plan hash, so recording a search does
not change the identity of its operations.

## Sampling, decoding, and plotting

The notebook runs a five-shot example at each of three noise points. These
small counts demonstrate execution and plotting; they do not estimate rare
logical failures accurately. Select a new run directory for a new experiment
and call the same benchmark function used by the CLI:

```python
from benchmarks.logical_ops.bb_ppm_runner import run_campaign
from benchmarks.logical_ops.plot_bb_ppm import display_results

run_campaign(
    sequence=ppm_sequence, plans=plans,
    p_values=(1e-4, 5e-4, 1e-3), shots=(5, 5, 5),
    workers=5, run_dir="benchmarks/logical_ops/results/my_bb_ppm",
    output="benchmarks/logical_ops/results/my_bb_ppm/results.csv",
)
display_results("benchmarks/logical_ops/results/my_bb_ppm/results.csv")
```

The experiment reuses supplied plans, constructs a clean circuit through
`BBPPMExperiment`, then injects the original `circuit_level_with_idling` noise
with all five physical rates equal to p. Noiseless initial/final boundaries
retain their existing exclusions. Temporary noiseless identity markers preserve
the original feedback slots' idle-noise exclusions; noise injection consumes
this schedule metadata and the returned physical circuit omits the markers.
The strict detector error model is not decomposed into graphlike faults.

The fixed-shot benchmark uses the upstream Relay-BP 0.2.2 detailed API with a
fresh decoder per shot and one thread per worker. Its default parameters are
`alpha=0.8`, `gamma0=0.5`, `pre_iter=320`, `num_sets=600`, `set_max_iter=60`,
`gamma_dist_interval=(-0.1, 0.5)`, `stop_nconv=5`,
`stopping_criterion="nconv"`, `logging=False`, and `seed=0`. These parameters
were tuned on full-circuit training samples and frozen before independent
validation; they are not the original paper's parameter profile.

LER counts any mismatch between predicted and sampled automatic Observables.
Syndrome consistency is independently checked using the returned correction
and detector matrix. A nonconverged shot whose Observables match contributes
to the nonconvergence count but not to the logical-failure count. No samples
are discarded or replaced. This benchmark-specific detailed reporting does
not change the generic `SimulationPipeline` or registered Relay-BP adapter.

Each run retains sampled inputs, their hashes, configuration and source
identity, and per-shot decoding checkpoints. A changed configuration cannot
resume an existing run. Completed noise points are published to a CSV with a
hash-bound manifest. The plotter validates the data and computes exact 95%
Clopper–Pearson intervals; zero observed errors have a nonzero upper bound.

The current unchanged-core example stores five shots at each of three noise
points in `benchmarks/logical_ops/reference/bb_ppm/`, together with its provenance
manifest. These small cohorts demonstrate execution and plotting; they do not
establish a precise low logical error rate. Earlier large-sample results remain
in repository history and local archives and are not pooled into the example.
Raw samples and checkpoints remain local rather than being bundled with the
demonstration. New runs use the documented benchmark seed policy.

See the [benchmark guide](../../benchmarks/logical_ops/README_bb_ppm.md) for
CLI configuration, checkpoint behavior, CSV fields, and plotting commands.

## Validation and limits of comparison with the paper

Logical tests check all 540 native measurement projectors using entangled
reference states, the signed rotation gadget on every outcome branch, random
multiqubit targets, noncommuting measurement sequences, and the order of inverse
rotations. Physical construction tests check stabilizer commutation, the target
product, gate collisions, and the actual measurement flows. The benchmark checks
that clean circuits have silent detector and observable samples before sampling.

The simulation is a **baseline for this implementation**, not an exact numerical
reproduction of the paper:

- The physical scheduler satisfies the implemented checks but is not the paper's
  optimized 12-timestep merged-round schedule; inspect actual layers/ticks.
- The built-in LightStim depolarizing/readout channels are not identical to the
  paper's independent Pauli-fault probability expansion.
- The notebook displays tuned full-circuit Relay-BP parameters. Neither these
  settings nor the physical scheduling, noise channels, and failure event
  establish numerical equivalence with the paper.
- The demo compares whole PPM sequences with uniform logical preparation and
  automatically exported observables. Initialization and final QEC/readout
  boundaries are noiseless by default, following Appendix A.7. It does not
  claim that its full-experiment failure event matches the paper's
  isolated-instruction failure event; both use shots as their denominator.
- Clean-circuit tests establish consistency; they do not by themselves certify
  the paper's circuit distance 10, an error threshold, or the low LER reported
  in Table 2. The static gross code's distance 12 is a separate property.

## Running the demonstration or benchmark

See the [benchmark guide](../../benchmarks/logical_ops/README_bb_ppm.md) for
environment setup, the five-shot notebook demonstration, and new runs with
configurable worker counts and checkpoint recovery.
