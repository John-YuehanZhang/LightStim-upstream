<!-- PLACEHOLDER (2026-09-29): drafted by the assistant from failures seen in this project; to be rewritten by the operator. -->

Known ways a construction looks correct and is not. Check every candidate
against each item.

1. Hook errors parallel to a logical operator. In a weight-4 check, a fault on
   the ancilla after the second two-qubit gate spreads to a weight-2 data error
   on the last two data qubits. If that pair is parallel to a logical operator
   of the same type, the circuit-level distance drops, typically to (d+1)/2.
   The CNOT order inside each check must be chosen with this in mind.
2. Default syndrome-extraction blocks. A generic block (for example an
   edge-colouring schedule) may ignore hook directions. On the rotated surface
   code LightStim's `MemoryExperiment` without an explicit
   `extraction_block_class` gives circuit distance d-2 for d >= 5. Always name
   the block and verify the circuit distance; never trust a default.
3. Missing detectors after a gate. If the stabilizer tracker silently projects
   the state, the operation can end up with no detectors across it, which
   hides errors. Check that detectors exist that compare syndromes before and
   after the operation.
4. Flows that hold only without sign. A logical action that is correct up to a
   Pauli (for example the result is Y·H instead of H) passes an unsigned check.
   Declare signed flows; the gate checks signs.
5. Upper bounds reported as distances. Search heuristics and BP-OSD sampling
   find some low-weight logical error, not the lightest; they can overestimate
   the distance by a large factor. Only exact distances count.
6. SWAP noise in fold-type gates. Physical SWAP layers used to realise a fold
   are noisy two-qubit operations; with circuit-level noise a fold gate built
   from SWAPs can lose distance (for H on the rotated surface code with three
   SWAP layers the worst-case distance is (d+1)/2).
7. Operation without surrounding extraction rounds. A fault-tolerance claim
   needs syndrome extraction before and after the operation (typically d
   rounds); an operation checked in isolation hides time-like failures.
8. Logical operators that are stabilizers or belong to the wrong block. The
   operators named in the flows must be non-trivial logical operators of the
   stated code, and for multi-block operations each must act on the intended
   block.
9. Hyperedges and decoders. Transversal and fold gates produce hyperedges in
   the detector error model; graph-matching decoders silently decompose them.
   This matters for later logical error rate evaluation, not for the distance.
10. Parameters that are not what they seem. k must be computed (n minus the
   rank of the checks), not read from a construction's name; equivalent codes
   under qubit relabelling are the same code.
