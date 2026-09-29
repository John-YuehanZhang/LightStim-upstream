### Submission format

A submission is a directory containing two files.

`submission.json`:

    {
      "title": "short name of the construction",
      "kind": "code" | "memory" | "logical_op",
      "description": "the intended logical action in words, e.g. 'CNOT_L from block A to block B'",
      "code": {"n": 9, "k": 1, "d": 3},                 # your claims about the code
      "circuits": [
        {"name": "cnot_ZZ", "claimed_circuit_distance": 3, "claim_type": "exact"}
      ],
      "depends_on": ["<fact id>", ...]                  # facts this construction relies on, if any
    }

`build.py` defines `build()` returning:

    {
      "code": {"patch": <LightStim QECPatch>}                  # or {"Hx": [[..]], "Hz": [[..]]}
                                                               # or {"stabilizers": ["XXZI.."], "logicals": [...]}
      "circuits": {
        "cnot_ZZ": {
          "circuit": <noisy stim.Circuit with DETECTOR and OBSERVABLE_INCLUDE>,
          "data_qubits": [...],                                # indices of all data qubits (speeds up the distance proof)
          "flow_circuit": <noiseless logical segment, optional>,
          "flows": ["X1*X7*X13 -> X1*X7*X13*X18*X24*X30", ...]  # stim flow strings, WITH sign
        }
      }
    }

- Noise: circuit-level depolarising noise with all rates p = 1e-3
  (`NoiseConfig(p_1q=p, p_2q=p, p_meas=p, p_reset=p, p_idle=p)`,
  `noise_model="circuit_level"`) unless the task says otherwise.
- `flows` state the logical action: for a logical operation declare the image
  of a generating set of logical Paulis (for k logical qubits, 2k flows),
  with signs. Flows are checked on `flow_circuit` if given, otherwise on the
  circuit itself. `flow_circuit` is the logical segment (e.g. the experiment
  without the initial data preparation and the final data readout); its
  two-qubit gate sequence must equal the main circuit's.
- `build.py` is executed from the repository root in a fresh interpreter; it
  may import LightStim and files in your working directory (add that directory
  to `sys.path` inside `build.py`).
- Circuit-level distance claims are exact values unless you set
  `"claim_type": "at_least"`.
