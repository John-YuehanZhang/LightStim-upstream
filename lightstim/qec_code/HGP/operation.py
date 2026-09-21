"""Fault-tolerant logical operations on hypergraph-product (HGP) patches.

This module follows Xu et al., "Fast and Parallelizable Logical Computation
with Homological Product Codes" (arXiv:2407.18490), whose Table I lists the
HGP gadgets used for logical computation.  Each gadget is transcribed here
with its paper rule; deviations from the paper are stated explicitly.

Fold-transversal H-SWAP (Table I; introduced in Ref. [27] of the paper,
Quintavalle-Webster-Vasmer, under the fold-transversal framework of Ref. [26],
Breuckmann-Burton)
--------------------------------------------------------------------------
Paper rule.  For a *symmetric* HGP code, i.e. one whose two base codes are
the same matrix (H1 = H2), with physical qubits ``Q_{i,j}`` at coordinates
``O = [n1] x [n2]  ∪  {n1+1..2n1-k1} x {n2+1..2n2-k2}`` and ``O+`` the upper
blocks ``{(i,j) ∈ O | j > i}``:

    physical:  (H^{⊗n}) · ⊗_{(i,j)∈O+} SWAP(Q_{i,j}, Q_{j,i})
    logical:   (H̄^{⊗k}) · ⊗_{(i,j)∈Ō+} SWAP(Q̄_{i,j}, Q̄_{j,i})

Time cost O(1); no ancilla.  The gate is a code automorphism composed with a
transversal single-qubit layer, so a single physical fault stays a single
physical error (up to the two qubits of one SWAP, see ``noisy_swap``).

Transcription into LightStim's HGPCode.  ``HGPCode`` stores the two data
sectors as ``vv_qubits[(bit_1, bit_2)]`` (V1 x V2) and
``cc_qubits[(check_1, check_2)]`` (C1 x C2).  The fold is the transposition
``(bit_1, bit_2) ↔ (bit_2, bit_1)`` on V1 x V2 and
``(check_1, check_2) ↔ (check_2, check_1)`` on C1 x C2; both sectors are
square exactly when H1 = H2.  Under H^{⊗n} followed by this transposition,

    X check (bit_1, check_2)  ↦  Z check (check_1 = check_2, bit_2 = bit_1)
    Z check (check_1, bit_2)  ↦  X check (bit_1 = bit_2, check_2 = check_1)

so the stabilizer generators are permuted among themselves (sign +1), and in
the canonical logical basis of ``HGPCode._build_logical_operators`` (X̄ =
kernel(H1) x pivot(H2), Z̄ = pivot(H1) x kernel(H2) on V1 x V2; the dual
pattern on C1 x C2)

    X̄ of pair (l1, l2)  ↦  +Z̄ of pair (l2, l1)
    Z̄ of pair (l1, l2)  ↦  +X̄ of pair (l2, l1)

within the same sector, i.e. exactly H̄ on every logical qubit composed with
the swap of the logical pairs ``(l1, l2) ↔ (l2, l1)``.  Pairs with
``l1 == l2`` (the logical diagonal) receive a plain H̄.  These identities hold
exactly, not only modulo stabilizers, because the supports map onto each
other qubit by qubit; the tests check this with stim tableaux.

Beyond the paper.  The paper assumes full-rank base codes, in which case all
logical qubits live on V1 x V2.  ``HGPCode`` also registers logical pairs on
C1 x C2 when the seed has redundant checks (e.g. the [[18,2,3]] toric
instance); the same derivation applies to that sector, and the tests cover
it.  This is a statement about the code, not a change to the gadget.

Deviations / choices.
* The SWAP layer is emitted as physical ``SWAP`` gates, the same way
  ``UnrotatedSurfaceCodeLogicalOpSet.fold_transversal_hadamard`` does.  With
  ``noisy_swap=True`` (default) they take the two-qubit gate noise of the
  noise model, so a single fault can produce a weight-two error on one mirror
  pair.  ``noisy_swap=False`` tags the SWAP layer noiseless, modelling the
  paper's picture of the fold as a relabelling of qubits (Sec. VII: qubit
  movement) with no gate error.
* The H layer is emitted before the SWAP layer.  The two commute (H^{⊗n} is
  invariant under any qubit permutation), so the operator equals the paper's
  product in either order.
* No Pauli frame is involved: the logical action is exactly H̄^{⊗k} · SWAP,
  with no residual logical Pauli.

Homomorphic CNOT (Table I "inter-block CNOTs"; Definition 3, Algorithm 1
step 4, Section V A)
--------------------------------------------------------------------------
Paper rule.  Between a data code ``Q`` and an ancilla ``Q'`` obtained by
puncturing a base code of ``Q`` (see :mod:`.homomorphic`), physical
transversal CNOTs between the qubits of ``Q`` and ``Q'`` that share a
coordinate implement logical transversal CNOTs between the logical qubits
that share a coordinate (Eq. 35-36); the deleted logical qubits are
untouched.  ``Q`` controls when the horizontal code was punctured; ``Q'``
controls when the vertical code was punctured (Sec. V A).  With nothing
punctured this is the standard transversal CNOT between two identical
blocks.  Time cost O(1).

Transcription.  ``homomorphic_cnot(builder, data_patch, ancilla_patch)``
takes the pairs from :func:`homomorphic_qubit_pairs` (coordinates, not
index order: the ancilla is smaller than the data patch, so the inherited
``transversal_cnot`` and its sorted-index pairing do not apply) and emits
one ``CX`` layer in the direction fixed by the puncture axis.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple, Type

import numpy as np
import stim

from lightstim.ir.builder import CircuitBuilder
from lightstim.ir.operation import CSSLogicalOpSet
from lightstim.ir.qec_patch import QECPatch
from lightstim.qec_code._operation_utils import require_global_patch

from .code_patch import HGPCode
from .homomorphic import (
    PuncturedHGPCode,
    homomorphic_cnot_circuit,
    homomorphic_qubit_pairs,
)


IndexPair = Tuple[int, int]


# ---------------------------------------------------------------------------
# Geometry of the fold
# ---------------------------------------------------------------------------

def is_symmetric_hgp(patch: HGPCode) -> bool:
    """True if the two base codes of ``patch`` are the same matrix (H1 = H2).

    This is matrix equality, not code equivalence: a row-permuted copy of H1
    defines the same classical code but is rejected, because the fold pairs
    ``(check_1, check_2) ↔ (check_2, check_1)`` on C1 x C2 and the mapping of
    X checks onto Z checks only hold entry by entry.
    """
    dense_1 = patch.h1.to_dense()
    dense_2 = patch.h2.to_dense()
    return dense_1.shape == dense_2.shape and bool(np.array_equal(dense_1, dense_2))


def _require_symmetric(patch: HGPCode) -> None:
    if not is_symmetric_hgp(patch):
        raise ValueError(
            "The fold-transversal H-SWAP needs a symmetric HGP code (H1 = H2, "
            "Xu et al. Table I); this patch has "
            f"H1 of shape {patch.h1.shape} and H2 of shape {patch.h2.shape}"
            + ("" if patch.h1.shape != patch.h2.shape else " with different entries")
            + "."
        )


def _global_index(builder: Optional[CircuitBuilder], patch: HGPCode, local_index: int) -> int:
    """Map a local data index of ``patch`` to its index in the builder's system.

    ``QECSystem.add_patch`` deep-copies the patch and shifts its coordinates,
    so the returned patch's ``qubit_coords`` are global coordinates while its
    ``vv_qubits``/``cc_qubits`` values stay local.  The system's ``index_map``
    turns a global coordinate into the global qubit index.  With
    ``builder=None`` the local index is returned unchanged.
    """
    if builder is None:
        return int(local_index)
    return int(builder.system.index_map[patch.qubit_coords[local_index]])


def _check_patch(patch: HGPCode, builder: Optional[CircuitBuilder]) -> None:
    """Shared validation of the public helpers and the op-set method."""
    if not isinstance(patch, HGPCode):
        raise TypeError(f"Expected an HGPCode patch, got {type(patch).__name__}.")
    if builder is not None:
        # A local (unregistered) patch carries local coordinates; looking them
        # up in the system's index_map would silently return whatever patch
        # happens to cover those coordinates.
        require_global_patch(builder, patch)
    _require_symmetric(patch)


def fold_mirror_pairs(patch: HGPCode, builder: Optional[CircuitBuilder] = None) -> List[IndexPair]:
    """Return the SWAP pairs of the fold, ``(Q_{i,j}, Q_{j,i})`` for ``j > i``.

    V1 x V2 pairs come first (ordered by ``(bit_1, bit_2)``), then C1 x C2
    pairs (ordered by ``(check_1, check_2)``).  Diagonal qubits are not
    listed.  Indices are global when ``builder`` is given (``patch`` must then
    be the global patch returned by ``QECSystem.add_patch``), local otherwise.
    """
    _check_patch(patch, builder)
    pairs: List[IndexPair] = []
    for sector in (patch.vv_qubits, patch.cc_qubits):
        for (a, b), local_index in sorted(sector.items()):
            if b > a:
                mirror = sector[(b, a)]
                pairs.append(
                    (_global_index(builder, patch, local_index), _global_index(builder, patch, mirror))
                )
    return pairs


def fold_diagonal_qubits(patch: HGPCode, builder: Optional[CircuitBuilder] = None) -> List[int]:
    """Return the data qubits fixed by the fold, ``Q_{i,i}`` of both sectors.

    Indices are global when ``builder`` is given (global patch required),
    local otherwise.
    """
    _check_patch(patch, builder)
    diagonal: List[int] = []
    for sector in (patch.vv_qubits, patch.cc_qubits):
        for (a, b), local_index in sorted(sector.items()):
            if a == b:
                diagonal.append(_global_index(builder, patch, local_index))
    return diagonal


def fold_logical_permutation(patch: HGPCode) -> Dict[int, int]:
    """Return ``{logical_id: mirror logical_id}`` induced by the fold.

    The canonical pair with ``seed_indices=(l1, l2)`` in a sector is mapped
    to the pair ``(l2, l1)`` of the same sector; diagonal pairs map to
    themselves.  Read from ``patch.logical_pairs`` (``HGPCode`` metadata).
    """
    _check_patch(patch, None)
    by_key = {
        (record["sector"], tuple(record["seed_indices"])): int(record["logical_id"])
        for record in patch.logical_pairs
    }
    permutation: Dict[int, int] = {}
    for record in patch.logical_pairs:
        l1, l2 = record["seed_indices"]
        key = (record["sector"], (l2, l1))
        if key not in by_key:
            raise RuntimeError(
                f"Logical pair {record['logical_id']} ({record['sector']}, seed {(l1, l2)}) "
                f"has no mirror pair {(l2, l1)}; the canonical bases of H1 and H2 differ."
            )
        permutation[int(record["logical_id"])] = by_key[key]
    return permutation


# ---------------------------------------------------------------------------
# Circuits
# ---------------------------------------------------------------------------

def fold_h_layer_circuit(data_qubits: List[int]) -> stim.Circuit:
    """The transversal layer ``H^{⊗n}`` of the H-SWAP on ``data_qubits``."""
    circuit = stim.Circuit()
    if data_qubits:
        circuit.append("H", sorted(int(q) for q in data_qubits))
    return circuit


def fold_swap_layer_circuit(mirror_pairs: List[IndexPair]) -> stim.Circuit:
    """The fold layer ``⊗ SWAP(Q_{i,j}, Q_{j,i})`` of the H-SWAP."""
    circuit = stim.Circuit()
    if mirror_pairs:
        circuit.append("SWAP", [int(q) for pair in mirror_pairs for q in pair])
    return circuit


def fold_h_swap_circuit(data_qubits: List[int], mirror_pairs: List[IndexPair]) -> stim.Circuit:
    """Physical H-SWAP: ``H`` on every data qubit, TICK, SWAP of every mirror pair."""
    circuit = fold_h_layer_circuit(data_qubits)
    swaps = fold_swap_layer_circuit(mirror_pairs)
    if len(circuit) and len(swaps):
        circuit.append("TICK")
    circuit += swaps
    return circuit


# ---------------------------------------------------------------------------
# Logical operation set
# ---------------------------------------------------------------------------

class HGPCodeLogicalOpSet(CSSLogicalOpSet):
    """Logical operation set for :class:`HGPCode` patches.

    Inherits ``transversal_cnot`` (between two identical patches) from
    :class:`CSSLogicalOpSet` and adds the fold-transversal H-SWAP of a
    symmetric HGP code and the homomorphic CNOT to a punctured ancilla
    (module docstring)::

        executor.apply_logical_operation("fold_transversal_h_swap", [patch])
        executor.apply_logical_operation("homomorphic_cnot", [data_patch, ancilla_patch])

    Patches must be the global patches returned by ``QECSystem.add_patch``.

    ``LogicalExecutor`` dispatches on the exact type of the first patch, so
    an ancilla (:class:`PuncturedHGPCode`) as first patch, e.g. the mask
    step of the paper's Fig. 2(a) or an H-SWAP on an ancilla, needs the op
    set registered for that class too.  :func:`register_hgp_op_set` does
    both registrations.

    ``extraction_block_class`` is stored for interface parity with the other
    op sets (e.g. ``RotatedSurfaceCodeLogicalOpSet``); the H-SWAP does not
    use it.
    """

    def __init__(self, extraction_block_class: Optional[Type] = None):
        super().__init__()
        self.name = "HGPCode"
        self.extraction_block_class = extraction_block_class

    def fold_transversal_h_swap(
        self,
        builder: CircuitBuilder,
        patch: QECPatch,
        noiseless: bool = False,
        noisy_swap: bool = True,
    ) -> stim.Circuit:
        """Apply the fold-transversal H-SWAP to every logical qubit of ``patch``.

        Logical action (exact): H̄ on all k logical qubits composed with the
        permutation ``fold_logical_permutation(patch)`` of the logical pairs,
        ``(l1, l2) ↔ (l2, l1)``.  The builder's tracker follows this through
        the physical Clifford; nothing is relabelled by hand.

        Args:
            builder: CircuitBuilder driving the experiment.
            patch: Global HGPCode patch (returned by ``system.add_patch``);
                a local patch is rejected with ValueError.  Its two base
                codes must be the same matrix, else ValueError.
            noiseless: If True, tag both layers (H and SWAP) as noiseless.
            noisy_swap: Only used when ``noiseless`` is False.  True: the
                SWAP layer takes the noise model's two-qubit gate noise.
                False: the SWAP layer is tagged noiseless, modelling the fold
                as a relabelling of qubits without gate error; the H layer
                stays noisy.

        Returns:
            The appended circuit (H layer, TICK, SWAP layer), i.e.
            :func:`fold_h_swap_circuit` on this patch's global indices.  The
            returned copy carries no ``noiseless`` tags; the builder's
            circuit does when requested.
        """
        data_qubits = sorted(int(q) for q in patch.data_indices)
        mirror_pairs = fold_mirror_pairs(patch, builder)   # validates type, global patch, symmetry
        if not set(q for pair in mirror_pairs for q in pair) <= set(data_qubits):
            raise RuntimeError("Fold pairs are not data qubits of this patch.")

        h_layer = fold_h_layer_circuit(data_qubits)
        swap_layer = fold_swap_layer_circuit(mirror_pairs)
        builder.apply_unitary_block(h_layer, noiseless=noiseless)
        if len(swap_layer):
            builder.apply_unitary_block(swap_layer, noiseless=(noiseless or not noisy_swap))
        return fold_h_swap_circuit(data_qubits, mirror_pairs)

    def homomorphic_cnot(
        self,
        builder: CircuitBuilder,
        data_patch: QECPatch,
        ancilla_patch: QECPatch,
        noiseless: bool = False,
    ) -> stim.Circuit:
        """Apply the homomorphic CNOT between ``data_patch`` and its punctured ancilla.

        Physical action: one layer of CNOTs between the qubits of the two
        patches that share a coordinate (Alg. 1 step 4).  Direction: the
        data patch controls for a horizontal puncture, the ancilla controls
        for a vertical puncture (Sec. V A); see
        ``ancilla_patch.puncture.control``.

        Logical action (Eq. 35-36): a logical CNOT, in the same direction,
        between every logical qubit of the ancilla and the data logical
        qubit at the same grid coordinate; data logical qubits on the
        punctured columns/rows are untouched.  The tracker follows it.

        Args:
            builder: CircuitBuilder driving the experiment.
            data_patch: Global HGPCode patch the ancilla was punctured from.
            ancilla_patch: Global :class:`PuncturedHGPCode` patch whose
                ``puncture`` records the parent's seeds, the axis and the
                deleted bits; a mismatch with ``data_patch`` is a ValueError.
            noiseless: If True, tag the CNOT layer as noiseless.

        Returns:
            The appended ``CX`` layer, i.e.
            :func:`homomorphic_cnot_circuit` on the global pairs.
        """
        if not isinstance(data_patch, HGPCode):
            raise TypeError(f"data_patch must be an HGPCode patch, got {type(data_patch).__name__}.")
        if not isinstance(ancilla_patch, PuncturedHGPCode):
            raise TypeError(
                "ancilla_patch must be a PuncturedHGPCode (built from data_patch), "
                f"got {type(ancilla_patch).__name__}."
            )
        pairs = homomorphic_qubit_pairs(data_patch, ancilla_patch, builder)  # validates global patches + parent
        circuit = homomorphic_cnot_circuit(pairs, ancilla_patch.puncture.control)
        if len(circuit):
            builder.apply_unitary_block(circuit, noiseless=noiseless)
        return circuit


def register_hgp_op_set(executor, op_set: Optional[HGPCodeLogicalOpSet] = None,
                        extraction_block_class: Optional[Type] = None) -> HGPCodeLogicalOpSet:
    """Register one :class:`HGPCodeLogicalOpSet` for ``HGPCode`` and ``PuncturedHGPCode``.

    ``LogicalExecutor.apply_logical_operation`` looks the op set up by the
    exact class of the first patch; punctured ancillas are a subclass, so
    they need their own entry.  Returns the registered op set.
    """
    if op_set is None:
        op_set = HGPCodeLogicalOpSet(extraction_block_class=extraction_block_class)
    executor.register_op_set(HGPCode, op_set)
    executor.register_op_set(PuncturedHGPCode, op_set)
    return op_set


__all__ = [
    "HGPCodeLogicalOpSet",
    "fold_diagonal_qubits",
    "fold_h_layer_circuit",
    "fold_h_swap_circuit",
    "fold_logical_permutation",
    "fold_mirror_pairs",
    "fold_swap_layer_circuit",
    "is_symmetric_hgp",
    "register_hgp_op_set",
]
