"""Punctured ancilla codes and the homomorphic CNOT for HGP patches.

Transcribed from Xu et al., "Fast and Parallelizable Logical Computation
with Homological Product Codes" (arXiv:2407.18490):

* Definition 4 (puncturing): deleting the columns ``S`` of a parity-check
  matrix ``H`` gives ``H^S = H|_{[n] \\ S}`` (Eq. 12).
* Section III E: puncturing only *information bits* keeps the classical
  distance.  With ``H`` in the canonical form ``(h_1, ..., h_k, I_{n-k})``
  (Eq. 17) the information bits are the first ``k`` columns, the generator
  matrix is ``(I_k, h^T)`` (Eq. 18), and deleting information bits keeps
  the remaining generator rows unchanged up to zero entries (Eq. 19).
* Proposition 8: puncturing induces the classical-code homomorphism
  ``gamma_1 = inclusion of the retained bits`` (Eq. 30); by Proposition 7
  the tensor product of such maps is a homomorphism of the product codes.
* Definition 3 / Algorithm 1 steps 1-4: the ancilla ``Q' = HGP(H1, H2^S)``
  has qubits ``O \\ O_0`` with ``O_0 = [n1] x S`` (the deleted columns of
  the V1 x V2 sector; the C1 x C2 sector is untouched), and physical
  transversal ``Q``-controlled CNOTs between ``Q|_{O \\ O_0}`` and
  ``Q'|_{O \\ O_0}`` (qubits with the *same coordinates*) are the
  homomorphic CNOT.
* Eq. 35-36 (proof of Theorem 9, Eq. 53-54): the logical action is a
  logical CNOT between every retained logical qubit ``Q̄_{i,j}`` and its
  counterpart ``Q̄'_{i,j}``; logical qubits on the deleted columns and all
  other logical operators are unchanged.
* Section V A: puncturing the *vertical* code ``H1`` instead reverses the
  direction of the homomorphism (``H1`` enters the chain complex
  transposed), so the homomorphic CNOT is then ``Q'``-controlled, both
  physically and logically.  This is the variant used for X-type
  measurements.

LightStim transcription
-----------------------
``HGPCode`` builds its canonical logical basis from
``canonical_kernel_basis`` (column elimination, Quintavalle-Webster-Vasmer
Algorithm 1).  Its ``pivots`` are the columns that end up in the kernel
basis with a unit entry: every kernel basis vector has a 1 at its own pivot
and 0 at every other pivot (``unit_complement @ basis.T == I``).  That is
exactly the paper's information set ``B_I`` with generator matrix in the
form of Eq. 18, so :func:`information_bits` returns those pivots and
:class:`PuncturedHGPCode` only accepts deletions inside that set.  Deleting
pivot columns does not change the elimination of the remaining columns, so
the ancilla's canonical basis on the retained columns is the restriction of
the parent's (Eq. 19); the tests check the induced logical action with stim
tableaux.

Coordinates.  ``HGPCode`` keeps the semantic product indices
``vv_qubits[(bit_1, bit_2)]`` and ``cc_qubits[(check_1, check_2)]``.  For a
horizontal puncture (``axis="horizontal"``, columns ``S`` of ``H2``) the
qubit identification is

    data (bit_1, bit_2), bit_2 not in S   <->   ancilla (bit_1, rank(bit_2))
    data (check_1, check_2)               <->   ancilla (check_1, check_2)

where ``rank(bit_2)`` is the position of ``bit_2`` among the retained bits;
for a vertical puncture the roles of ``bit_1`` and ``bit_2`` are exchanged.
Global indices are recovered through the patch coordinates and the
``QECSystem.index_map`` exactly as in :mod:`.operation`.

Axis names follow the paper (H1 is the vertical code, H2 the horizontal
one, so a horizontal puncture removes *columns* of the paper's grid).  On
LightStim's canvas ``HGPCode`` draws H1 along x and H2 along y, so
``axis="horizontal"`` removes rows of constant y there.  The identification
above is by product index, not by canvas coordinate, so this is naming only.

Information set.  The paper allows any information set (Eq. 17 holds after
permuting the bits).  This module accepts exactly one: the pivots of
``canonical_kernel_basis``.  Deleting a bit of another information set also
keeps rank and distance, but then the ancilla's canonical basis is no longer
the restriction of the parent's and the coordinate-matched identification
of Eq. 34 would need a change of logical basis, so such deletions are
rejected rather than silently mis-paired.

Beyond the paper.  The paper assumes full-rank base codes.  ``HGPCode``
also registers C1 x C2 logical pairs when the seed has redundant checks;
puncturing information bits keeps the seed's rank, so those logical qubits
survive on the (untouched) C1 x C2 sector and the CNOT acts on them as on
any retained logical pair.  Augmenting (Definition 5) is not implemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

import numpy as np

from lightstim.ir.builder import CircuitBuilder

from .algebra import canonical_kernel_basis
from .binary_parity_check import BinaryParityCheck, as_binary_parity_check
from .code_patch import HGPCode


Axis = Literal["horizontal", "vertical"]
IndexPair = Tuple[int, int]


# ---------------------------------------------------------------------------
# Classical puncturing (Definition 4, Section III E)
# ---------------------------------------------------------------------------

def information_bits(check: Any) -> Tuple[int, ...]:
    """LightStim's canonical information set of a classical code (Xu et al. Sec. III E).

    These are the pivots of ``canonical_kernel_basis``: the columns on which
    the kernel basis vectors carry their unit entries.  Their complement is a
    set of ``rank(H)`` linearly independent columns, so this is an
    information set in the paper's sense; it is the only one this module
    uses (module docstring, "Information set").
    """
    return tuple(canonical_kernel_basis(as_binary_parity_check(check)).pivots)


def puncture_parity_check(check: Any, delete: Iterable[int]) -> BinaryParityCheck:
    """Delete the columns ``delete`` of ``check`` (Definition 4, Eq. 12).

    The retained columns keep their relative order; column ``j`` of the
    result is the ``j``-th retained column of the input.  This is the bare
    matrix operation; :class:`PuncturedHGPCode` adds the information-bit
    restriction that keeps the distance.
    """
    parity_check = as_binary_parity_check(check)
    deleted = sorted({int(index) for index in delete})
    for index in deleted:
        if index < 0 or index >= parity_check.num_bits:
            raise ValueError(
                f"Cannot delete bit {index}: the code has {parity_check.num_bits} bits."
            )
    retained = [j for j in range(parity_check.num_bits) if j not in deleted]
    dense = parity_check.to_dense()[:, retained]
    return BinaryParityCheck.from_dense(dense)


def retained_bit_positions(num_bits: int, delete: Iterable[int]) -> Dict[int, int]:
    """Map each retained bit index of the parent to its index in the punctured code."""
    deleted = {int(index) for index in delete}
    return {j: position for position, j in enumerate(k for k in range(num_bits) if k not in deleted)}


# ---------------------------------------------------------------------------
# Punctured ancilla HGP code
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Puncture:
    """How an ancilla HGP code was derived from its parent (paper Alg. 1 step 1)."""

    axis: Axis
    deleted_bits: Tuple[int, ...]
    parent_h1: BinaryParityCheck
    parent_h2: BinaryParityCheck

    @property
    def control(self) -> Literal["parent", "ancilla"]:
        """Which code controls the homomorphic CNOT (Sec. V A direction rule)."""
        return "parent" if self.axis == "horizontal" else "ancilla"

    def matches_parent(self, patch: HGPCode) -> bool:
        """True if ``patch`` has exactly the parent's base matrices."""
        return (
            np.array_equal(patch.h1.to_dense(), self.parent_h1.to_dense())
            and np.array_equal(patch.h2.to_dense(), self.parent_h2.to_dense())
        )


class PuncturedHGPCode(HGPCode):
    """Ancilla HGP code obtained by puncturing one base code of a parent patch.

    ``PuncturedHGPCode(parent, axis, delete)`` deletes the information bits
    ``delete`` of the parent's horizontal seed ``H2`` (``axis="horizontal"``)
    or vertical seed ``H1`` (``axis="vertical"``) and builds
    ``HGP(H1, H2^S)`` or ``HGP(H1^S, H2)``.  Only bits of LightStim's
    canonical information set (:func:`information_bits`) may be deleted.
    This is narrower than the paper's condition (any information set,
    Sec. III E) and is what makes the coordinate-matched logical
    identification exact (module docstring).  With such deletions the seed
    keeps its rank, exactly the deleted logical bits disappear (Eq. 19) and
    the distance is preserved, hence the ancilla distance ``d' >= d`` (proof
    of Theorem 9).  Deleting a non-information bit can drop the rank instead,
    leaving a logical bit in place and a trivial check.  The instance
    remembers the puncture in ``self.puncture`` so that
    :meth:`HGPCodeLogicalOpSet.homomorphic_cnot` can pair its qubits with the
    parent's by coordinates and pick the CNOT direction.

    Deleting nothing is allowed and reproduces the parent (the standard
    transversal CNOT between two identical blocks).  Deleting every
    information bit is rejected: the paper's ancillas always keep at least
    one column/row of logical qubits.
    """

    def __init__(self, parent: HGPCode, axis: Axis, delete: Iterable[int], **kwargs: Any):
        if not isinstance(parent, HGPCode):
            raise TypeError(f"parent must be an HGPCode patch, got {type(parent).__name__}.")
        if axis not in ("horizontal", "vertical"):
            raise ValueError(f"axis must be 'horizontal' or 'vertical', got {axis!r}.")
        seed = parent.h2 if axis == "horizontal" else parent.h1
        if isinstance(delete, (str, bytes)):
            raise TypeError("delete must be an iterable of integer bit indices, not a string.")
        raw = list(delete)
        for index in raw:
            if isinstance(index, bool) or not isinstance(index, (int, np.integer)):
                raise TypeError(
                    f"delete must contain integer bit indices, got {index!r} ({type(index).__name__})."
                )
        deleted = tuple(sorted({int(index) for index in raw}))
        for index in deleted:
            if index < 0 or index >= seed.num_bits:
                raise ValueError(
                    f"Cannot delete bit {index}: the {axis} seed has {seed.num_bits} bits."
                )
        allowed = set(information_bits(seed))
        bad = [index for index in deleted if index not in allowed]
        if bad:
            raise ValueError(
                f"Bits {bad} are not in LightStim's canonical information set of the "
                f"{axis} seed (pivots of canonical_kernel_basis: {sorted(allowed)}). "
                "Only these may be punctured: the coordinate-matched logical "
                "identification relies on this information set (Xu et al. Sec. III E "
                "allows any information set, this module supports this one)."
            )
        if allowed and set(deleted) == allowed:
            raise ValueError(
                "Puncturing every information bit leaves the ancilla without "
                "logical qubits on that axis; keep at least one."
            )
        punctured = puncture_parity_check(seed, deleted)
        h1, h2 = (parent.h1, punctured) if axis == "horizontal" else (punctured, parent.h2)
        kwargs.setdefault("d", parent.code_distance)
        super().__init__(h1, h2, **kwargs)
        self.puncture = Puncture(
            axis=axis,
            deleted_bits=deleted,
            parent_h1=parent.h1,
            parent_h2=parent.h2,
        )

    # ``QECSystem.add_patch`` deep-copies the patch; ``puncture`` is a frozen
    # dataclass of frozen dataclasses and copies along.

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info["puncture"] = {
            "axis": self.puncture.axis,
            "deleted_bits": self.puncture.deleted_bits,
            "control": self.puncture.control,
            "parent_h1_shape": self.puncture.parent_h1.shape,
            "parent_h2_shape": self.puncture.parent_h2.shape,
        }
        return info


def _sector_pairs(parent: HGPCode, ancilla: PuncturedHGPCode) -> List[Tuple[int, int]]:
    """Local index pairs ``(parent_qubit, ancilla_qubit)`` with the same coordinates."""
    puncture = ancilla.puncture
    pairs: List[Tuple[int, int]] = []
    if puncture.axis == "horizontal":
        position = retained_bit_positions(parent.h2.num_bits, puncture.deleted_bits)
        for (bit_1, bit_2), local in sorted(parent.vv_qubits.items()):
            if bit_2 in position:
                pairs.append((local, ancilla.vv_qubits[(bit_1, position[bit_2])]))
    else:
        position = retained_bit_positions(parent.h1.num_bits, puncture.deleted_bits)
        for (bit_1, bit_2), local in sorted(parent.vv_qubits.items()):
            if bit_1 in position:
                pairs.append((local, ancilla.vv_qubits[(position[bit_1], bit_2)]))
    for key, local in sorted(parent.cc_qubits.items()):
        pairs.append((local, ancilla.cc_qubits[key]))
    return pairs


def homomorphic_qubit_pairs(
    parent: HGPCode,
    ancilla: PuncturedHGPCode,
    builder: Optional[CircuitBuilder] = None,
) -> List[IndexPair]:
    """Qubit pairs ``(parent, ancilla)`` identified by the puncture (Alg. 1 step 2).

    Every ancilla data qubit appears exactly once; parent qubits on the
    deleted columns/rows appear in no pair.  Indices are local without a
    ``builder``; with one, both patches must be the global patches returned
    by ``QECSystem.add_patch`` and the indices are global.
    """
    if not isinstance(ancilla, PuncturedHGPCode):
        raise TypeError(
            f"ancilla must be a PuncturedHGPCode, got {type(ancilla).__name__}."
        )
    if not isinstance(parent, HGPCode):
        raise TypeError(f"parent must be an HGPCode patch, got {type(parent).__name__}.")
    if not ancilla.puncture.matches_parent(parent):
        raise ValueError(
            "This ancilla was punctured from a different code: its parent seeds "
            f"have shapes {ancilla.puncture.parent_h1.shape}/{ancilla.puncture.parent_h2.shape}, "
            f"the given patch has {parent.h1.shape}/{parent.h2.shape} (or different entries)."
        )
    if builder is not None:
        from lightstim.qec_code._operation_utils import require_global_patch

        require_global_patch(builder, parent)
        require_global_patch(builder, ancilla)
    else:
        # ``add_patch`` stamps its global views; their records carry global
        # indices that would be mixed with local ones here.
        for label, patch in (("parent", parent), ("ancilla", ancilla)):
            if hasattr(patch, "_registered_stabilizer_uids"):
                raise ValueError(
                    f"The {label} patch is a global patch (returned by QECSystem.add_patch); "
                    "pass the builder to get global indices, or pass local patches."
                )

    def to_global(patch: HGPCode, local: int) -> int:
        if builder is None:
            return int(local)
        return int(builder.system.index_map[patch.qubit_coords[local]])

    return [
        (to_global(parent, parent_local), to_global(ancilla, ancilla_local))
        for parent_local, ancilla_local in _sector_pairs(parent, ancilla)
    ]


def homomorphic_logical_pairs(
    parent: HGPCode,
    ancilla: PuncturedHGPCode,
    builder: Optional[CircuitBuilder] = None,
) -> Dict[int, int]:
    """Map every ancilla ``logical_id`` to the parent ``logical_id`` at the same coordinate.

    Eq. 34: the logical operators of ``Q'`` are identified with those of
    ``Q`` minus ``Q_0`` under ``gamma_1``.  Concretely, an ancilla logical pair is
    matched to the parent pair whose X̄ and Z̄ supports are the images of its
    own under the qubit identification of :func:`homomorphic_qubit_pairs`.
    Parent logical qubits on the punctured columns/rows have no image.
    Both patches must be local (``builder=None``) or both global (``builder``
    given); the logical records are read at the same level.
    """
    pairs = homomorphic_qubit_pairs(parent, ancilla, builder)
    to_parent = {ancilla_qubit: parent_qubit for parent_qubit, ancilla_qubit in pairs}

    def records(patch: HGPCode) -> Dict[int, Dict[str, Tuple[int, ...]]]:
        out: Dict[int, Dict[str, Tuple[int, ...]]] = {}
        for record in patch.logical_ops:
            out.setdefault(int(record["logical_id"]), {})[str(record["type"])] = tuple(
                sorted(int(q) for q in record["data_indices"])
            )
        return out

    parent_records = records(parent)
    by_support = {(rec["X"], rec["Z"]): logical_id for logical_id, rec in parent_records.items()}
    mapping: Dict[int, int] = {}
    for ancilla_id, rec in records(ancilla).items():
        key = (
            tuple(sorted(to_parent[q] for q in rec["X"])),
            tuple(sorted(to_parent[q] for q in rec["Z"])),
        )
        if key not in by_support:
            raise RuntimeError(
                f"Ancilla logical {ancilla_id} has no parent logical with the same "
                "supports under the puncture identification; the canonical bases "
                "of parent and ancilla do not restrict onto each other."
            )
        mapping[ancilla_id] = by_support[key]
    return mapping


def homomorphic_cnot_circuit(pairs: List[IndexPair], control: Literal["parent", "ancilla"]):
    """One layer of physical CNOTs on ``pairs`` in the given direction."""
    import stim

    circuit = stim.Circuit()
    targets: List[int] = []
    for parent_qubit, ancilla_qubit in pairs:
        if control == "parent":
            targets.extend([int(parent_qubit), int(ancilla_qubit)])
        else:
            targets.extend([int(ancilla_qubit), int(parent_qubit)])
    if targets:
        circuit.append("CX", targets)
    return circuit


__all__ = [
    "Axis",
    "Puncture",
    "PuncturedHGPCode",
    "homomorphic_cnot_circuit",
    "homomorphic_logical_pairs",
    "homomorphic_qubit_pairs",
    "information_bits",
    "puncture_parity_check",
    "retained_bit_positions",
]
