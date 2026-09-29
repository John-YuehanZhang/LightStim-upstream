<!-- PLACEHOLDER (2026-09-29, v2.2): check items only, no results; to be rewritten by the operator. Rule: every item says what to check and how, never what the check will find. -->

Checks to run on every candidate before you rely on it or submit it. Each item
says what to check, not what you will find.

1. Hook errors. For every multi-qubit check, work out which data errors a
   single fault on the ancilla part-way through the check spreads to, and
   compare their direction with the logical operators of the same type. Choose
   the order of the two-qubit gates with this in mind and confirm with the
   exact circuit distance.
2. Existing building blocks. Do not assume that any existing syndrome-extraction
   block, schedule or protocol class is fault-tolerant for your circuit. Name
   the block you use and compute the exact circuit distance yourself.
3. Detectors across the operation. Check that there are detectors that compare
   the syndrome before the operation with the syndrome after it, so that
   errors during the operation are detected.
4. Signs. Check every flow with its sign; a construction that is correct only
   up to a Pauli is not the stated operation.
5. Exact distances only. A distance from a search heuristic or from sampling is
   an upper bound. Only an exact computation (the gate's or
   `circuit_distance_fast`) counts as a distance.
6. Every physical operation is noisy. Qubit moves, swaps and resets inside an
   operation are noisy operations; include them in the circuit and in the
   distance computation.
7. Rounds around the operation. Include syndrome extraction before and after
   the operation, as many rounds as the task requires, so that time-like
   failures are covered.
8. Logical operators. Check that every operator named in a flow is a
   non-trivial logical operator of the stated code on the intended block, not
   a stabilizer and not an operator of another block.
9. Layout. Check that every block ends on the same data qubits in the same code
   as it started (for example, a patch that is rotated or moved by the
   operation must be brought back).
10. Parameters. Compute n, k and d; do not read them from a construction's name.
    Treat codes that differ only by a qubit relabelling as the same code.
