"""Verification gate for agent-for-QEC v2.

The gate is the ONLY code path that writes facts. It implements the
programmatic checks (the analogue of a Lean kernel check in AI-for-math):

  P1  code: stabilizers commute, logicals commute with stabilizers, n and k match
      the claim, exact code distance equals the claimed d (SAT, witness-closed)
  P2  circuit sanity: noiseless circuit has zero detection events and zero
      observable flips; the DEM builds with no non-deterministic detector;
      at least one detector and one observable
  P3  signed logical action: every declared stim flow holds WITH sign
  P4  circuit-level distance: exact value (relaxation lower bound + lifted
      witness, closed by MILP when needed) equals the claim; the operation is
      fault-tolerant at this instance iff it equals the code distance

Non-programmatic judgements (platform suitability, novelty, whether the
declared specification is the intended operation) are NOT decided here; they
are recorded separately by the reviewer / novelty roles and the human.

Everything the agent built is rebuilt in a fresh interpreter (gate_build.py)
and read back from plain text, so the gate never trusts objects from the
agent's own process.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import stim

HERE = Path(__file__).resolve().parent
V2 = HERE.parent
REPO = V2.parents[1]
TOOLS = V2.parent / "tools"
sys.path.insert(0, str(TOOLS))
from verify_stack import (code_distance_symplectic, min_weight_vector, gf2_rank,  # noqa: E402
                          check_flows, noiseless_sanity, platform_report, _dem_matrices)
from circuit_distance_fast import fast_circuit_distance  # noqa: E402

PY = os.environ.get("QEC_PYTHON", "/home/yuehan/miniconda3/envs/light_stim/bin/python")
RESULTS = Path(os.environ.get("QEC_RESULTS_ROOT", str(V2 / "results")))


# ---------------------------------------------------------------- GF(2) helpers
def nullspace_gf2(M: np.ndarray) -> np.ndarray:
    M = (np.asarray(M) % 2).astype(np.uint8).copy()
    rows, cols = M.shape
    pivots, r = [], 0
    for c in range(cols):
        piv = next((i for i in range(r, rows) if M[i, c]), None)
        if piv is None:
            continue
        M[[r, piv]] = M[[piv, r]]
        for i in range(rows):
            if i != r and M[i, c]:
                M[i] ^= M[r]
        pivots.append(c)
        r += 1
        if r == rows:
            break
    free = [c for c in range(cols) if c not in pivots]
    basis = []
    for f in free:
        v = np.zeros(cols, dtype=np.uint8)
        v[f] = 1
        for i, pc in enumerate(pivots):
            if M[i, f]:
                v[pc] = 1
        basis.append(v)
    return np.array(basis, dtype=np.uint8).reshape(len(basis), cols)


def pauli_to_xz(s: str) -> np.ndarray:
    n = len(s)
    v = np.zeros(2 * n, dtype=np.uint8)
    for i, ch in enumerate(s):
        if ch in "XY":
            v[i] = 1
        if ch in "ZY":
            v[n + i] = 1
    return v


def symp(a: np.ndarray, b: np.ndarray) -> int:
    n = len(a) // 2
    return int((a[:n] @ b[n:] + a[n:] @ b[:n]) % 2)


# ---------------------------------------------------------------- P1
def check_code(code: dict, claims: dict, timeout_s: float) -> dict:
    out: Dict = {"check": "P1_code"}
    if "Hx" in code:
        Hx = np.array(code["Hx"], dtype=np.uint8) % 2
        Hz = np.array(code["Hz"], dtype=np.uint8) % 2
        n = Hx.shape[1]
        comm = not ((Hx @ Hz.T) % 2).any()
        k = n - gf2_rank(Hx) - gf2_rank(Hz)
        out.update(n=n, k=k, commute=comm)
        if not comm:
            return {**out, "pass": False, "reason": "Hx Hz^T != 0"}
        # dX: min |e| with Hz e = 0 and e notin rowspace(Hx)  <=>  g.e = 1 for some g in ker(Hx)
        rx = min_weight_vector(Hz, nullspace_gf2(Hx), timeout_s=timeout_s)
        rz = min_weight_vector(Hx, nullspace_gf2(Hz), timeout_s=timeout_s)
        d = None if rx.weight is None or rz.weight is None else min(rx.weight, rz.weight)
        out.update(dX=rx.weight, dZ=rz.weight, d=d, status=f"X:{rx.status},Z:{rz.status}",
                   witness_X=rx.witness, witness_Z=rz.witness)
    else:
        S = np.array([pauli_to_xz(s) for s in code["stabilizers"]], dtype=np.uint8)
        L = np.array([pauli_to_xz(s) for s in code["logicals"]], dtype=np.uint8)
        n = S.shape[1] // 2
        comm = all(symp(a, b) == 0 for i, a in enumerate(S) for b in S[i + 1:])
        lcomm = all(symp(a, b) == 0 for a in S for b in L)
        k = n - gf2_rank(S)
        out.update(n=n, k=k, commute=comm, logicals_commute=lcomm, n_logical_reps=len(L))
        if not (comm and lcomm):
            return {**out, "pass": False, "reason": "stabilizers or logicals fail to commute"}
        r = code_distance_symplectic(list(S), list(L), n, timeout_s=timeout_s)
        d = r.d
        out.update(d=d, status=r.status, witness=r.detail.get("witness"))
    problems = []
    if claims.get("n") is not None and claims["n"] != out["n"]:
        problems.append(f"claimed n={claims['n']} but n={out['n']}")
    if claims.get("k") is not None and claims["k"] != out["k"]:
        problems.append(f"claimed k={claims['k']} but k={out['k']}")
    if out["k"] <= 0:
        problems.append("k <= 0")
    if d is None:
        problems.append("code distance not proven within the time limit")
    elif claims.get("d") is not None and claims["d"] != d:
        problems.append(f"claimed d={claims['d']} but exact d={d}")
    out["pass"] = not problems
    if problems:
        out["reason"] = "; ".join(problems)
    return out


# ---------------------------------------------------------------- witness explanation
def explain_witness(circuit: stim.Circuit, observables, cols: List[int], limit: int = 12) -> List[str]:
    dem = circuit.detector_error_model(decompose_errors=False, flatten_loops=True)
    _, _, keys, _ = _dem_matrices(dem, observables)
    lines = []
    for c in cols[:limit]:
        dets, obs = keys[c]
        parts = [f"D{d}" for d in sorted(dets)] + [f"L{o}" for o in sorted(obs)]
        lines.append("error(0.001) " + " ".join(parts))
    try:
        filt = stim.DetectorErrorModel("\n".join(lines))
        expl = circuit.explain_detector_error_model_errors(dem_filter=filt, reduce_to_one_representative_error=True)
    except Exception as ex:  # explanation is diagnostic only
        return [f"(explanation failed: {type(ex).__name__}: {str(ex)[:120]})"]
    out = []
    for e in expl:
        if not e.circuit_error_locations:
            out.append(f"{e.dem_error_terms}: no circuit location")
            continue
        loc = e.circuit_error_locations[0]
        tg = loc.instruction_targets
        qs = [(t.gate_target.value, list(t.coords)) for t in tg.targets_in_range]
        flipped = [str(p.gate_target) + str(list(p.coords)) for p in loc.flipped_pauli_product]
        out.append(f"tick={loc.tick_offset} gate={tg.gate} targets={qs} flipped={flipped}")
    return out


# ---------------------------------------------------------------- P2-P4
TWO_Q = {"CX", "CNOT", "CZ", "CY", "SWAP", "ISWAP", "ISWAP_DAG", "XCX", "XCY", "XCZ", "YCX", "YCY", "YCZ",
         "ZCX", "ZCY", "ZCZ", "SQRT_XX", "SQRT_YY", "SQRT_ZZ", "SQRT_XX_DAG", "SQRT_YY_DAG", "SQRT_ZZ_DAG"}


def _two_qubit_sequence(c: stim.Circuit):
    seq = []
    for inst in c.without_noise().flattened():
        if inst.name in TWO_Q:
            ts = [t.value for t in inst.targets_copy()]
            seq += [(inst.name, a, b) for a, b in zip(ts[::2], ts[1::2])]
    return seq


def check_circuit(name: str, circuit: stim.Circuit, spec: dict, data_qubits: List[int],
                  code_d: Optional[int], milp_time_s: float) -> dict:
    out: Dict = {"check": "P2-P4_circuit", "name": name}
    problems = []
    ok, nd, no = noiseless_sanity(circuit)
    out["P2_noiseless"] = {"pass": ok, "detection_events": nd, "observable_flips": no}
    if not ok:
        problems.append("noiseless circuit fires detectors or flips observables")
    try:
        dem = circuit.detector_error_model(decompose_errors=False, flatten_loops=True, allow_gauge_detectors=False)
        out["P2_dem"] = {"pass": True, "detectors": dem.num_detectors, "observables": dem.num_observables}
        if dem.num_detectors == 0 or dem.num_observables == 0:
            problems.append("circuit has no detectors or no observables")
            out["P2_dem"]["pass"] = False
    except Exception as ex:
        out["P2_dem"] = {"pass": False, "error": str(ex)[:600]}
        problems.append("DEM construction failed (non-deterministic detector/observable)")
    flows = spec.get("flows") or []
    flow_circ = spec.get("_flow_circuit") or circuit
    if spec.get("_flow_circuit") is not None:
        same = _two_qubit_sequence(flow_circ) == _two_qubit_sequence(circuit)
        out["P3_flow_circuit_matches"] = same
        if not same:
            problems.append("flow_circuit's two-qubit gate sequence differs from the main circuit")
    if flows:
        try:
            fr = check_flows(flow_circ, flows)
            out["P3_flows"] = {"pass": fr.all_ok, "signed": fr.results, "unsigned": fr.unsigned_results}
            if not fr.all_ok:
                bad = [f for f, v in fr.results.items() if not v]
                sign_only = [f for f in bad if fr.unsigned_results.get(f)]
                problems.append(f"{len(bad)} declared flow(s) fail with sign"
                                + (f" ({len(sign_only)} hold unsigned: Pauli-frame/sign error)" if sign_only else ""))
        except Exception as ex:
            out["P3_flows"] = {"pass": False, "error": str(ex)[:400]}
            problems.append("flow check raised")
    else:
        out["P3_flows"] = {"pass": None, "note": "no flows declared (allowed only for memory experiments)"}
        if spec.get("requires_flows", True) and spec.get("kind") == "logical_op":
            problems.append("logical operation declared without flows")
    if not problems or out.get("P2_dem", {}).get("pass"):
        claimed = spec.get("claimed_circuit_distance")
        ctype = spec.get("claim_type", "exact")
        obs = spec.get("observables")
        r = fast_circuit_distance(circuit, data_qubits=data_qubits or None, observables=obs,
                                  milp_time_s=milp_time_s)
        p4 = {"lb": r.lb, "ub": r.ub, "status": r.status, "lb_method": r.lb_method, "ub_method": r.ub_method,
              "detectors": r.n_detectors, "mechanisms": r.n_mechanisms, "seconds": round(r.seconds, 1),
              "claimed": claimed, "claim_type": ctype}
        wit = r.detail.get("witness")
        if wit is not None and (claimed is None or (r.ub is not None and r.ub < claimed)):
            p4["witness_explained"] = explain_witness(circuit, obs, wit)
        if claimed is None:
            problems.append("no claimed_circuit_distance")
        elif ctype == "exact":
            if r.status == "exact" and r.lb == claimed:
                p4["pass"] = True
            elif r.ub is not None and r.ub < claimed:
                p4["pass"] = False
                problems.append(f"circuit distance is at most {r.ub} < claimed {claimed}")
            elif r.status == "exact":
                p4["pass"] = False
                problems.append(f"exact circuit distance {r.lb} != claimed {claimed}")
            else:
                p4["pass"] = False
                problems.append(f"only bounds proven: [{r.lb}, {r.ub}] (claim exact {claimed})")
                out["bounds_only"] = True
        else:  # at_least
            p4["pass"] = r.lb >= claimed
            if not p4["pass"]:
                problems.append(f"proven lower bound {r.lb} < claimed {claimed}"
                                + (f" (upper bound {r.ub})" if r.ub is not None else ""))
        dist = r.lb if r.status == "exact" else None
        if code_d is not None:
            p4["fault_tolerant_at_instance"] = (dist is not None and dist == code_d) or \
                                               (r.status != "exact" and r.lb >= code_d)
        out["P4_distance"] = p4
    try:
        pr = platform_report(circuit)
        out["platform_stats"] = pr.__dict__
    except Exception:
        pass
    out["pass"] = not problems
    if problems:
        out["reason"] = "; ".join(problems)
    return out


# ---------------------------------------------------------------- entry point
def run_gate(sub_dir: Path, store, author: str, milp_time_s: float = 1800.0,
             code_timeout_s: float = 600.0) -> dict:
    """Verify a submission directory; on success insert a fact. Returns the report."""
    from store import sha  # local import: lib/ is on sys.path via qec.py
    sub_dir = Path(sub_dir).resolve()
    spec = json.loads((sub_dir / "submission.json").read_text())
    build_bytes = (sub_dir / "build.py").read_bytes()
    deps = sorted(spec.get("depends_on") or [])
    sub_id = sha((sub_dir / "submission.json").read_bytes() + build_bytes + json.dumps(deps).encode())
    work = store.dir / "gate_work" / sub_id[:16]
    if work.exists():
        shutil.rmtree(work)
    t0 = time.time()
    report: Dict = {"submission_id": sub_id, "title": spec.get("title"), "kind": spec.get("kind"),
                    "author": author, "checks": []}
    proc = subprocess.run([PY, str(HERE / "gate_build.py"), str(sub_dir), str(work)], cwd=str(REPO),
                          env={**os.environ, "PYTHONPATH": str(REPO)}, capture_output=True, text=True,
                          timeout=3600)
    if proc.returncode != 0:
        report.update(outcome="error", reason="build.py failed in a fresh process",
                      stderr_tail=proc.stderr[-2000:])
        store.add_submission(sub_id, author, str(sub_dir), "error", report, None)
        _log_verification(store, author, report)
        return report
    meta = json.loads((work / "meta.json").read_text())
    report["build_sha"], report["repo_head"] = meta.get("build_sha"), meta.get("repo_head")
    problems, bounds_only = [], False
    code_d = None
    code_claims = spec.get("code") or {}
    if (work / "code.json").exists():
        cr = check_code(json.loads((work / "code.json").read_text()), code_claims, code_timeout_s)
        report["checks"].append(cr)
        code_d = cr.get("d")
        if not cr["pass"]:
            problems.append("P1: " + cr["reason"])
    elif spec.get("kind") in ("code", "logical_op"):
        problems.append("P1: kind requires build() to return 'code'")
    circuit_specs = {c["name"]: c for c in spec.get("circuits") or []}
    built = meta["circuits"]
    for name in sorted(set(circuit_specs) | set(built)):
        if name not in built:
            problems.append(f"circuit {name} declared but not built")
            continue
        if name not in circuit_specs:
            problems.append(f"circuit {name} built but not declared in submission.json")
            continue
        circ = stim.Circuit((work / "circuits" / f"{name}.stim").read_text())
        cspec = {**circuit_specs[name], "kind": spec.get("kind")}
        if not cspec.get("flows") and built[name].get("flows"):
            cspec["flows"] = built[name]["flows"]
        fpath = work / "circuits" / f"{name}.flow.stim"
        if fpath.exists():
            cspec["_flow_circuit"] = stim.Circuit(fpath.read_text())
        rr = check_circuit(name, circ, cspec, built[name]["data_qubits"], code_d, milp_time_s)
        report["checks"].append(rr)
        if not rr["pass"]:
            problems.append(f"{name}: " + rr["reason"])
            bounds_only |= bool(rr.get("bounds_only"))
    if spec.get("kind") in ("memory", "logical_op") and not built:
        problems.append("no circuits built")
    report["seconds"] = round(time.time() - t0, 1)
    fact_id = None
    if problems:
        report["outcome"] = "bounds_only" if (bounds_only and all("only bounds" in p for p in problems)) else "rejected"
        report["reason"] = " | ".join(problems)
    else:
        report["outcome"] = "accepted"
        fact_id = sub_id
        claims = {k: spec.get(k) for k in ("code", "circuits", "description", "kind", "title")}
        store.add_fact(fact_id, spec.get("kind"), spec.get("title"), claims, report, deps, sub_id, author,
                       origin=spec.get("origin", "agent"))
        dest = RESULTS / store.project / "facts" / fact_id[:12]
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sub_dir / "submission.json", dest / "submission.json")
        shutil.copy2(sub_dir / "build.py", dest / "build.py")
        (dest / "verdict.json").write_text(json.dumps(report, indent=1, default=str))
    report["fact_id"] = fact_id
    store.add_submission(sub_id, author, str(sub_dir), report["outcome"], report, fact_id)
    _log_verification(store, author, report)
    return report


def _log_verification(store, author: str, report: dict) -> None:
    """Every gate outcome becomes a shared 'verification' memory so siblings learn from rejections."""
    claim = f"[{report.get('outcome')}] {report.get('title')}"
    ev = report.get("reason", "all programmatic checks passed")
    wits = [c["P4_distance"]["witness_explained"] for c in report.get("checks", [])
            if isinstance(c.get("P4_distance"), dict) and c["P4_distance"].get("witness_explained")]
    if wits:
        ev += "\nlightest undetected logical error (first circuit): " + "; ".join(wits[0][:8])
    store.add_memory("verification", author, claim, ev,
                     refs=[r for r in [report.get("fact_id"), report.get("submission_id")] if r])
