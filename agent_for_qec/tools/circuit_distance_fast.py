"""Faster exact circuit-level distance (Phase 1a). Does not change verify_stack.

Problem: min |e| over DEM mechanisms with H e = 0 and L_j e = 1 for some j.

Tools here, all exact or explicitly bounded:

  * projected_lower_bound(...)  -- (a) detector-subset relaxation. For ANY subset S
        of detectors, dropping the constraints of detectors outside S can only
        lower the minimum, so min over the projected problem is a valid LOWER
        bound. Mechanisms are projected to (dets in S, observables), duplicates
        merged (weight 1 each), mechanisms whose projection is empty dropped (they
        can never help). For CSS circuits S = "detectors flipped by X errors on
        data qubits" is the Z-type half; the projected DEM is then usually
        graphlike, and the graphlike problem is solved EXACTLY by BFS on the
        parity-lifted graph (see _graphlike_min). Otherwise the projected problem
        goes to the MILP.
  * milp_feasibility(...)       -- (b) "is there a solution of weight <= k?" as a
        MILP with sum e <= k and objective 0 (infeasible = proof of > k). Reports
        the solver status; on time-out the answer is unknown.
  * upper bound: first the relaxation's optimal solution lifted back to the
        original mechanisms (each merged class represented by its member with
        the fewest detectors outside S); if that is a full solution its weight
        equals the relaxed optimum and the distance is exact at once. Fallbacks:
        stim.search_for_undetectable_logical_errors, then the MILP. Every
        witness is re-checked against H, L.
  * fast_circuit_distance(...)  -- LB from the relaxations, UB from a witness; if
        LB == UB the distance is exact (status 'exact'); else it tries
        milp_feasibility(ub-1) on the full problem; on timeout it reports
        status 'bounds' with the proven interval [lb, ub] (never a fake exact).

Soundness of the graphlike exact routine: in a graphlike DEM every mechanism is
an edge (1 or 2 detectors; 1-detector edges go to a virtual boundary node B;
0-detector mechanisms that flip the observable are self-loops of weight 1). A
solution is an edge multiset with even degree at every real detector and odd
observable parity; it decomposes into closed walks through real nodes / B, one
of which has odd parity. Conversely, a closed walk from (v,0) to (v,1) in the
parity-lifted graph gives, after cancelling repeated edges, a solution of no
larger weight. So min_v dist((v,0),(v,1)) is the exact minimum.

  PYTHONPATH=. python agent_for_qec/tools/circuit_distance_fast.py   (self-test)
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import stim

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_stack import _dem_matrices  # noqa: E402


# --------------------------------------------------------------------------- #
# detector classification
# --------------------------------------------------------------------------- #
_NOISE = {"DEPOLARIZE1", "DEPOLARIZE2", "X_ERROR", "Y_ERROR", "Z_ERROR", "PAULI_CHANNEL_1",
          "PAULI_CHANNEL_2", "E", "ELSE_CORRELATED_ERROR", "HERALDED_ERASE",
          "HERALDED_PAULI_CHANNEL_1", "CORRELATED_ERROR"}


def sensitive_detectors(circuit: stim.Circuit, qubits: Sequence[int], pauli: str = "X") -> Set[int]:
    """Detectors flipped by a `pauli` error on any of `qubits` after any TICK
    (and at the very start). For CSS SE circuits with qubits = data qubits and
    pauli='X' this is the set of Z-type detectors."""
    clean = circuit.without_noise().flattened()
    op = {"X": "X_ERROR", "Y": "Y_ERROR", "Z": "Z_ERROR"}[pauli]
    qs = sorted(set(int(q) for q in qubits))
    out = stim.Circuit()
    out.append(op, qs, 0.01)
    for inst in clean:
        out.append(inst)
        if inst.name == "TICK":
            out.append(op, qs, 0.01)
    dem = out.detector_error_model(decompose_errors=False, flatten_loops=True,
                                   allow_gauge_detectors=True, approximate_disjoint_errors=True)
    s: Set[int] = set()
    for inst in dem.flattened():
        if inst.type == "error":
            for t in inst.targets_copy():
                if t.is_relative_detector_id():
                    s.add(t.val)
    return s


# --------------------------------------------------------------------------- #
# relaxation
# --------------------------------------------------------------------------- #
def project(H: np.ndarray, L: np.ndarray, S: Sequence[int]) -> Tuple[np.ndarray, np.ndarray, List[int]]:
    """Keep rows S of H; merge duplicate columns; drop columns that are zero in
    both the kept H rows and L. Returns (Hs, Ls, representative original column).
    The representative of a merged class is the column with the fewest detectors
    outside S, so a projected witness often lifts to a full witness unchanged."""
    S = sorted(S)
    Hs = H[S, :]
    outside = H.sum(0).astype(np.int64) - Hs.sum(0).astype(np.int64)
    seen: Dict[bytes, int] = {}
    keep: List[int] = []
    for j in range(H.shape[1]):
        if not Hs[:, j].any() and not L[:, j].any():
            continue
        key = Hs[:, j].tobytes() + b"|" + L[:, j].tobytes()
        if key in seen:
            i = seen[key]
            if outside[j] < outside[keep[i]]:
                keep[i] = j
            continue
        seen[key] = len(keep)
        keep.append(j)
    return Hs[:, keep], L[:, keep], keep


def _graphlike_min(Hs: np.ndarray, Lrow: np.ndarray) -> Tuple[Optional[int], Optional[List[int]]]:
    """Exact min weight of e with Hs e = 0, Lrow e = 1 when every column of Hs has
    <= 2 ones. Returns (weight, column list) or (None, None) if no solution."""
    m, n = Hs.shape
    B = m                      # boundary node
    adj: List[List[Tuple[int, int, int]]] = [[] for _ in range(m + 1)]
    best = None
    best_cols = None
    for j in range(n):
        dets = list(np.flatnonzero(Hs[:, j]))
        par = int(Lrow[j])
        if len(dets) == 0:
            if par and (best is None or 1 < best):
                best, best_cols = 1, [j]
            continue
        if len(dets) == 1:
            a, b = dets[0], B
        else:
            a, b = dets
        adj[a].append((b, par, j))
        adj[b].append((a, par, j))
    if best == 1:
        return 1, best_cols
    # BFS in the parity-lifted graph from every node v: dist((v,0) -> (v,1)).
    for v in range(m + 1):
        if not adj[v]:
            continue
        N = m + 1
        dist = np.full(2 * N, -1, dtype=np.int64)
        prev: Dict[int, Tuple[int, int]] = {}
        s = 2 * v
        dist[s] = 0
        dq = deque([s])
        tgt = 2 * v + 1
        limit = best if best is not None else None
        while dq:
            x = dq.popleft()
            if x == tgt:
                break
            if limit is not None and dist[x] + 1 >= limit:
                break
            node, p = divmod(x, 2)
            for (w, par, j) in adj[node]:
                y = 2 * w + (p ^ par)
                if dist[y] < 0:
                    dist[y] = dist[x] + 1
                    prev[y] = (x, j)
                    dq.append(y)
        if dist[tgt] >= 0 and (best is None or dist[tgt] < best):
            cols = []
            y = tgt
            while y != s:
                x, j = prev[y]
                cols.append(j)
                y = x
            # cancel repeated edges
            cnt: Dict[int, int] = {}
            for j in cols:
                cnt[j] = cnt.get(j, 0) ^ 1
            best_cols = sorted(j for j, c in cnt.items() if c)
            best = len(best_cols)
    return best, best_cols


def _check(H: np.ndarray, L: np.ndarray, cols: Sequence[int]) -> bool:
    e = np.zeros(H.shape[1], dtype=np.int64)
    e[list(cols)] = 1
    return (not ((H.astype(np.int64) @ e) % 2).any()) and bool(((L.astype(np.int64) @ e) % 2).any())


@dataclass
class BoundResult:
    lb: int                       # proven: every logical error has weight >= lb
    method: str
    detail: dict = field(default_factory=dict)
    seconds: float = 0.0


def projected_lower_bound(H: np.ndarray, L: np.ndarray, S: Sequence[int], *,
                          milp_time_s: float = 600.0) -> BoundResult:
    """Exact min of the S-projected problem (a valid lower bound for the full one)."""
    t0 = time.time()
    Hs, Ls, keep = project(H, L, S)
    maxdeg = int(Hs.sum(0).max()) if Hs.shape[1] else 0
    rows = [j for j in range(Ls.shape[0]) if Ls[j].any()]
    det = {"S": len(S), "cols": Hs.shape[1], "maxdeg": maxdeg}
    if not rows:
        return BoundResult(10 ** 9, "no-logical", det, time.time() - t0)
    if maxdeg <= 2:
        best, bcols = 10 ** 9, None
        for j in rows:
            w, cols = _graphlike_min(Hs, Ls[j])
            if w is not None and w < best:
                best, bcols = w, cols
        # witness of the projected problem, in ORIGINAL column indices
        det["lifted"] = [keep[c] for c in bcols] if bcols is not None else None
        return BoundResult(best, "graphlike-bfs", det, time.time() - t0)
    # NB: not verify_stack.min_weight_vector_milp -- its timeout path can
    # overstate the bound (rows skipped / unknown rows ignored); see _milp_min.
    w, cols, lbnd, status = _milp_min(Hs, Ls, time_limit_s=milp_time_s)
    det["milp_status"] = status
    if status == "exact":
        det["lifted"] = [keep[c] for c in cols] if cols is not None else None
        return BoundResult(w, "projected-milp", det, time.time() - t0)
    det["lifted"] = [keep[c] for c in cols] if cols is not None else None
    return BoundResult(max(1, lbnd), "projected-milp-dualbound", det, time.time() - t0)


def _milp_min(H: np.ndarray, L: np.ndarray, time_limit_s: float = 600.0):
    """min |e| s.t. H e = 0, L e != 0, one MILP per nonzero row of L.
    Returns (weight|None, cols|None, proven_lb, status). proven_lb is the min
    over ALL nonzero rows of that row's proven bound (exact optimum, HiGHS dual
    bound on timeout, +inf if infeasible); a row that was not solved or ended
    with any other status counts as bound 1 (nothing proven). status 'exact'
    only if every row finished (optimal or infeasible)."""
    from scipy.optimize import milp, LinearConstraint, Bounds
    from scipy.sparse import coo_matrix, hstack, csr_matrix, vstack
    t0 = time.time()
    H = np.asarray(H) % 2
    L = np.asarray(L) % 2
    m, n = H.shape
    rows = [j for j in range(L.shape[0]) if L[j].any()]
    best, best_cols = None, None
    row_lbs = []
    all_done = True
    for j in rows:
        remaining = time_limit_s - (time.time() - t0)
        if remaining <= 1:
            row_lbs.append(1); all_done = False
            continue
        nv = n + m + 1
        A1 = hstack([coo_matrix(H.astype(float)), -2.0 * _eye(m), csr_matrix((m, 1))])
        A2 = hstack([csr_matrix(L[j].astype(float)[None, :]), csr_matrix((1, m)), csr_matrix([[-2.0]])])
        A = vstack([A1, A2]).tocsr()
        lo = np.concatenate([np.zeros(m), [1.0]])
        hi = lo.copy()
        ub = np.concatenate([np.ones(n), np.maximum(1, np.ceil(H.sum(1) / 2.0)),
                             [max(1, math.ceil(L[j].sum() / 2.0))]])
        c = np.concatenate([np.ones(n), np.zeros(m + 1)])
        res = milp(c=c, constraints=LinearConstraint(A, lo, hi), integrality=np.ones(nv),
                   bounds=Bounds(np.zeros(nv), ub),
                   options={"time_limit": max(1.0, remaining), "disp": False})
        if res.status == 0 and res.x is not None:
            w = int(round(res.fun))
            sol = [i for i in range(n) if res.x[i] > 0.5]
            row_lbs.append(w)
            if best is None or w < best:
                best, best_cols = w, sol
        elif res.status == 2:
            row_lbs.append(10 ** 9)
        else:
            all_done = False
            db = getattr(res, "mip_dual_bound", None)
            row_lbs.append(int(math.ceil(db - 1e-6)) if (res.status == 1 and db is not None
                                                          and np.isfinite(db)) else 1)
            if res.x is not None:
                sol = [i for i in range(n) if res.x[i] > 0.5]
                if best is None or len(sol) < best:
                    best, best_cols = len(sol), sol
    lb = min(row_lbs) if row_lbs else 10 ** 9
    if all_done:
        return best, best_cols, (best if best is not None else 10 ** 9), "exact"
    return None, best_cols, lb, "timeout"


# --------------------------------------------------------------------------- #
# (b) MILP feasibility: exists e with weight <= k ?
# --------------------------------------------------------------------------- #
def milp_feasibility(H: np.ndarray, L: np.ndarray, k: int, time_limit_s: float = 600.0,
                     threads: Optional[int] = None) -> Tuple[str, Optional[List[int]], float]:
    """Returns ('infeasible'|'feasible'|'timeout', witness, seconds). One MILP per
    logical row j: H e = 2 s, L_j e = 2 t + 1, sum e <= k, objective 0."""
    from scipy.optimize import milp, LinearConstraint, Bounds
    from scipy.sparse import coo_matrix, hstack, csr_matrix, vstack
    t0 = time.time()
    H = np.asarray(H) % 2
    L = np.asarray(L) % 2
    m, n = H.shape
    out = "infeasible"
    for j in [j for j in range(L.shape[0]) if L[j].any()]:
        remaining = time_limit_s - (time.time() - t0)
        if remaining <= 1:
            return "timeout", None, time.time() - t0
        nv = n + m + 1
        Hc = coo_matrix(H.astype(float))
        A1 = hstack([Hc, -2.0 * _eye(m), csr_matrix((m, 1))])
        A2 = hstack([csr_matrix(L[j].astype(float)[None, :]), csr_matrix((1, m)), csr_matrix([[-2.0]])])
        A3 = hstack([csr_matrix(np.ones((1, n))), csr_matrix((1, m + 1))])
        A = vstack([A1, A2, A3]).tocsr()
        lo = np.concatenate([np.zeros(m), [1.0], [-np.inf]])
        hi = np.concatenate([np.zeros(m), [1.0], [float(k)]])
        ub = np.concatenate([np.ones(n), np.maximum(1, np.ceil(H.sum(1) / 2.0)),
                             [max(1, math.ceil(L[j].sum() / 2.0))]])
        # objective: minimise weight anyway (helps HiGHS find/refute quickly)
        c = np.concatenate([np.ones(n), np.zeros(m + 1)])
        opts = {"time_limit": max(1.0, remaining), "disp": False}
        res = milp(c=c, constraints=LinearConstraint(A, lo, hi), integrality=np.ones(nv),
                   bounds=Bounds(np.zeros(nv), ub), options=opts)
        if res.status == 0 and res.x is not None:
            return "feasible", [i for i in range(n) if res.x[i] > 0.5], time.time() - t0
        if res.status == 1:
            if res.x is not None:
                return "feasible", [i for i in range(n) if res.x[i] > 0.5], time.time() - t0
            return "timeout", None, time.time() - t0
        if res.status != 2:
            return f"error{res.status}", None, time.time() - t0
    return out, None, time.time() - t0


def _eye(m):
    from scipy.sparse import identity
    return identity(m, format="csr")


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
@dataclass
class FastDistanceResult:
    lb: int
    ub: Optional[int]
    status: str                      # 'exact' | 'bounds'
    lb_method: str
    ub_method: str
    n_detectors: int
    n_mechanisms: int
    seconds: float
    detail: dict = field(default_factory=dict)

    @property
    def exact(self) -> Optional[int]:
        return self.lb if self.status == "exact" else None


def stim_upper_bound(circuit: stim.Circuit, H, L, keys, max_size: int = 6) -> Tuple[Optional[int], Optional[List[int]]]:
    """Witness from stim's search, mapped back to merged DEM columns and re-checked."""
    try:
        errs = circuit.search_for_undetectable_logical_errors(
            dont_explore_detection_event_sets_with_size_above=max_size,
            dont_explore_edges_with_degree_above=max_size,
            dont_explore_edges_increasing_symptom_degree=False,
            canonicalize_circuit_errors=True)
    except Exception:
        return None, None
    col = {k: i for i, k in enumerate(keys)}
    cols = []
    for er in errs:
        dets, obs = set(), set()
        for tgt in er.dem_error_terms:
            t = tgt.dem_target
            if t.is_relative_detector_id():
                dets ^= {t.val}
            elif t.is_logical_observable_id():
                obs ^= {t.val}
        k = (frozenset(dets), frozenset(obs))
        if k not in col:
            return len(errs), None
        cols.append(col[k])
    cnt: Dict[int, int] = {}
    for c in cols:
        cnt[c] = cnt.get(c, 0) ^ 1
    cols = [c for c, v in cnt.items() if v]
    if not _check(H, L, cols):
        return None, None
    return len(cols), cols


def fast_circuit_distance(circuit: stim.Circuit, *, data_qubits: Optional[Sequence[int]] = None,
                          observables: Optional[Sequence[int]] = None,
                          extra_subsets: Sequence[Sequence[int]] = (),
                          ub_hint: Optional[int] = None, use_stim_search: bool = True,
                          milp_time_s: float = 1800.0, verbose: bool = False) -> FastDistanceResult:
    t0 = time.time()
    dem = circuit.detector_error_model(decompose_errors=False, flatten_loops=True,
                                       allow_gauge_detectors=False)
    H, L, keys, _ = _dem_matrices(dem, observables)
    if observables is not None:
        L = L[list(observables), :]
    detail: dict = {}
    ub, wit, ub_method = None, None, "none"
    # ---------- lower bounds from relaxations (+ lifted witnesses as upper bounds)
    subsets = []
    if data_qubits is not None:
        subsets.append(("Xdata-sensitive", sorted(sensitive_detectors(circuit, data_qubits, "X"))))
        subsets.append(("Zdata-sensitive", sorted(sensitive_detectors(circuit, data_qubits, "Z"))))
    for i, s in enumerate(extra_subsets):
        subsets.append((f"extra{i}", sorted(s)))
    # cheapest relaxations first: graphlike projections (BFS) before hyperedge
    # ones (MILP); among equals, the larger subset (usually the tighter bound)
    def _cost(item):
        Hs, _, _ = project(H, L, item[1]) if item[1] else (np.zeros((0, 0)), None, None)
        md = int(Hs.sum(0).max()) if Hs.size else 99
        return (md > 2, md, -len(item[1]))
    subsets.sort(key=_cost)
    # Per observable row j: lb_j = max over subsets of the projected optimum for
    # row j alone (every error flipping obs j has weight >= lb_j); the distance
    # is min_j of the row minima, so lb = min_j lb_j. A lifted witness for row j
    # that passes the full check is a global upper bound.
    rows = [j for j in range(L.shape[0]) if L[j].any()]
    row_lb = {}
    row_how = {}
    for j in rows:
        Lj = L[[j], :]
        row_lb[j], row_how[j] = 1, "trivial"
        for name, S in subsets:
            if not S:
                continue
            if ub is not None and row_lb[j] >= ub:
                break
            r = projected_lower_bound(H, Lj, S, milp_time_s=milp_time_s)
            lifted = r.detail.pop("lifted", None)
            lift_ok = lifted is not None and _check(H, Lj, lifted)
            detail[f"relax:{name}:obs{j}"] = dict(lb=r.lb, method=r.method, sec=round(r.seconds, 1),
                                                  lift_ok=lift_ok, lift_w=len(lifted) if lift_ok else None,
                                                  **r.detail)
            if verbose:
                print(f"    relax {name} obs{j}: {detail[f'relax:{name}:obs{j}']}", flush=True)
            if r.lb > row_lb[j]:
                row_lb[j], row_how[j] = r.lb, f"relax:{name}:{r.method}"
            if lift_ok and (ub is None or len(lifted) < ub):
                ub, wit, ub_method = len(lifted), sorted(lifted), f"lifted:{name}:obs{j}"
    if rows:
        jmin = min(rows, key=lambda j: row_lb[j])
        lb, lb_method = row_lb[jmin], row_how[jmin]
    else:
        lb, lb_method = 10 ** 9, "no-logical"
    detail["row_lb"] = row_lb
    # ---------- upper bound from stim's search if the lifted witnesses did not close it
    if use_stim_search and (ub is None or lb < ub):
        w, cols = stim_upper_bound(circuit, H, L, keys)
        if cols is not None and (ub is None or w < ub):
            ub, wit, ub_method = w, cols, "stim-search"
    detail["t_ub"] = round(time.time() - t0, 1)
    # ---------- close the gap with MILP feasibility on the full problem
    if ub is None or lb < ub:
        target = (ub - 1) if ub is not None else (ub_hint - 1 if ub_hint else None)
        while target is not None and target >= lb:
            remaining = milp_time_s - (time.time() - t0)
            st, sol, sec = milp_feasibility(H, L, target, time_limit_s=max(1.0, remaining))
            detail.setdefault("feas", []).append((target, st, round(sec, 1)))
            if verbose:
                print(f"    feasibility(<= {target}): {st} {sec:.0f}s", flush=True)
            if st == "infeasible":
                lb = target + 1
                lb_method = "full-milp-feasibility"
                break
            if st == "feasible" and sol is not None and _check(H, L, sol):
                ub, wit, ub_method = len(sol), sol, "milp-feasible"
                target = ub - 1
                continue
            break
    status = "exact" if (ub is not None and lb == ub) else "bounds"
    detail["witness"] = wit
    return FastDistanceResult(lb=lb, ub=ub, status=status, lb_method=lb_method, ub_method=ub_method,
                              n_detectors=H.shape[0], n_mechanisms=H.shape[1],
                              seconds=time.time() - t0, detail=detail)


def _selftest():
    from lightstim.qec_code.surface_code.rotated.code_patch import RotatedSurfaceCode
    from lightstim.qec_code.surface_code.rotated.SE_block import RotatedSurfaceCodeExtractionBlock
    from lightstim.protocols.memory import MemoryExperiment
    from lightstim.noise.config import NoiseConfig
    p = 1e-3
    nc = NoiseConfig(p_1q=p, p_2q=p, p_meas=p, p_reset=p, p_idle=p)
    for d, sched, basis in ((3, 'perpendicular', 'Z'), (3, 'swapped', 'Z'), (5, 'perpendicular', 'X'),
                            (5, 'parallel', 'X')):
        exp = MemoryExperiment(qec_patch=RotatedSurfaceCode(distance=d), rounds=d, basis=basis,
                               noise_params=nc, noise_model='circuit_level',
                               extraction_block_class=RotatedSurfaceCodeExtractionBlock,
                               se_block_kwargs={'scheduling': sched})
        c = exp.build()
        data = [exp.system.index_map[x] for x in exp.system.data_coords]
        r = fast_circuit_distance(c, data_qubits=data, milp_time_s=300)
        print(f"[fast] d={d} {sched} {basis}: lb={r.lb} ub={r.ub} {r.status} lb_by={r.lb_method} "
              f"ub_by={r.ub_method} {r.seconds:.1f}s", flush=True)


if __name__ == "__main__":
    _selftest()
