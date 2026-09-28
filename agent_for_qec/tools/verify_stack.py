"""agent-for-QEC verification stack.

Four checks every candidate (code, logical operation) must pass before it may
enter LEDGER.md. LER is deliberately NOT here (evaluation only, done last).

  1. code_distance(...)        exact distance of the stabilizer code (SAT, z3)
  2. circuit_distance(...)     circuit-level distance of a noisy stim circuit:
                               stim search gives a witness (upper bound),
                               z3 proves no lighter undetectable error exists
  3. check_flows(...)          signed logical action via stim.Circuit.has_flow
  4. noiseless_sanity(...)     zero detection events / observable flips at p=0

Plus platform_report(...) : connectivity statistics used for the "platform"
column (theoretical analysis, not a simulation).

Run as a script for a self-test on the rotated surface code:
  PYTHONPATH=. /home/yuehan/miniconda3/envs/light_stim/bin/python agent_for_qec/tools/verify_stack.py
"""
from __future__ import annotations

import itertools
import math
import time
from dataclasses import dataclass, field
from functools import reduce
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import stim

try:
    import z3
except ImportError:  # pragma: no cover
    z3 = None


# --------------------------------------------------------------------------- #
# GF(2) helpers
# --------------------------------------------------------------------------- #
def _rows_as_supports(M: np.ndarray) -> List[List[int]]:
    M = np.asarray(M) % 2
    return [list(np.flatnonzero(r)) for r in M]


def gf2_rank(M: np.ndarray) -> int:
    M = (np.asarray(M) % 2).astype(np.uint8).copy()
    r = 0
    rows, cols = M.shape
    for c in range(cols):
        piv = next((i for i in range(r, rows) if M[i, c]), None)
        if piv is None:
            continue
        M[[r, piv]] = M[[piv, r]]
        for i in range(rows):
            if i != r and M[i, c]:
                M[i] ^= M[r]
        r += 1
        if r == rows:
            break
    return r


# --------------------------------------------------------------------------- #
# 1+2. exact minimum weight via SAT (z3)
# --------------------------------------------------------------------------- #
@dataclass
class MinWeightResult:
    weight: Optional[int]          # exact minimum weight, None if unknown
    witness: Optional[List[int]]   # column indices of a minimum-weight solution
    proven_lower_bound: int        # largest k such that "no solution of weight <= k" was proven
    status: str                    # 'exact' | 'timeout' | 'no_solution' | 'unavailable'
    seconds: float = 0.0


def min_weight_vector(H: np.ndarray, L: np.ndarray, *, weight_of=None,
                      upper_bound: Optional[int] = None,
                      witness: Optional[Sequence[int]] = None,
                      timeout_s: float = 600.0) -> MinWeightResult:
    """Minimum weight e (binary, length n) with H e = 0 and L e != 0 (over GF(2)).

    `weight_of`: optional list of groups of columns; the weight counts groups with
    any active column (Pauli weight for symplectic (x|z) columns). Default: |e|.
    Strategy: if a witness/upper bound is known, ask z3 for a solution of weight
    <= ub-1; UNSAT proves exactness. Otherwise search k = 1,2,... upward.
    """
    t0 = time.time()
    H = np.asarray(H) % 2
    L = np.asarray(L) % 2
    n = H.shape[1]
    if z3 is None:
        return MinWeightResult(None, None, 0, "unavailable")
    if witness is not None and upper_bound is None:
        upper_bound = _weight(witness, weight_of)
    e = [z3.Bool(f"e{i}") for i in range(n)]

    def xor_row(sup):
        if not sup:
            return z3.BoolVal(False)
        return reduce(z3.Xor, [e[i] for i in sup])

    base = z3.Solver()
    for sup in _rows_as_supports(H):
        base.add(z3.Not(xor_row(sup)))
    lrows = [s for s in _rows_as_supports(L) if s]
    if not lrows:
        return MinWeightResult(None, None, 0, "no_solution", time.time() - t0)
    base.add(z3.Or([xor_row(s) for s in lrows]))
    if weight_of is None:
        groups = [[i] for i in range(n)]
    else:
        groups = [list(g) for g in weight_of]
    gvars = []
    for gi, g in enumerate(groups):
        if len(g) == 1:
            gvars.append(e[g[0]])
        else:
            gv = z3.Bool(f"g{gi}")
            base.add(gv == z3.Or([e[i] for i in g]))
            gvars.append(gv)

    def solve_le(k):
        s = z3.Solver()
        s.set("timeout", max(1000, int((timeout_s - (time.time() - t0)) * 1000)))
        s.add(base.assertions())
        s.add(z3.PbLe([(g, 1) for g in gvars], k))
        r = s.check()
        if r == z3.sat:
            m = s.model()
            sol = [i for i in range(n) if z3.is_true(m.eval(e[i], model_completion=True))]
            return "sat", sol
        return ("unsat", None) if r == z3.unsat else ("unknown", None)

    best_w = upper_bound
    best_sol = list(witness) if witness is not None else None
    proven = 0
    if best_w is not None:
        while True:
            if best_w <= 1:
                return MinWeightResult(best_w, best_sol, best_w - 1, "exact", time.time() - t0)
            r, sol = solve_le(best_w - 1)
            if r == "unsat":
                return MinWeightResult(best_w, best_sol, best_w - 1, "exact", time.time() - t0)
            if r == "unknown":
                return MinWeightResult(None, best_sol, proven, "timeout", time.time() - t0)
            best_sol = sol
            best_w = _weight(sol, weight_of)
    k = 1
    while k <= len(groups):
        r, sol = solve_le(k)
        if r == "sat":
            return MinWeightResult(_weight(sol, weight_of), sol, k - 1, "exact", time.time() - t0)
        if r == "unknown":
            return MinWeightResult(None, None, proven, "timeout", time.time() - t0)
        proven = k
        k += 1
    return MinWeightResult(None, None, proven, "no_solution", time.time() - t0)


def _weight(sol: Sequence[int], weight_of) -> int:
    if weight_of is None:
        return len(sol)
    s = set(sol)
    return sum(1 for g in weight_of if any(i in s for i in g))


def min_weight_vector_milp(H: np.ndarray, L: np.ndarray, *, weight_of=None,
                           time_limit_s: float = 600.0) -> MinWeightResult:
    """Same problem as min_weight_vector, solved as an integer program with
    HiGHS (scipy.optimize.milp). Parity constraints use integer slacks:
        H e - 2 s = 0,   L_j e - 2 t = 1   (one MILP per logical row j, take min).
    Much faster than SAT for proving optimality on DEM-sized instances.
    """
    from scipy.optimize import milp, LinearConstraint, Bounds
    from scipy.sparse import csr_matrix, hstack, eye, vstack
    t0 = time.time()
    H = np.asarray(H) % 2
    L = np.asarray(L) % 2
    m, n = H.shape
    groups = [[i] for i in range(n)] if weight_of is None else [list(g) for g in weight_of]
    ng = len(groups)
    best = None
    status = "no_solution"
    proven = 0
    lrows = [j for j in range(L.shape[0]) if L[j].any()]
    for j in lrows:
        remaining = time_limit_s - (time.time() - t0)
        if remaining <= 1:
            status = "timeout"
            break
        # variables: e (n) | g (ng, only if grouped) | s (m) | t (1)
        use_g = weight_of is not None
        nv = n + (ng if use_g else 0) + m + 1
        off_g = n
        off_s = n + (ng if use_g else 0)
        off_t = off_s + m
        rows, cols, vals = [], [], []
        lo, hi = [], []
        r = 0
        for i in range(m):                       # H e - 2 s_i = 0
            for c in np.flatnonzero(H[i]):
                rows.append(r); cols.append(int(c)); vals.append(1.0)
            rows.append(r); cols.append(off_s + i); vals.append(-2.0)
            lo.append(0.0); hi.append(0.0); r += 1
        for c in np.flatnonzero(L[j]):           # L_j e - 2 t = 1
            rows.append(r); cols.append(int(c)); vals.append(1.0)
        rows.append(r); cols.append(off_t); vals.append(-2.0)
        lo.append(1.0); hi.append(1.0); r += 1
        if use_g:                                # g_k >= e_i for i in group k
            for k, g in enumerate(groups):
                for i in g:
                    rows.append(r); cols.append(off_g + k); vals.append(1.0)
                    rows.append(r); cols.append(int(i)); vals.append(-1.0)
                    lo.append(0.0); hi.append(np.inf); r += 1
        A = csr_matrix((vals, (rows, cols)), shape=(r, nv))
        cobj = np.zeros(nv)
        if use_g:
            cobj[off_g:off_g + ng] = 1.0
        else:
            cobj[:n] = 1.0
        lb = np.zeros(nv); ub = np.full(nv, np.inf)
        ub[:n] = 1.0
        if use_g:
            ub[off_g:off_g + ng] = 1.0
        ub[off_s:off_s + m] = np.maximum(1, np.ceil(H.sum(1) / 2.0))
        ub[off_t] = max(1, math.ceil(L[j].sum() / 2.0))
        integrality = np.ones(nv)
        res = milp(c=cobj, constraints=LinearConstraint(A, lo, hi), integrality=integrality,
                   bounds=Bounds(lb, ub), options={"time_limit": max(1.0, remaining), "disp": False})
        if res.status == 0 and res.x is not None:
            w = int(round(res.fun))
            sol = [i for i in range(n) if res.x[i] > 0.5]
            if best is None or w < best[0]:
                best = (w, sol)
            status = "exact" if status != "timeout" else status
            row_lb = w
        elif res.status == 1:  # time limit: keep incumbent + dual bound
            status = "timeout"
            if res.x is not None:
                w = int(round(res.fun)); sol = [i for i in range(n) if res.x[i] > 0.5]
                if best is None or w < best[0]:
                    best = (w, sol)
            db = getattr(res, "mip_dual_bound", None)
            row_lb = int(math.ceil(db - 1e-6)) if db is not None and np.isfinite(db) else 0
        else:
            row_lb = None  # infeasible for this row: no constraint on the minimum
        if row_lb is not None:
            proven = row_lb if proven == 0 else min(proven, row_lb)
    # proven = min over logical rows of each row's lower bound = lower bound on the distance
    if best is None:
        return MinWeightResult(None, None, max(proven - 1, 0), status, time.time() - t0)
    if status == "exact":
        return MinWeightResult(best[0], best[1], best[0] - 1, "exact", time.time() - t0)
    return MinWeightResult(None, best[1], max(proven - 1, 0), "timeout", time.time() - t0)


# --------------------------------------------------------------------------- #
# 1. code distance
# --------------------------------------------------------------------------- #
@dataclass
class CodeDistanceResult:
    n: int
    k: int
    d: Optional[int]
    dX: Optional[int]      # min weight of an X-type logical (CSS only)
    dZ: Optional[int]      # min weight of a Z-type logical (CSS only)
    status: str
    detail: dict = field(default_factory=dict)


def _pauli_dict_to_xz(pauli: Dict[int, str], n: int) -> np.ndarray:
    v = np.zeros(2 * n, dtype=np.uint8)
    for q, p in pauli.items():
        if p in ("X", "Y"):
            v[q] = 1
        if p in ("Z", "Y"):
            v[n + q] = 1
    return v


def code_distance_symplectic(stabs: Sequence[np.ndarray], logicals: Sequence[np.ndarray], n: int,
                             timeout_s: float = 600.0) -> CodeDistanceResult:
    """General stabilizer code. stabs/logicals are (x|z) rows of length 2n.
    Undetectable error e=(x|z): symplectic product 0 with every stabilizer and
    1 with at least one logical representative (so it acts non-trivially).
    Weight = number of qubits with (x_q, z_q) != (0,0).
    """
    S = np.array(stabs, dtype=np.uint8) % 2
    Lg = np.array(logicals, dtype=np.uint8) % 2
    # symplectic product <a,b> = a_x.b_z + a_z.b_x  ->  linear map on e: rows are (s_z | s_x)
    def swap(M):
        return np.concatenate([M[:, n:], M[:, :n]], axis=1)
    H = swap(S)
    L = swap(Lg)
    groups = [[q, n + q] for q in range(n)]
    k = (2 * n - gf2_rank(S) - gf2_rank(np.concatenate([S, Lg]))) if False else None
    ub = None
    if len(Lg):
        # any logical representative is itself a valid witness
        ws = [_weight(list(np.flatnonzero(l)), groups) for l in Lg]
        wit = list(np.flatnonzero(Lg[int(np.argmin(ws))]))
        r = min_weight_vector(H, L, weight_of=groups, witness=wit, timeout_s=timeout_s)
    else:
        r = min_weight_vector(H, L, weight_of=groups, timeout_s=timeout_s)
    kk = n - gf2_rank(S)
    return CodeDistanceResult(n=n, k=kk, d=r.weight, dX=None, dZ=None, status=r.status,
                              detail={"witness": r.witness, "proven_lb": r.proven_lower_bound,
                                      "seconds": r.seconds})


def code_distance_css(Hx: np.ndarray, Hz: np.ndarray, Lx: np.ndarray, Lz: np.ndarray,
                      timeout_s: float = 600.0) -> CodeDistanceResult:
    """CSS code: dX = min |e| with Hz e = 0, Lz e != 0 (X-type logical error);
    dZ symmetric. d = min(dX, dZ)."""
    Hx, Hz, Lx, Lz = (np.asarray(m) % 2 for m in (Hx, Hz, Lx, Lz))
    n = Hx.shape[1]
    rx = min_weight_vector(Hz, Lz, witness=list(np.flatnonzero(Lx[np.argmin(Lx.sum(1))])) if len(Lx) else None,
                           timeout_s=timeout_s)
    rz = min_weight_vector(Hx, Lx, witness=list(np.flatnonzero(Lz[np.argmin(Lz.sum(1))])) if len(Lz) else None,
                           timeout_s=timeout_s)
    k = n - gf2_rank(Hx) - gf2_rank(Hz)
    d = None if (rx.weight is None or rz.weight is None) else min(rx.weight, rz.weight)
    status = "exact" if rx.status == rz.status == "exact" else f"X:{rx.status},Z:{rz.status}"
    return CodeDistanceResult(n=n, k=k, d=d, dX=rx.weight, dZ=rz.weight, status=status,
                              detail={"wX": rx.witness, "wZ": rz.witness,
                                      "lbX": rx.proven_lower_bound, "lbZ": rz.proven_lower_bound})


def code_distance_from_patch(patch, timeout_s: float = 600.0) -> CodeDistanceResult:
    """Read stabilizers/logicals from a LightStim QECPatch (local indices)."""
    n = len(patch.data_indices)
    local = {q: i for i, q in enumerate(sorted(patch.data_indices))}
    def conv(pauli):
        return {local[q]: p for q, p in pauli.items() if q in local}
    stabs = [_pauli_dict_to_xz(conv(s["pauli"]), n) for s in patch.stabilizers]
    logs = [_pauli_dict_to_xz(conv(l["pauli"]), n) for l in patch.logical_ops]
    return code_distance_symplectic(stabs, logs, n, timeout_s=timeout_s)


# --------------------------------------------------------------------------- #
# 2. circuit-level distance
# --------------------------------------------------------------------------- #
@dataclass
class CircuitDistanceResult:
    search_weight: Optional[int]      # weight of the error found by stim's search (upper bound)
    exact_weight: Optional[int]       # z3-proven exact minimum (None if not proven)
    proven_lower_bound: int
    status: str
    n_detectors: int = 0
    n_mechanisms: int = 0
    max_hyperedge_degree: int = 0
    seconds: float = 0.0
    witness: Optional[list] = None
    search_error: Optional[str] = None


def _dem_matrices(dem: stim.DetectorErrorModel, observables: Optional[Sequence[int]] = None):
    """H (detectors x mechanisms), L (observables x mechanisms), duplicates merged."""
    dem = dem.flattened()
    cols = {}
    for inst in dem:
        if inst.type != "error":
            continue
        dets, obs = set(), set()
        for t in inst.targets_copy():
            if t.is_relative_detector_id():
                dets ^= {t.val}
            elif t.is_logical_observable_id():
                obs ^= {t.val}
        if observables is not None:
            obs &= set(observables)
        key = (frozenset(dets), frozenset(obs))
        if not dets and not obs:
            continue
        cols[key] = cols.get(key, 0) + 1
    keys = list(cols.keys())
    nd, no = dem.num_detectors, dem.num_observables
    H = np.zeros((nd, len(keys)), dtype=np.uint8)
    L = np.zeros((no, len(keys)), dtype=np.uint8)
    for j, (dets, obs) in enumerate(keys):
        for d in dets:
            H[d, j] = 1
        for o in obs:
            L[o, j] = 1
    maxdeg = max((len(d) for d, _ in keys), default=0)
    return H, L, keys, maxdeg


def circuit_distance(noisy_circuit: stim.Circuit, *, observables: Optional[Sequence[int]] = None,
                     exact: bool = True, timeout_s: float = 900.0,
                     search_kwargs: Optional[dict] = None) -> CircuitDistanceResult:
    """Circuit-level distance = minimum number of DEM error mechanisms that flip
    a (selected) logical observable without triggering any detector.
    Step 1: stim.search_for_undetectable_logical_errors -> witness (upper bound).
    Step 2: z3 proves nothing lighter exists (exact), with timeout.
    """
    t0 = time.time()
    dem = noisy_circuit.detector_error_model(decompose_errors=False, flatten_loops=True,
                                             allow_gauge_detectors=False)
    H, L, keys, maxdeg = _dem_matrices(dem, observables)
    if observables is not None:
        L = L[list(observables), :]
    # fast search first (stim default pruning; exact for graphlike DEMs, an
    # upper bound otherwise) -- the exact step below closes the gap.
    kw = dict(dont_explore_detection_event_sets_with_size_above=9999,
              dont_explore_edges_with_degree_above=9999,
              dont_explore_edges_increasing_symptom_degree=True,
              canonicalize_circuit_errors=True)
    if search_kwargs:
        kw.update(search_kwargs)
    sw = None
    try:
        errs = noisy_circuit.search_for_undetectable_logical_errors(**kw)
        if observables is not None:
            # keep only witnesses touching a selected observable: search cannot
            # target observables, so verify the found set flips one of ours
            flipped = set()
            for er in errs:
                for tgt in er.dem_error_terms:
                    if tgt.dem_target.is_logical_observable_id():
                        flipped ^= {tgt.dem_target.val}
            sw = len(errs) if flipped & set(observables) else None
        else:
            sw = len(errs)
    except Exception as ex:  # search can fail on gauge / empty
        sw = None
        errs = []
        search_error = f"{type(ex).__name__}: {str(ex)[:160]}"
    else:
        search_error = None
    res = CircuitDistanceResult(search_weight=sw, exact_weight=None, proven_lower_bound=0,
                                status="search_only", n_detectors=H.shape[0],
                                n_mechanisms=H.shape[1], max_hyperedge_degree=maxdeg)
    res.search_error = search_error
    if exact:
        r = min_weight_vector_milp(H, L, time_limit_s=timeout_s)
        res.exact_weight = r.weight
        res.proven_lower_bound = r.proven_lower_bound
        res.status = r.status
        res.witness = r.witness
        if r.status == "timeout" and r.witness is not None:
            # incumbent from the MILP is a valid witness -> upper bound
            res.search_weight = min(sw, len(r.witness)) if sw is not None else len(r.witness)
    res.seconds = time.time() - t0
    return res


# --------------------------------------------------------------------------- #
# 3. signed logical action
# --------------------------------------------------------------------------- #
@dataclass
class FlowCheckResult:
    all_ok: bool
    results: Dict[str, bool]
    unsigned_results: Dict[str, bool]


def check_flows(circuit: stim.Circuit, flows: Iterable[str]) -> FlowCheckResult:
    """flows are stim flow strings, e.g. "X0*X1 -> X0*X1 xor rec[-1]".
    A flow must hold WITH sign; we also report the unsigned result so a sign
    error is diagnosable (unsigned True, signed False => wrong sign / Pauli frame).
    """
    signed, unsigned = {}, {}
    clean = circuit.without_noise()
    for f in flows:
        fl = stim.Flow(f)
        signed[f] = clean.has_flow(fl)
        unsigned[f] = clean.has_flow(fl, unsigned=True)
    return FlowCheckResult(all(signed.values()), signed, unsigned)


def logical_flow_string(inp: Dict[int, str], out: Dict[int, str], recs: Sequence[int] = (),
                        sign: int = +1) -> str:
    def pauli(d):
        return "*".join(f"{p}{q}" for q, p in sorted(d.items())) if d else "1"
    lhs = pauli(inp)
    rhs = ("-" if sign < 0 else "") + pauli(out)
    if recs:
        rhs += " xor " + " xor ".join(f"rec[{r}]" for r in recs)
    return f"{lhs} -> {rhs}"


# --------------------------------------------------------------------------- #
# 4. noiseless sanity
# --------------------------------------------------------------------------- #
def noiseless_sanity(circuit: stim.Circuit, shots: int = 256) -> Tuple[bool, int, int]:
    clean = circuit.without_noise()
    dets, obs = clean.compile_detector_sampler().sample(shots, separate_observables=True)
    return (not dets.any()) and (not obs.any()), int(dets.sum()), int(obs.sum())


# --------------------------------------------------------------------------- #
# platform report (theory column)
# --------------------------------------------------------------------------- #
@dataclass
class PlatformReport:
    n_qubits: int
    n_2q_gates: int
    n_nonlocal_2q: int            # two-qubit gates between non-adjacent grid coords
    max_range: float
    max_degree: int               # max number of distinct partners per qubit
    two_qubit_layers: int
    verdict: Dict[str, str]


def platform_report(circuit: stim.Circuit, nn_threshold: float = 1.5) -> PlatformReport:
    coords = circuit.get_final_qubit_coordinates()
    partners: Dict[int, set] = {}
    n2 = nonloc = 0
    maxr = 0.0
    layers = 0
    for inst in circuit.without_noise().flattened():
        if inst.name in ("CX", "CZ", "CY", "SWAP", "ISWAP", "XCX", "XCZ", "YCX", "YCZ", "ZCX", "ZCZ"):
            ts = [t.value for t in inst.targets_copy()]
            layer_has = False
            for a, b in zip(ts[::2], ts[1::2]):
                n2 += 1
                layer_has = True
                partners.setdefault(a, set()).add(b)
                partners.setdefault(b, set()).add(a)
                if a in coords and b in coords and len(coords[a]) >= 2 and len(coords[b]) >= 2:
                    r = math.dist(coords[a][:2], coords[b][:2])
                    maxr = max(maxr, r)
                    if r > nn_threshold:
                        nonloc += 1
            layers += layer_has
    maxdeg = max((len(p) for p in partners.values()), default=0)
    verdict = {
        "superconducting_2D": "fits nearest-neighbour grid" if nonloc == 0 else
                              f"{nonloc} non-local 2q gates: needs SWAP routing or long-range couplers",
        "neutral_atom": "all interactions local; shuttling optional" if nonloc == 0 else
                        f"{nonloc} non-local 2q gates realised by atom moves (max range {maxr:.1f})",
        "trapped_ion": "fits all-to-all within one trap" if len(coords) <= 50 else
                       f"{len(coords)} qubits: needs multi-zone shuttling",
    }
    return PlatformReport(len(coords), n2, nonloc, maxr, maxdeg, layers, verdict)


# --------------------------------------------------------------------------- #
# self-test
# --------------------------------------------------------------------------- #
def _selftest():
    from lightstim.qec_code.surface_code.rotated.code_patch import RotatedSurfaceCode
    from lightstim.protocols.memory import MemoryExperiment
    from lightstim.noise.config import NoiseConfig
    for d in (3, 5):
        patch = RotatedSurfaceCode(distance=d)
        cd = code_distance_from_patch(patch)
        print(f"[code] rotated d={d}: n={cd.n} k={cd.k} d={cd.d} ({cd.status}, {cd.detail['seconds']:.1f}s)")
        p = 1e-3
        exp = MemoryExperiment(qec_patch=RotatedSurfaceCode(distance=d), rounds=d, basis="Z",
                               noise_params=NoiseConfig(p_1q=p, p_2q=p, p_meas=p, p_reset=p, p_idle=p),
                               noise_model="circuit_level")
        c = exp.build()
        ok, nd, no = noiseless_sanity(c)
        print(f"[sanity] d={d}: ok={ok} dets={nd} obs={no}")
        cr = circuit_distance(c, timeout_s=300)
        print(f"[circuit] d={d}: search={cr.search_weight} exact={cr.exact_weight} lb={cr.proven_lower_bound} "
              f"status={cr.status} dets={cr.n_detectors} mech={cr.n_mechanisms} maxdeg={cr.max_hyperedge_degree} "
              f"{cr.seconds:.1f}s")
        pr = platform_report(c)
        print(f"[platform] d={d}: 2q={pr.n_2q_gates} nonlocal={pr.n_nonlocal_2q} maxdeg={pr.max_degree} -> {pr.verdict['superconducting_2D']}")
    # signed flow check on a tiny circuit: CNOT propagates X0 -> X0*X1 and Z1 -> Z0*Z1, and flips sign under Z error frame
    c = stim.Circuit("CX 0 1")
    fr = check_flows(c, ["X0 -> X0*X1", "Z1 -> Z0*Z1", "X1 -> X1", "X0 -> -X0*X1"])
    print("[flows]", fr.results)


if __name__ == "__main__":
    _selftest()
