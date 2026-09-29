"""Code-level algebra for the gate: stabilizer rank, the normalizer, exact code
distance independent of any logicals the agent supplies, and validation of
declared logical flows against the code."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import stim


def gf2_rank(M: np.ndarray) -> int:
    M = (np.asarray(M) % 2).astype(np.uint8).copy()
    if M.size == 0:
        return 0
    r, (rows, cols) = 0, M.shape
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


def swap_halves(M: np.ndarray) -> np.ndarray:
    n = M.shape[1] // 2
    return np.concatenate([M[:, n:], M[:, :n]], axis=1)


def symplectic_matrix(code: dict) -> Tuple[np.ndarray, int, bool]:
    """Stabilizer generators as (x|z) rows; returns (S, n, is_css)."""
    if "Hx" in code:
        Hx = np.array(code["Hx"], dtype=np.uint8) % 2
        Hz = np.array(code["Hz"], dtype=np.uint8) % 2
        n = Hx.shape[1]
        S = np.concatenate([np.concatenate([Hx, np.zeros_like(Hx)], 1),
                            np.concatenate([np.zeros_like(Hz), Hz], 1)], 0)
        return S, n, True
    S = np.array([pauli_to_xz(s) for s in code["stabilizers"]], dtype=np.uint8)
    return S, S.shape[1] // 2, False


def commute_matrix(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Symplectic products A_i . B_j over GF(2)."""
    return (A @ swap_halves(B).T) % 2


def normalizer_basis(S: np.ndarray) -> np.ndarray:
    """All Paulis commuting with every stabilizer: nullspace of the symplectic map."""
    return nullspace_gf2(swap_halves(S))


def logical_basis(S: np.ndarray) -> np.ndarray:
    """A set of 2k Paulis that together with S span the normalizer (logical representatives)."""
    N = normalizer_basis(S)
    rS = gf2_rank(S)
    basis, cur = [], S.copy()
    for v in N:
        cand = np.vstack([cur, v[None, :]])
        if gf2_rank(cand) > gf2_rank(cur):
            basis.append(v)
            cur = cand
    return np.array(basis, dtype=np.uint8).reshape(len(basis), S.shape[1])


def code_distance(S: np.ndarray, min_weight_vector, timeout_s: float) -> dict:
    """Exact distance: minimum Pauli weight of e with e in N(S) and e not in <S>.
    Implemented as: e commutes with every row of S and anticommutes with some
    element of N(S) (the symplectic complement of N(S) is <S>). Agent-supplied
    logicals are not used."""
    n = S.shape[1] // 2
    N = normalizer_basis(S)
    H = swap_halves(S)            # rows h with h.e = <s,e>
    L = swap_halves(N)            # rows with L.e = <nu,e>
    groups = [[q, n + q] for q in range(n)]
    r = min_weight_vector(H, L, weight_of=groups, timeout_s=timeout_s)
    return {"d": r.weight, "status": r.status, "witness": r.witness, "proven_lb": r.proven_lower_bound}


# ------------------------------------------------------------------ flows vs code
def _flow_paulis(flow: stim.Flow):
    return flow.input_copy(), flow.output_copy(), list(flow.measurements_copy())


def _block_vectors(ps: stim.PauliString, blocks: List[List[int]], n: int) -> Tuple[List[np.ndarray], bool]:
    """Split a global Pauli string into per-block local (x|z) vectors; also report
    whether it acts outside the blocks."""
    idx = {}
    for b, qs in enumerate(blocks):
        for loc, q in enumerate(qs):
            idx[q] = (b, loc)
    vecs = [np.zeros(2 * n, dtype=np.uint8) for _ in blocks]
    outside = False
    for q in range(len(ps)):
        p = ps[q]
        if p == 0:
            continue
        if q not in idx:
            outside = True
            continue
        b, loc = idx[q]
        if p in (1, 2):
            vecs[b][loc] = 1
        if p in (2, 3):
            vecs[b][n + loc] = 1
    return vecs, outside


def check_logical_flows(flows: Sequence[str], S: np.ndarray, blocks: List[List[int]], kind: str) -> dict:
    """Every Pauli in every flow must act only on block data qubits and be, block by
    block, in the normalizer. For kind 'logical_gate' and 'memory', inputs and outputs
    must each generate the full logical group of all blocks (2k per block); for
    'logical_measurement', at least one flow must map a non-trivial logical input
    to measurement records."""
    n = S.shape[1] // 2
    k = n - gf2_rank(S)
    problems = []
    ins, outs, measured = [], [], False
    for f in flows:
        try:
            fl = stim.Flow(f)
        except Exception as ex:
            problems.append(f"unparsable flow {f!r}: {ex}")
            continue
        pin, pout, recs = _flow_paulis(fl)
        vec_in, out_in = _block_vectors(pin, blocks, n)
        vec_out, out_out = _block_vectors(pout, blocks, n)
        if out_in or out_out:
            problems.append(f"flow {f!r} acts on qubits outside the code blocks")
            continue
        for v in vec_in + vec_out:
            if commute_matrix(v[None, :], S).any():
                problems.append(f"flow {f!r} contains an operator that is not a logical operator of the code "
                                f"(anticommutes with a stabilizer)")
                break
        ins.append(np.concatenate(vec_in))
        outs.append(np.concatenate(vec_out))
        if recs and any(v.any() for v in vec_in):
            measured = True
    m = len(blocks)
    Sfull = np.zeros((m * S.shape[0], m * 2 * n), dtype=np.uint8)
    for b in range(m):
        Sfull[b * S.shape[0]:(b + 1) * S.shape[0], b * 2 * n:(b + 1) * 2 * n] = S
    base = gf2_rank(Sfull)

    def logical_rank(vs):
        if not vs:
            return 0
        return gf2_rank(np.vstack([Sfull, np.array(vs)])) - base

    rin, rout = logical_rank(ins), logical_rank(outs)
    need = 2 * k * m
    res = {"k": k, "blocks": m, "logical_rank_inputs": rin, "logical_rank_outputs": rout, "needed": need,
           "measured_logical": measured}
    if kind in ("logical_gate", "memory"):
        if rin < need or rout < need:
            problems.append(f"declared flows do not generate the full logical group: input rank {rin}, "
                            f"output rank {rout}, needed {need} (2k per block)")
    elif kind == "logical_measurement":
        if not measured:
            problems.append("no declared flow maps a non-trivial logical operator to measurement records")
    res["problems"] = problems
    res["pass"] = not problems
    return res
