# agent-for-QEC pipeline

An autonomous exploration loop in which a coding agent (Claude Code, headless)
uses LightStim as its instrument to construct quantum error-correcting codes and,
for each code, fault-tolerant implementations of logical operations. The loop
was started 2026-09-28. Everything the agent claims must pass the verification
stack below; the deliverable is the ledger table, not a metric.

## What counts (project rules)

* No objective function. This is a feasibility search with a novelty audit.
* A candidate is a pair (code, logical operation implementation). Both distances
  must be verified: the stabilizer-code distance `d` (exact) and the circuit-level
  distance of the logical-operation circuit (exact). The operation is
  fault-tolerant iff the circuit-level distance equals `d`.
* The logical action must be verified with signs (stim stabilizer flows).
* Logical error rate is evaluation only, done last for ledger rows, never used
  to steer the search.
* A new code must ship with a generating set of logical operations (all logical
  Clifford generators fault-tolerantly, plus one non-Clifford route).
* Hardware platform is analysed after the fact (theory, per candidate), it is a
  ledger column, not a search constraint.
* Novelty is audited after a candidate is found and again before the table is
  frozen; rediscoveries and failures stay in the ledger as their own row classes.

## Layout

```
agent_for_qec/
  README.md            this file (architecture)
  PROGRESS.md          agent state machine: rules, done phases, next phase
  LEDGER.md            the result table (three row classes: new / rediscovered / failed)
  prompts/phase_prompt.md   the fixed prompt every headless run receives
  runner/select_account.py  picks the account with quota for the chosen model
  runner/run_phase.sh       one phase = one `claude -p` process on one account
  runner/loop.sh            phases back to back, re-selecting the account between phases
  tools/verify_stack.py     code distance / circuit distance / signed flows / sanity / platform
```

Runtime state that must not enter git (tokens, logs, usage accounting) lives in
`/nvme2n1/yuehan_zhang/agent_for_qec_runner/` and `/nvme2n1/yuehan_zhang/.secrets/`.

## The loop

```
select_account.py --model M      probe accounts, choose least-used one with quota for M
run_phase.sh                     cd repo (branch agent-for-QEC); claude -p <phase_prompt> --model M
   agent: read PROGRESS.md -> do exactly the "next phase" -> verify with tools/ ->
          update LEDGER.md + PROGRESS.md -> git commit (local) -> exit
run_phase.sh                     parse result JSON, append usage_log, mark account if limit hit
loop.sh                          repeat; a run killed by a quota limit is resumed by the
                                 next run from PROGRESS.md + git status
```

Permissions of the headless agent are an explicit allowlist (`run_phase.sh`):
file edits inside the repo are auto-accepted; shell use is limited to the
project python environment, local git (no push), and read-only inspection.
There is no blanket permission bypass.

Account switching therefore never interrupts a phase: a Claude Code process
reads its token once at start-up, so the only switch point is between phases.
Inside a phase the agent may not fan out (at most two concurrent subagents, no
workflow tool) because each account has a 5-hour session window.

## Verification stack (`tools/verify_stack.py`)

| check | method | exact? |
|---|---|---|
| code distance | z3 SAT: min weight of a Pauli that commutes with all stabilizers and anticommutes with some logical (witness = lightest logical representative, then UNSAT at weight−1) | yes |
| circuit-level distance | stim `search_for_undetectable_logical_errors` gives a witness; z3 proves no lighter set of DEM error mechanisms flips an observable undetected | yes (timeout ⇒ proven lower bound only) |
| logical action | `stim.Circuit.has_flow` with signs on the noiseless circuit; unsigned result reported alongside to diagnose Pauli-frame errors | yes |
| noiseless sanity | zero detection events and zero observable flips at p = 0 | yes |
| platform report | two-qubit gate range/degree statistics from QUBIT_COORDS → verdict per platform (superconducting 2D, neutral atom, trapped ion) | heuristic |

Calibration on the rotated surface code (memory, circuit-level depolarising
noise, all rates 1e-3, `rounds = d`): code distance exact 3/5/7 for d = 3/5/7.
Circuit-level distance depends on the syndrome-extraction block:
`MemoryExperiment` without an explicit `extraction_block_class` uses the
generic edge-colouring block (`GenericCSSColorationExtractionBlock`), which
ignores hook-error direction and gives 3/3/5; the rotated block with the
`perpendicular` schedule gives full distance (5 at d = 5, proven by MILP in
18 s). Lesson for the agent: always name the SE block explicitly and verify
circuit-level distance; never trust a default. Full table: `phase0/schedule_calibration.out`.

Exactness of the circuit-level check: the MILP (HiGHS) proves optimality in
seconds for d ≤ 5 (1.7k mechanisms); the z3 SAT route could not prove the
weight-4 UNSAT for d = 5 within 10 minutes and is kept only as a fallback.

Phase 1a added `tools/circuit_distance_fast.py` (use it first; `verify_stack.circuit_distance`
remains the reference). How it works:
* **Lower bound from a relaxation.** Keep only a subset S of detectors, e.g. the
  detectors flipped by X errors on data qubits, which are the Z-type half of a CSS circuit.
  Merge mechanisms that become identical and drop those that become empty. Every
  logical error of the full problem is also a solution of this smaller problem,
  so the smaller problem's minimum is a proven lower bound.
* **Solving the smaller problem.** If every projected mechanism touches at most
  2 detectors, the problem is graph-like and a parity-lifted BFS solves it exactly.
  Otherwise it goes to a MILP.
* **Upper bound.** The optimum of the smaller problem is mapped back to real
  mechanisms: from each merged class, take the member with the fewest detectors
  outside S. That set is re-checked against the full H and L. If it passes, it is
  an upper bound equal to the lower bound, so the distance is exact.
* **Per-observable bounds.** Bounds are taken separately for each observable, and
  the overall result is the minimum over observables.

Measured results (`phase1/solver_benchmark.out`):
* **Rotated-surface-code memory with the perpendicular schedule.** Exact d for
  d = 5…15 in both bases. At d = 7 it takes 0.2 s where the old MILP timed out
  after 1500 s; at d = 15 (66k mechanisms) it takes 18 s.
* **Transversal CNOT at d = 5.** Exact 5 in 4–7 s, against 510–1270 s for the full MILP.
* **Agreement with Phase 0.** It matches all 18 exact configurations from Phase 0,
  including the non-fault-tolerant ones (2, 3, (d+1)/2).
* **Full-problem MILP feasibility, "any logical error of weight ≤ 6?"** Tried on
  d = 7 memory; it timed out at 1800 s.
* **Hyperedge problems stay hard.** The smaller problem is not graph-like when a
  single fault touches more than 2 detectors of S:
  * weight-6 BB checks;
  * a transversal-CNOT fault that reaches both patches.

  For those it falls back to the MILP, and a timeout is reported as the proven
  interval [lb, ub], never as an exact value. Examples:
  * BB [[72,12,6]] memory: only ub = 6 in 1800 s.
  * 4D det5 memory: exact in 24 s.
* **Known defect in `verify_stack.min_weight_vector_milp`.** On timeout it can
  overstate the lower bound: rows that were skipped or have an unknown bound are
  ignored, and statuses other than "infeasible" are treated as infeasible. The
  fast tool uses its own `_milp_min` for this reason. Do not use
  `verify_stack.circuit_distance`'s `proven_lower_bound` when its status is
  `timeout`; its `exact` results are unaffected.

## Environment

```
cd /nvme2n1/yuehan_zhang/LightStim-upstream        # branch agent-for-QEC
PYTHONPATH=. /home/yuehan/miniconda3/envs/light_stim/bin/python ...
PYTHONPATH=. /home/yuehan/miniconda3/envs/light_stim/bin/python -m pytest tests/ -m smoke -q
```
