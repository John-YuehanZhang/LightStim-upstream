# Task explore_v1 — open exploration of codes, logical operations and syndrome extraction

Find constructions that have not been done before, at any of three layers.
The planner opens one project per topic; each project has one object of study.

Layer 1 — code construction. A stabilizer code together with a complete set of
logical operations that includes a universal gate set. Every operation must be
fault-tolerant at circuit level (exact circuit distance = code distance d).
Non-Clifford gates may consume magic states, which are given as a resource and
need not be prepared or verified.

Layer 2 — logical operations on an existing code. An implementation of a
logical operation (gate, measurement, state preparation, code switching) on a
known code, fault-tolerant at circuit level, by a mechanism not used before for
that operation on that code.

Layer 3 — syndrome extraction for an existing code and operation. A new
extraction circuit that either reaches circuit distance d where the previous
methods you compare against do not, or reaches d with fewer ancilla qubits.
State the method you compare against (`compared_to` in the submission).

Rules for all layers:
- Noise: the gate's standard circuit-level model at the project's rate p.
- Verify at small distances; a code family needs at least two instances
  (e.g. d = 3 and d = 5, or its two smallest members). State the instances.
- Logical operations leave every block on the same data qubits with the same
  stabilizer group as before (the gate checks this).
- Negative results count: a mechanism that fails, with the lightest undetected
  error and the reason, is recorded as a dead end or obstacle.
- Record every submission's raw quantities truthfully (rounds before, during
  and after the operation); no score is optimised.
- Facts of other projects may be used through the shared library
  (`external_depends_on`).

Completion of a topic: its own TASK.md says when it is done. The overall task
has no completion criterion; the run continues while the operator lets it.
