"""Fresh-process builder for the verification gate.

Usage: python gate_build.py <submission_dir> <out_dir>

Imports <submission_dir>/build.py in a new interpreter, calls build(), and
writes plain artefacts that the gate checks without trusting any object the
agent's own process created:

  out/code.json          {"stabilizers": [...], "logicals": [...]}  Pauli strings over n data qubits
                         or {"Hx": [[...]], "Hz": [[...]]} for CSS
  out/circuits/<name>.stim   noisy stim circuit text
  out/meta.json          {"circuits": {name: {"data_qubits": [...]}}, "build_sha": ..., "repo_head": ...}

build() must return a dict:
  {
    "code": {"Hx": ..., "Hz": ...}  |  {"stabilizers": [...], "logicals": [...]}  |  {"patch": <QECPatch>},   (optional)
    "circuits": {name: {"circuit": stim.Circuit, "data_qubits": [int, ...],
                        "flow_circuit": stim.Circuit (optional)}}                                               (optional)
  }
"flow_circuit" is the logical segment on which the declared flows are checked
(e.g. the experiment without the initial data preparation and final data
readout). The gate requires its two-qubit gate sequence to equal the main
circuit's, so it cannot be an unrelated circuit.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def patch_to_paulis(patch):
    data = sorted(patch.data_indices)
    local = {q: i for i, q in enumerate(data)}
    n = len(data)

    def s(pauli):
        chars = ["I"] * n
        for q, p in pauli.items():
            if q in local:
                chars[local[q]] = p
        return "".join(chars)

    return {"stabilizers": [s(st["pauli"]) for st in patch.stabilizers],
            "logicals": [s(lg["pauli"]) for lg in patch.logical_ops]}


def main():
    sub, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    (out / "circuits").mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("submission_build", sub / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    res = mod.build()
    meta = {"circuits": {}}
    code = res.get("code")
    if code is not None:
        if "patch" in code:
            code = patch_to_paulis(code["patch"])
        else:
            code = {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in code.items()}
        (out / "code.json").write_text(json.dumps(code))
    for name, c in (res.get("circuits") or {}).items():
        circ = c["circuit"]
        (out / "circuits" / f"{name}.stim").write_text(str(circ))
        meta["circuits"][name] = {"data_qubits": [int(q) for q in c.get("data_qubits") or []],
                                  "has_flow_circuit": c.get("flow_circuit") is not None,
                                  "flows": [str(f) for f in c.get("flows") or []]}
        if c.get("flow_circuit") is not None:
            (out / "circuits" / f"{name}.flow.stim").write_text(str(c["flow_circuit"]))
    meta["build_sha"] = hashlib.sha256((sub / "build.py").read_bytes()).hexdigest()
    try:
        meta["repo_head"] = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                           cwd=Path(__file__).resolve().parents[3]).stdout.strip()
    except Exception:
        meta["repo_head"] = None
    (out / "meta.json").write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
