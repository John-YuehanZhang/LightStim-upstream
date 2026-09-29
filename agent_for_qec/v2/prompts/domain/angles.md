<!-- PLACEHOLDER (2026-09-29, v2.2): families of mechanisms only, no results; to be rewritten by the operator. -->

Families of mechanisms for implementing logical operations fault-tolerantly
and for constructing codes. Treat them as starting points, not an exhaustive
list; combining families is often where new constructions come from.

1. Transversal and fold-type gates
   - transversal gates between blocks of the same code (CNOT, sometimes CZ)
   - fold-transversal gates using a ZX-duality or self-duality of the code
     (physical H or S layers combined with qubit swaps along the fold)
   - code automorphisms: a qubit permutation that maps the stabilizer group to
     itself acts as a logical Clifford; a pure relabelling costs no physical gates
   - permutation plus a SWAP network, ZX-duality plus local Cliffords
2. Measurement-based operations
   - lattice surgery and generalised surgery (an auxiliary system gauges a
     logical operator; merge, measure, split)
   - logical Pauli product measurements followed by teleportation
   - homomorphic measurement / homomorphic CNOT onto an ancilla code
   - punctured or extended ancilla codes, extractor systems
3. Code deformation
   - moving or reshaping boundaries, twists and domain walls
   - growing and shrinking ancilla patches; stretching or folding a patch
4. State injection and teleportation
   - magic-state injection and gate teleportation for non-Clifford gates
   - resource states such as |Y> used as proxies
   - teleporting into a code where the target gate is easy, then back
5. Circuit and schedule level
   - order of the two-qubit gates inside a syndrome-extraction round (hook
     errors), flag qubits, interleaving the operation with extraction rounds,
     number of rounds before and after the operation
6. Code construction
   - product constructions: hypergraph product, lifted product, balanced product
   - group-algebra constructions: bivariate bicycle and generalised bicycle codes
   - puncturing, shortening or extending known codes; subsystem codes
   - designing a code around a desired automorphism or duality so that the
     logical gates you want exist by construction
