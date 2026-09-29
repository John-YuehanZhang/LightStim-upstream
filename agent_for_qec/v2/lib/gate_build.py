"""Sandboxed builder for the verification gate.

Usage (inside the sandbox): python gate_build.py <submission_copy_dir> <out_dir>

Imports <submission_copy_dir>/build.py in a fresh interpreter (the submission
directory is on sys.path, so helper modules must live inside it), calls build(),
and writes plain artefacts that the gate checks:

  out/code.json              {"Hx": ..., "Hz": ...} or {"stabilizers": [...]}
  out/circuits/<name>.stim   the circuit (any noise in it is ignored by the gate)
  out/meta.json              {"circuits": {name: {"blocks": [[...], ...], "flows": [...]}}}

build() must return:
  {
    "code": {"patch": <QECPatch>} | {"Hx": ..., "Hz": ...} | {"stabilizers": [...]},
    "circuits": {name: {"circuit": stim.Circuit,
                        "blocks": [[global data qubit index for local qubit 0..n-1], ...],   # one list per code block
                        "flows": ["<stim flow string>", ...]}}
  }
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def patch_to_code(patch):
    data = sorted(patch.data_indices)
    local = {q: i for i, q in enumerate(data)}
    n = len(data)

    def s(pauli):
        chars = ["I"] * n
        for q, p in pauli.items():
            if q in local:
                chars[local[q]] = p
        return "".join(chars)

    return {"stabilizers": [s(st["pauli"]) for st in patch.stabilizers]}


def main():
    sub, out = Path(sys.argv[1]), Path(sys.argv[2])
    (out / "circuits").mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(sub))
    spec = importlib.util.spec_from_file_location("submission_build", sub / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    res = mod.build()
    code = res.get("code")
    if code is not None:
        if "patch" in code:
            code = patch_to_code(code["patch"])
        else:
            code = {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in code.items() if k != "logicals"}
        (out / "code.json").write_text(json.dumps(code))
    meta = {"circuits": {}}
    for name, c in (res.get("circuits") or {}).items():
        if not all(ch.isalnum() or ch in "_-" for ch in name):
            raise ValueError(f"bad circuit name {name!r}")
        (out / "circuits" / f"{name}.stim").write_text(str(c["circuit"]))
        meta["circuits"][name] = {"blocks": [[int(q) for q in b] for b in c.get("blocks") or []],
                                  "flows": [str(f) for f in c.get("flows") or []]}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
