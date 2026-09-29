### Submission format

A submission is a self-contained directory: `submission.json`, `build.py`, and
any other Python modules `build.py` imports. The gate copies the directory,
hashes all of it, and runs `build.py` in a fresh interpreter inside a sandbox
that sees only the repository (read-only), the copied directory and no network.
Nothing else from your working directory is available there: copy every module
you need into the submission directory. Change anything and it is a new
submission; an identical directory is recognised as a duplicate.

`submission.json` (these fields and no others):

    {
      "title": "short name of the construction",
      "kind": "code" | "memory" | "logical_gate" | "logical_measurement" | "resource_gate",
      "description": "the intended logical action in words, e.g. 'CNOT_L from block A to block B'",
      "code": {"n": 9, "k": 1, "d": 3},                 # your claims about the code
      "circuits": [
        {"name": "cnot_ZZ", "claimed_circuit_distance": 3, "claim_type": "exact"}
      ],
      "depends_on": ["<fact id, at least 8 hex characters>", ...],
      "layer": 1 | 2 | 3,                               # if the task defines layers
      "compared_to": "the existing method this improves on, if the claim is a comparison"
    }

Circuit entries accept only `name`, `claimed_circuit_distance`, `claim_type`
(`"exact"`, the default, or `"at_least"`) and, for kind `resource_gate`,
`resource_blocks` (see below).

`build.py` defines `build()` returning:

    {
      "code": {"patch": <LightStim code patch>}       # or {"Hx": [[..]], "Hz": [[..]]}
                                                      # or {"stabilizers": ["XXZI..", ...]}
      "circuits": {
        "cnot_ZZ": {
          "circuit": <stim.Circuit with DETECTOR and OBSERVABLE_INCLUDE>,
          "blocks": [[q, q, ...], [q, q, ...]],       # one list per code block: the global
                                                      # indices of its n data qubits, in the
                                                      # code's qubit order
          "flows": ["X1*X7*X13 -> X1*X7*X13*X18*X24*X30", ...]   # stim flow strings, WITH sign
        }
      }
    }

What the gate does with it:

- Code: the distance is computed from the stabilizers alone; logical
  operators are derived by the gate, never taken from you.
- Noise: the gate removes every noise instruction from your circuit and
  injects its own standard circuit-level model (depolarising noise after every
  gate, flips on every reset and measurement, idle noise in every TICK moment,
  all at the project's rate p). Build a correct circuit with or without noise;
  TICKs matter because they define idle moments.
- Flows: the gate derives the logical segment itself (your circuit without the
  first preparation and the final readout of every data qubit in `blocks`) and
  checks every flow on it with sign. Every Pauli in a flow must be a logical
  operator of the declared code on the declared blocks, and no flow may act on
  qubits outside the blocks. For `memory` and `logical_gate`, the flow inputs
  and the flow outputs must each generate all 2k logical Paulis of every block;
  for `logical_measurement`, at least one flow must map a logical operator to
  measurement records (`... -> rec[-1] xor ...`).
- Layout: every block must start in the code (its stabilizers are measured at
  the start of the segment) and end in the code on the same data qubits (its
  stabilizers hold at the end). An operation must leave each block where and
  as it found it; for example, a logical gate that rotates or moves a patch
  must bring it back.
- Given resources (kind `resource_gate`): a block listed in the circuit's
  `resource_blocks` (indices into `blocks`) holds a state that is given, not
  prepared by you, for example an encoded magic state. In the circuit you may
  prepare it in any stabilizer state so that the circuit is deterministic; the
  gate removes that preparation from the segment and treats the block as an
  input. Flow outputs must generate all logical operators of the other blocks
  and must not act on resource blocks (they are consumed). The resource
  itself is not verified; the table records the assumption.
- Distance: the exact circuit-level distance of the re-noised circuit, counting
  every observable in the circuit, must equal your claim (or be at least the
  claim for `at_least`). The circuit is fault-tolerant at this instance iff the
  circuit distance equals the code distance.

Helpers (importable in `build.py` and in your own scripts):

    from agent_for_qec.v2.helpers import patch_block, patch_logicals, flow, \
        check_flows_like_gate, logical_segment, standard_noise

`logical_segment` and `standard_noise` are the exact functions the gate uses;
`check_flows_like_gate(circuit, blocks, flows)` runs the gate's signed flow
check, so you can test before submitting.

Submitting: `{QEC} submit <dir>` waits up to about nine minutes for the gate.
If verification takes longer you get a submission id; query it with
`{QEC} submission <id>` until it shows an outcome. Never end your turn with a
submission whose outcome you have not read.
