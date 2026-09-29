# Task calib_rotated — calibration on the rotated surface code

Purpose: test the pipeline itself on objects whose answers are known. Every
result of this task is expected to be known in the literature; that is the
point. A worker must still construct and verify everything itself.

Code: the rotated surface code [[d^2, 1, d]] (LightStim:
`lightstim.qec_code.surface_code.rotated`). Distances d = 3 and d = 5.
Noise: circuit-level depolarising, all rates 1e-3. Syndrome extraction: d
rounds before and d rounds after any logical operation.

Items (each resolved by gate-accepted facts, or by a recorded negative result
explaining the mechanism):

1. Memory. Z-basis and X-basis memory experiments (d rounds) whose exact
   circuit-level distance equals d, for d = 3 and d = 5. This requires
   choosing a syndrome-extraction schedule; do not assume any block's default
   is fault-tolerant.
2. Transversal CNOT between two patches, with signed flows for all four
   logical Pauli generators and exact circuit-level distance equal to d, for
   d = 3 and d = 5.
3. Logical Hadamard on one patch by any mechanism you choose, with signed
   flows (X_L -> Z_L and Z_L -> X_L, up to the correct sign) and the exact
   circuit-level distance reported truthfully, for d = 3 and d = 5. If your
   construction does not reach distance d, record the lightest undetected
   error and the mechanism as an obstacle, and try a different mechanism.
4. A logical Z⊗Z measurement between two patches by lattice surgery (d = 3),
   with the exact circuit-level distance reported truthfully.

Completion criterion: every item has either an accepted fact that meets it, or
a recorded `dead_end`/`obstacle` after at least two materially different
mechanisms were tried. The main agent declares `done` citing fact ids.
