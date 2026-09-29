"""Verification gate for agent-for-QEC v2.

The gate is the only code path that writes facts. It decides acceptance by
programmatic checks (the analogue of a Lean kernel check), and it computes the
specification under test itself wherever the agent could otherwise choose it:

  P1  code: stabilizers commute; n, k as claimed; exact code distance = claimed d.
      The distance is computed from the stabilizers alone (all logical operators
      come from the normalizer); agent-supplied logicals are never used.
  P2  circuit sanity: the noiseless circuit fires no detector and flips no
      observable; the detector error model is deterministic; there is at least one
      detector and one observable; data qubits = union of the declared code blocks,
      each block has exactly n qubits.
  P3  logical action: the declared flows hold WITH sign on the logical segment,
      which the gate derives itself from the circuit (block data preparation and
      final block readout removed). Every Pauli in the flows is a logical operator
      of the declared code on the declared blocks. For gates and memories the
      flows' inputs and outputs each generate the full logical group (2k per
      block); for measurements a logical operator must map to measurement records.
  P4  circuit-level distance: the gate strips all noise and injects its standard
      noise model (circuitops.standard_noise); the distance counts every
      observable of the circuit; exact value must equal the claim. Fault-tolerant
      at this instance iff it equals the code distance d.

Submissions are self-contained directories; the whole directory is hashed and
archived. build.py runs in a sandbox (read-only filesystem, no network, no
access to the shared store or credentials) in a fresh interpreter.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
sys.path.insert(0, str(HERE))
from verify_stack import min_weight_vector, min_weight_vector_milp, check_flows, noiseless_sanity, \
    platform_report, _dem_matrices  # noqa: E402
from circuit_distance_fast import fast_circuit_distance  # noqa: E402
from circuitops import standard_noise, logical_segment  # noqa: E402
from codecheck import symplectic_matrix, commute_matrix, gf2_rank, code_distance, check_logical_flows  # noqa: E402
import sandbox  # noqa: E402

PY = os.environ.get("QEC_PYTHON", "/home/yuehan/miniconda3/envs/light_stim/bin/python")
RESULTS = Path(os.environ.get("QEC_RESULTS_ROOT", str(V2 / "results")))
KINDS = {"code", "memory", "logical_gate", "logical_measurement"}
CLAIM_TYPES = {"exact", "at_least"}
MAX_BUNDLE_BYTES = 20 * 1024 * 1024


# ---------------------------------------------------------------- provenance
def bundle_hash(sub_dir: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(sub_dir.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(sub_dir)).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def tree_state() -> dict:
    """Repository HEAD and whether the code the gate trusts differs from it."""
    def git(*a):
        return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True).stdout.strip()
    paths = ["agent_for_qec/v2/lib", "agent_for_qec/tools", "lightstim"]
    dirty = git("status", "--porcelain", "--", *paths)
    return {"repo_head": git("rev-parse", "HEAD"), "trusted_code_dirty": bool(dirty),
            "dirty_files": dirty.splitlines()[:20]}


# ---------------------------------------------------------------- P1
def _min_weight(H, L, weight_of=None, timeout_s=600.0, witness=None):
    r = min_weight_vector_milp(H, L, weight_of=weight_of, time_limit_s=timeout_s)
    if r.status != "exact":
        r2 = min_weight_vector(H, L, weight_of=weight_of, timeout_s=timeout_s)
        if r2.status == "exact":
            return r2
    return r


def check_code(code: dict, claims: dict, timeout_s: float) -> dict:
    out: Dict = {"check": "P1_code"}
    try:
        S, n, css = symplectic_matrix(code)
    except Exception as ex:
        return {**out, "pass": False, "reason": f"unreadable code: {ex}"}, None
    comm = not commute_matrix(S, S).any()
    k = n - gf2_rank(S)
    out.update(n=n, k=k, commute=comm, css=css, n_stabilizer_rows=int(S.shape[0]))
    if not comm:
        return {**out, "pass": False, "reason": "stabilizers do not commute"}, None
    if k <= 0:
        return {**out, "pass": False, "reason": "k <= 0"}, None
    cd = code_distance(S, lambda H, L, weight_of, timeout_s: _min_weight(H, L, weight_of, timeout_s), timeout_s)
    d = cd["d"]
    out.update(d=d, status=cd["status"], witness=cd["witness"])
    problems = []
    for key in ("n", "k"):
        if claims.get(key) is not None and claims[key] != out[key]:
            problems.append(f"claimed {key}={claims[key]} but {key}={out[key]}")
    if d is None:
        problems.append("code distance not proven within the time limit")
    elif claims.get("d") is None or claims["d"] != d:
        problems.append(f"claimed d={claims.get('d')} but exact d={d}")
    out["pass"] = not problems
    if problems:
        out["reason"] = "; ".join(problems)
    return out, S


# ---------------------------------------------------------------- witness explanation
def explain_witness(circuit: stim.Circuit, cols: List[int], limit: int = 12) -> List[str]:
    dem = circuit.detector_error_model(decompose_errors=False, flatten_loops=True)
    _, _, keys, _ = _dem_matrices(dem, None)
    lines = []
    for c in cols[:limit]:
        dets, obs = keys[c]
        lines.append("error(0.001) " + " ".join([f"D{d}" for d in sorted(dets)] + [f"L{o}" for o in sorted(obs)]))
    try:
        expl = circuit.explain_detector_error_model_errors(dem_filter=stim.DetectorErrorModel("\n".join(lines)),
                                                          reduce_to_one_representative_error=True)
    except Exception as ex:
        return [f"(explanation failed: {type(ex).__name__}: {str(ex)[:120]})"]
    out = []
    for e in expl:
        if not e.circuit_error_locations:
            continue
        loc = e.circuit_error_locations[0]
        tg = loc.instruction_targets
        qs = [(t.gate_target.value, list(t.coords)) for t in tg.targets_in_range]
        flipped = [str(p.gate_target) + str(list(p.coords)) for p in loc.flipped_pauli_product]
        out.append(f"tick={loc.tick_offset} gate={tg.gate} targets={qs} flipped={flipped}")
    return out


# ---------------------------------------------------------------- P2-P4
def check_circuit(name: str, circuit: stim.Circuit, spec: dict, built: dict, S, code_n: int, code_d,
                  kind: str, p: float, milp_time_s: float) -> dict:
    out: Dict = {"check": "P2-P4_circuit", "name": name}
    problems = []
    blocks = [[int(q) for q in b] for b in built.get("blocks") or []]
    data = sorted({q for b in blocks for q in b})
    # --- P2 structure
    if not blocks:
        problems.append("no code blocks declared (build must return 'blocks')")
    elif any(len(b) != code_n for b in blocks) or len(data) != sum(len(b) for b in blocks):
        problems.append(f"each block must list exactly n={code_n} distinct data qubits")
    ok, nd, no = noiseless_sanity(circuit)
    out["P2_noiseless"] = {"pass": ok, "detection_events": nd, "observable_flips": no}
    if not ok:
        problems.append("noiseless circuit fires detectors or flips observables")
    noisy = standard_noise(circuit, p)
    try:
        dem = noisy.detector_error_model(decompose_errors=False, flatten_loops=True, allow_gauge_detectors=False)
        out["P2_dem"] = {"pass": dem.num_detectors > 0 and dem.num_observables > 0,
                         "detectors": dem.num_detectors, "observables": dem.num_observables}
        if not out["P2_dem"]["pass"]:
            problems.append("circuit has no detectors or no observables")
    except Exception as ex:
        out["P2_dem"] = {"pass": False, "error": str(ex)[:600]}
        problems.append("detector error model failed (non-deterministic detector or observable)")
    # --- P3 flows on the gate-derived segment
    flows = built.get("flows") or []
    if S is not None and blocks:
        seg = logical_segment(circuit, data)
        lf = check_logical_flows(flows, S, blocks, kind)
        out["P3_code_flows"] = {k: v for k, v in lf.items() if k != "problems"}
        problems += lf["problems"]
        if flows:
            try:
                fr = check_flows(seg, flows)
                out["P3_flows"] = {"pass": fr.all_ok, "signed": fr.results, "unsigned": fr.unsigned_results}
                if not fr.all_ok:
                    bad = [f for f, v in fr.results.items() if not v]
                    sign_only = [f for f in bad if fr.unsigned_results.get(f)]
                    problems.append(f"{len(bad)} declared flow(s) fail with sign on the logical segment"
                                    + (f" ({len(sign_only)} hold unsigned: sign/Pauli-frame error)" if sign_only else ""))
            except Exception as ex:
                out["P3_flows"] = {"pass": False, "error": str(ex)[:400]}
                problems.append("flow check raised")
        else:
            problems.append("no flows declared")
    # --- P4 distance on the gate-noised circuit, all observables
    if out.get("P2_dem", {}).get("pass"):
        claimed = spec.get("claimed_circuit_distance")
        ctype = spec.get("claim_type", "exact")
        r = fast_circuit_distance(noisy, data_qubits=data or None, observables=None, milp_time_s=milp_time_s)
        p4 = {"lb": r.lb, "ub": r.ub, "status": r.status, "lb_method": r.lb_method, "ub_method": r.ub_method,
              "detectors": r.n_detectors, "mechanisms": r.n_mechanisms, "seconds": round(r.seconds, 1),
              "claimed": claimed, "claim_type": ctype, "noise": f"standard p={p}"}
        if r.lb_method == "no-logical" or r.lb >= 10 ** 8:
            p4["pass"] = False
            problems.append("no logical error mechanism found: the circuit has no effective logical observable")
        elif not isinstance(claimed, int):
            p4["pass"] = False
            problems.append("claimed_circuit_distance must be an integer")
        elif ctype == "exact":
            if r.status == "exact" and r.lb == claimed:
                p4["pass"] = True
            else:
                p4["pass"] = False
                if r.ub is not None and r.ub < claimed:
                    problems.append(f"circuit distance is at most {r.ub} < claimed {claimed}")
                elif r.status == "exact":
                    problems.append(f"exact circuit distance {r.lb} != claimed {claimed}")
                else:
                    problems.append(f"only bounds proven: [{r.lb}, {r.ub}] (claim exact {claimed})")
                    out["bounds_only"] = True
        else:
            p4["pass"] = r.lb >= claimed
            if not p4["pass"]:
                problems.append(f"proven lower bound {r.lb} < claimed {claimed}")
        wit = r.detail.get("witness")
        if wit is not None and (not p4.get("pass") or (code_d is not None and r.ub is not None and r.ub < code_d)):
            p4["witness_explained"] = explain_witness(noisy, wit)
        exact = r.lb if r.status == "exact" else None
        if code_d is not None:
            p4["fault_tolerant_at_instance"] = (exact == code_d) if exact is not None else (r.lb >= code_d)
        out["P4_distance"] = p4
    try:
        out["platform_stats"] = platform_report(circuit).__dict__
    except Exception:
        pass
    out["pass"] = not problems
    if problems:
        out["reason"] = "; ".join(problems)
    return out


# ---------------------------------------------------------------- build in sandbox
def build_in_sandbox(sub_dir: Path, work: Path, timeout_s: int = 3600) -> subprocess.CompletedProcess:
    work.mkdir(parents=True, exist_ok=True)
    src = work / "src"
    shutil.copytree(sub_dir, src, ignore=shutil.ignore_patterns("__pycache__"))
    out = work / "out"
    out.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "PYTHONPATH": f"{REPO}:{src}", "PYTHONDONTWRITEBYTECODE": "1",
           "OMP_NUM_THREADS": "4"}
    cmd = [PY, str(HERE / "gate_build.py"), str(src), str(out)]
    if sandbox.bwrap_available():
        pyenv = str(Path(PY).resolve().parents[1])
        cmd = sandbox.wrap_minimal(cmd, readonly=[pyenv, str(REPO), str(src)], writable=[str(out)], env=env,
                                   cwd=str(src), network=False)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    return subprocess.run(cmd, cwd=str(src), env=env, capture_output=True, text=True, timeout=timeout_s)


# ---------------------------------------------------------------- entry point
def run_gate(sub_dir: Path, store, author: str, milp_time_s: float = 1800.0, code_timeout_s: float = 600.0,
             noise_p: Optional[float] = None) -> dict:
    """Verify a submission directory; on success insert a fact. Returns the report.
    Never raises for problems in the submission: every outcome is recorded."""
    sub_dir = Path(sub_dir).resolve()
    t0 = time.time()
    report: Dict = {"author": author, "checks": []}
    sub_id = None
    try:
        size = sum(p.stat().st_size for p in sub_dir.rglob("*") if p.is_file())
        if size > MAX_BUNDLE_BYTES:
            raise ValueError(f"submission directory too large ({size} bytes)")
        spec = json.loads((sub_dir / "submission.json").read_text())
        if not (sub_dir / "build.py").exists():
            raise ValueError("build.py missing")
        sub_id = bundle_hash(sub_dir)
        report.update(submission_id=sub_id, title=spec.get("title"), kind=spec.get("kind"))
        kind = spec.get("kind")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}")
        for c in spec.get("circuits") or []:
            if c.get("claim_type", "exact") not in CLAIM_TYPES:
                raise ValueError(f"claim_type must be one of {sorted(CLAIM_TYPES)}")
            extra = set(c) - {"name", "claimed_circuit_distance", "claim_type"}
            if extra:
                raise ValueError(f"unknown circuit fields {sorted(extra)}")
        deps = [store.resolve_fact_id(d) for d in (spec.get("depends_on") or [])]
        report["depends_on"] = deps
        existing = store.fact(sub_id, exact=True)
        if existing is not None:
            report.update(outcome="duplicate", fact_id=sub_id,
                          reason=f"identical bundle already recorded as fact with status {existing['status']}")
            store.add_submission(sub_id, author, str(sub_dir), "duplicate", report, None)
            _log_verification(store, author, report)
            return report
    except Exception as ex:
        report.update(outcome="error", reason=f"invalid submission: {ex}")
        _record(store, sub_id or f"invalid-{time.time()}", author, sub_dir, report)
        return report
    report.update(tree_state())
    (store.dir / "gate_work").mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="gate_", dir=str(store.dir / "gate_work")))
    try:
        proc = build_in_sandbox(sub_dir, work)
    except subprocess.TimeoutExpired:
        report.update(outcome="error", reason="build.py timed out in the sandbox")
        _record(store, sub_id, author, sub_dir, report)
        return report
    if proc.returncode != 0:
        report.update(outcome="error", reason="build.py failed in the sandbox", stderr_tail=proc.stderr[-3000:])
        _record(store, sub_id, author, sub_dir, report)
        return report
    out = work / "out"
    meta = json.loads((out / "meta.json").read_text())
    p = noise_p if noise_p is not None else float(store.get("noise_p", 1e-3))
    problems, bounds_only = [], False
    S, code_d, code_n = None, None, None
    try:
        if not (out / "code.json").exists():
            raise ValueError("build() must return 'code'")
        cr, S = check_code(json.loads((out / "code.json").read_text()), spec.get("code") or {}, code_timeout_s)
        report["checks"].append(cr)
        code_d, code_n = cr.get("d"), cr.get("n")
        if not cr["pass"]:
            problems.append("P1: " + cr["reason"])
        circuit_specs = {c["name"]: c for c in spec.get("circuits") or []}
        built = meta["circuits"]
        if kind != "code" and not built:
            problems.append("no circuits built")
        if kind == "code" and built:
            problems.append("kind 'code' must not contain circuits")
        for name in sorted(set(circuit_specs) | set(built)):
            if name not in built or name not in circuit_specs:
                problems.append(f"circuit {name} must be both declared in submission.json and built")
                continue
            circ = stim.Circuit((out / "circuits" / f"{name}.stim").read_text())
            rr = check_circuit(name, circ, circuit_specs[name], built[name], S, code_n, code_d, kind, p, milp_time_s)
            report["checks"].append(rr)
            if not rr["pass"]:
                problems.append(f"{name}: " + rr["reason"])
                bounds_only |= bool(rr.get("bounds_only"))
    except Exception as ex:
        problems.append(f"gate error: {type(ex).__name__}: {ex}")
    report["seconds"] = round(time.time() - t0, 1)
    fact_id = None
    if problems:
        report["outcome"] = "bounds_only" if bounds_only and all("only bounds" in x for x in problems) else "rejected"
        report["reason"] = " | ".join(problems)
    else:
        report["outcome"] = "accepted"
        fact_id = sub_id
        claims = {k: spec.get(k) for k in ("code", "circuits", "description", "kind", "title")}
        try:
            store.add_fact(fact_id, kind, spec.get("title"), claims, report, deps, sub_id, author,
                           origin=store.get("origin", "agent"))
            dest = RESULTS / store.project / "facts" / fact_id[:12]
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(sub_dir, dest / "bundle", ignore=shutil.ignore_patterns("__pycache__"))
            (dest / "verdict.json").write_text(json.dumps(report, indent=1, default=str))
        except Exception as ex:
            report.update(outcome="error", reason=f"could not record fact: {ex}")
            fact_id = None
    report["fact_id"] = fact_id
    _record(store, sub_id, author, sub_dir, report)
    shutil.rmtree(work, ignore_errors=True)
    return report


def _record(store, sub_id, author, sub_dir, report):
    try:
        store.add_submission(sub_id, author, str(sub_dir), report.get("outcome"), report, report.get("fact_id"))
    finally:
        _log_verification(store, author, report)


def _log_verification(store, author: str, report: dict) -> None:
    """Every gate outcome becomes a shared 'verification' memory so siblings learn from rejections."""
    claim = f"[{report.get('outcome')}] {report.get('title')}"
    ev = report.get("reason") or "all programmatic checks passed"
    wits = [c["P4_distance"]["witness_explained"] for c in report.get("checks", [])
            if isinstance(c.get("P4_distance"), dict) and c["P4_distance"].get("witness_explained")]
    if wits:
        ev += "\nlightest undetected logical error (first circuit): " + "; ".join(wits[0][:8])
    store.add_memory("verification", author, claim, ev,
                     refs=[r for r in [report.get("fact_id"), report.get("submission_id")] if r])
