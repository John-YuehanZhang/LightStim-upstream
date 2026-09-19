"""Targeted logical Clifford gates for hypergraph-product codes.

Transcribed from Patra and Barg, "Targeted Clifford logical gates for
hypergraph product codes", Quantum (accepted 2025-08-22), arXiv:2411.17050v3:
Section 4.2.2 (Theorem 4.6, Algorithm 2, Figure 2) for the Hadamard and
Section 4.2.1 (Theorem 4.4, Algorithm 1, Figure 3(a)) for the phase gate S.

Every logical qubit of an HGP patch is one canonical pair (X̄, Z̄) whose
supports I = supp(X̄) and J = supp(Z̄) lie on one row and one column of the
same sector and overlap on exactly one qubit ρ (Theorem 4.2 of the paper;
LightStim builds this basis in :mod:`.algebra` and :mod:`.code_patch`).  Each
targeted gate acts on that logical qubit only.

Targeted logical S (Algorithm 1, steps 1-4)
-------------------------------------------
Algorithm 1 of the paper, with I = supp(Z̄) in the paper's notation (here J):

    1. Select any qubit x in J.
    2. CNOT layer: for y in J \\ {x}, CNOT from y to x
    3. Phase gate S on qubit x
    4. CNOT layer: for y in J \\ {x}, CNOT from y to x
    5. X on qubit x

STEP 5 IS OMITTED HERE.  Steps 2-4 map Z_x to Z̄ and back, so steps 1-4 equal
exp(−iπ/4·Z̄) up to a global phase: that is the logical S̄ itself, it
commutes with every stabilizer generator, and it maps X̄ ↦ i·X̄·Z̄ = Ȳ and
fixes Z̄.  The paper adds step 5 because Appendix A.2.2 (Case II) states that
the Phase gate flips the sign of the Z-type checks through x; but S·Z·S† = Z,
so no sign ever changes.  The X on x therefore has nothing to correct and,
being a single-qubit Pauli that is not a logical operator, it anticommutes
with every Z-type check through x and with Z̄.  Checked with stim tableaus
on the bundled instances and on non-self-product codes: steps 1-4 fix all
stabilizer generators with sign and act exactly as S̄ (S̄† when step 3 uses
S†); adding step 5 flips exactly the Z-type generators containing x (1 on
[[13,1,3]], 2 on [[18,2,3]], 3 on [[225,9,4]]) and maps Z̄ ↦ −Z̄.  The X
layer of the alternative Algorithm 5 (Appendix A.3, S on J then CZ on every
pair of J) is unnecessary for the same reason: S-layer plus CZ-layer already
equals exp(−iπ/4·Z̄), and the X layer flips every Z-type generator that
touches J and maps Z̄ ↦ (−1)^|J|·Z̄.  The pivot x is taken to be ρ for
consistency with the Hadamard; any x in J gives the same gate.
Support |J|, 2(|J|−1) CNOTs all touching x (one TICK each), one S.

Targeted logical Hadamard (Algorithm 2)
---------------------------------------

Algorithm 2 of the paper, verbatim (I* = I \\ {ρ}, J* = J \\ {ρ}):

    1. Hadamard on qubit ρ
    2. CNOT layer: for x in J*, CNOT from x to ρ
    3. CNOT layer: for y in I*, CNOT from ρ to y
    4. Hadamard layer: for y in I, Hadamard on y
    5. CZ layer: for y in I*, CZ between y and ρ
    6. Hadamard layer: for y in I, Hadamard on y
    7. CZ layer: for x in J*, CZ between x and ρ
    8. Y gate on qubit ρ

THE EIGHT STEPS DIFFER FROM A LOGICAL HADAMARD BY ONE LOGICAL PAULI.
Checked with stim tableaus on every logical qubit of the bundled instances
([[13,1,3]], [[18,2,3]], [[225,9,4]]):

    * all stabilizer generators are fixed, signs included (the paper proves
      this in Appendix A.4.2; step 8 is what fixes the signs);
    * every other logical Pauli is fixed exactly;
    * the target is mapped X̄ ↦ −Z̄ and Z̄ ↦ −X̄, not X̄ ↦ Z̄ and Z̄ ↦ X̄.

So steps 1-8 realise Ȳ·H̄ on the target, i.e. a logical Hadamard followed by
the logical Pauli Ȳ.  The minus signs come from step 8: Y_ρ is needed to
restore the signs of the checks through ρ, but ρ also lies in both X̄ and Z̄,
so Y_ρ anticommutes with both and flips their images.  Steps 1-7 alone give
the right logical signs but leave every check through ρ mapped to −S.  The
paper's "implements H̄" is meant in its symplectic (sign-free) sense; Section
3.4 of the paper explicitly discards signs on logical Paulis.

Consequences in LightStim:

    * LightStim's LER pipeline does not see the extra Ȳ: stim's detector
      sampler and detector error model are defined relative to the noiseless
      reference sample, so a constant logical Pauli never shows up as a flip
      (same convention as :mod:`lightstim.protocols.rotated_logical_s`).
    * The RAW measurement parity of the target's readout does see it: on the
      [[18,2,3]] patch, |0̄⟩ → steps 1-8 → X readout gives raw parity 1 on
      every shot, while an exact H̄ gives 0.

:meth:`HGPCodeLogicalOpSet.targeted_hadamard` therefore appends, by default,
the logical Pauli frame correction Ȳ = X̄·Z̄ as one physical Pauli layer
(:func:`logical_y_frame_circuit`: X on I*, Z on J*, Y on ρ), so that the net
action is exactly H̄.  The eight steps themselves are never altered, so the
frame's Y on ρ directly follows step 8's Y on ρ; the two cancel logically but
are both emitted.  By default the frame layer is made of ordinary physical
Pauli gates and takes gate noise like every other gate (LightStim's
circuit-level model depolarizes after X/Y/Z).  ``noisy_frame_correction=False``
tags the layer noiseless instead, modelling a classically tracked Pauli frame
that is never executed on hardware.  One caveat: the frame layer is still a
separate moment of the circuit, so under ``circuit_level_with_idling`` the
other qubits collect one extra idle channel while it "runs"; only the gate
noise on I ∪ J is removed.

Every two-qubit gate of Algorithm 2 touches ρ, so the CNOT and CZ "layers" of
the paper are sequential; the circuit emits one TICK per two-qubit gate.  The
support is χ = |I| + |J| − 1 qubits.  The paper states (Section 5) that its
circuits do not account for fault tolerance; concretely, ρ is a hub that every
two-qubit gate touches, so a single fault on ρ spreads over I ∪ J.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple, Type

import numpy as np
import stim

from lightstim.ir.builder import CircuitBuilder
from lightstim.ir.operation import CSSLogicalOpSet
from lightstim.ir.qec_patch import QECPatch
from lightstim.qec_code._operation_utils import require_global_patch

from .code_patch import HGPCode


Support = Tuple[int, ...]


def logical_supports(patch: QECPatch) -> Dict[int, Dict[str, Support]]:
    """Return ``{logical_id: {"X": I, "Z": J}}`` from the patch's logical records.

    Reads ``patch.logical_ops`` because :meth:`QECSystem.add_patch` remaps
    those records to global qubit indices; pass the patch returned by
    ``add_patch`` to get global supports.
    """
    supports: Dict[int, Dict[str, Support]] = {}
    for record in patch.logical_ops:
        try:
            logical_id = int(record["logical_id"])
            letter = str(record["type"])
            data_indices = record["data_indices"]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                "Every logical record needs 'logical_id', 'type' and "
                f"'data_indices' (HGPCode patches have them); got {record!r}."
            ) from error
        entry = supports.setdefault(logical_id, {})
        if letter in entry:
            raise ValueError(
                f"Logical {logical_id} has more than one {letter} record; "
                "targeted gates need exactly one X and one Z record per logical."
            )
        entry[letter] = tuple(sorted(int(q) for q in data_indices))
    for logical_id, entry in supports.items():
        if set(entry) != {"X", "Z"}:
            raise ValueError(
                f"Logical {logical_id} needs exactly one X and one Z record; "
                f"got {sorted(entry)}."
            )
    return supports


def pivot_qubit(x_support: Iterable[int], z_support: Iterable[int]) -> int:
    """Return the unique qubit ρ shared by supp(X̄) and supp(Z̄)."""
    overlap = set(x_support) & set(z_support)
    if len(overlap) != 1:
        raise ValueError(
            "Targeted HGP gates need a canonical logical pair whose X and Z "
            f"supports overlap on exactly one qubit; got overlap {sorted(overlap)}."
        )
    return overlap.pop()


def _split_supports(x_support: Iterable[int], z_support: Iterable[int]):
    rho = pivot_qubit(x_support, z_support)
    i_all = sorted(set(x_support))
    i_star = [q for q in i_all if q != rho]
    j_star = [q for q in sorted(set(z_support)) if q != rho]
    return rho, i_all, i_star, j_star


def _append_sequential(circuit: stim.Circuit, gate: str, pairs: List[Tuple[int, int]]) -> None:
    """Emit two-qubit gates that all touch ρ, one per TICK."""
    for control, target in pairs:
        circuit.append(gate, [control, target])
        circuit.append("TICK")


def targeted_hadamard_algorithm2(x_support: Iterable[int], z_support: Iterable[int]) -> stim.Circuit:
    """Algorithm 2 of Patra and Barg, verbatim (steps 1-8).

    Net action on the target pair is Ȳ·H̄ (X̄ ↦ −Z̄, Z̄ ↦ −X̄), one logical
    Pauli away from H̄; see the module docstring.  All stabilizer generators
    and all other logical Paulis are fixed with sign.
    """
    rho, i_all, i_star, j_star = _split_supports(x_support, z_support)
    circuit = stim.Circuit()
    circuit.append("H", [rho])                                     # step 1
    circuit.append("TICK")
    _append_sequential(circuit, "CX", [(x, rho) for x in j_star])  # step 2: CNOT from x to ρ
    _append_sequential(circuit, "CX", [(rho, y) for y in i_star])  # step 3: CNOT from ρ to y
    circuit.append("H", i_all)                                     # step 4
    circuit.append("TICK")
    _append_sequential(circuit, "CZ", [(y, rho) for y in i_star])  # step 5
    circuit.append("H", i_all)                                     # step 6
    circuit.append("TICK")
    _append_sequential(circuit, "CZ", [(x, rho) for x in j_star])  # step 7
    # step 8: Y_ρ restores the signs of the checks through ρ, but it also
    # anticommutes with X̄ and Z̄ (both contain ρ), which is why steps 1-8
    # end up as Ȳ·H̄ instead of H̄.
    circuit.append("Y", [rho])
    return circuit


def logical_y_frame_circuit(x_support: Iterable[int], z_support: Iterable[int]) -> stim.Circuit:
    """The logical Pauli Ȳ = X̄·Z̄ of one canonical pair as a single Pauli layer.

    X on I*, Z on J*, and Y on ρ (X_ρ·Z_ρ ∝ Y_ρ).  Appended after Algorithm 2
    it cancels the residual Ȳ, so the total is exactly H̄.  It commutes with
    every stabilizer, so the signs fixed by step 8 stay fixed.
    """
    rho, _, i_star, j_star = _split_supports(x_support, z_support)
    circuit = stim.Circuit()
    if i_star:
        circuit.append("X", i_star)
    if j_star:
        circuit.append("Z", j_star)
    circuit.append("Y", [rho])
    return circuit


def targeted_hadamard_circuit(x_support: Iterable[int], z_support: Iterable[int]) -> stim.Circuit:
    """Algorithm 2 followed by the logical Ȳ frame layer: exactly H̄ on the target."""
    circuit = targeted_hadamard_algorithm2(x_support, z_support)
    circuit.append("TICK")
    circuit += logical_y_frame_circuit(x_support, z_support)
    return circuit


def targeted_phase_circuit(
    z_support: Iterable[int], pivot: int, gate: str = "S"
) -> stim.Circuit:
    """Steps 1-4 of Algorithm 1 of Patra and Barg: exactly S̄ (or S̄†) on the target.

    ``z_support`` is J = supp(Z̄), ``pivot`` is the qubit x of step 1 (any
    element of J), ``gate`` is ``"S"`` or ``"S_DAG"`` for step 3.  Step 5 of
    the paper (X on x) is deliberately not emitted; see the module docstring.
    """
    if gate not in ("S", "S_DAG"):
        raise ValueError(f"gate must be 'S' or 'S_DAG', got {gate!r}.")
    j_all = sorted(set(z_support))
    if pivot not in j_all:
        raise ValueError(f"pivot {pivot} is not in the Z support {j_all}.")
    others = [q for q in j_all if q != pivot]
    circuit = stim.Circuit()
    _append_sequential(circuit, "CX", [(y, pivot) for y in others])  # step 2: CNOT from y to x
    circuit.append(gate, [pivot])                                     # step 3
    if others:
        circuit.append("TICK")
        for index, y in enumerate(others):                            # step 4: CNOT from y to x
            circuit.append("CX", [y, pivot])
            if index < len(others) - 1:
                circuit.append("TICK")
    # step 5 (X on x) omitted on purpose: it is not a logical operator and
    # flips every Z-type check through x; steps 1-4 are already exactly S̄.
    return circuit


class HGPCodeLogicalOpSet(CSSLogicalOpSet):
    """Logical operation set for :class:`HGPCode` patches.

    Inherits ``transversal_cnot`` (between two patches) from
    :class:`CSSLogicalOpSet` and adds the targeted single-qubit logical
    Hadamard, S and S† of Patra and Barg.  Available through::

        executor.apply_logical_operation("targeted_hadamard", [patch], logical_id=i)
        executor.apply_logical_operation("targeted_s", [patch], logical_id=i)
        executor.apply_logical_operation("targeted_s_dag", [patch], logical_id=i)

    ``patch`` must be the global patch returned by ``QECSystem.add_patch`` so
    that its logical records carry global qubit indices.
    """

    def __init__(self, extraction_block_class: Optional[Type] = None):
        super().__init__()
        self.name = "HGPCode"
        self.extraction_block_class = extraction_block_class

    def targeted_hadamard(
        self,
        builder: CircuitBuilder,
        patch: QECPatch,
        logical_id: int,
        noiseless: bool = False,
        pauli_frame_correction: bool = True,
        noisy_frame_correction: bool = True,
    ) -> stim.Circuit:
        """Apply the targeted logical Hadamard to one logical qubit of ``patch``.

        The eight steps of Algorithm 2 are emitted verbatim.  On their own
        they realise Ȳ·H̄, one logical Pauli away from H̄ (module docstring).
        With ``pauli_frame_correction=True`` (default) the logical Ȳ frame
        layer is appended so the net action is exactly H̄.

        Args:
            builder: CircuitBuilder driving the experiment.
            patch: Global HGPCode patch (returned by ``system.add_patch``);
                a local patch is rejected with ValueError.
            logical_id: Index of the canonical logical pair to act on
                (``patch.logical_pairs[logical_id]["logical_id"]``; the record
                also lists the sector and seed indices of that pair).  The
                supports and the pivot ρ are taken from ``patch.logical_ops``,
                which ``add_patch`` remaps to global indices; the record's
                ``pivot_index`` field is not remapped and is not used here.
            noiseless: If True, tag every emitted gate as noiseless: all of
                Algorithm 2 and, when emitted, the frame layer too.
            pauli_frame_correction: If True append the Ȳ frame layer (net H̄);
                if False emit Algorithm 2 only (net Ȳ·H̄).
            noisy_frame_correction: Only used when the frame layer is
                emitted and ``noiseless`` is False.  True: the frame Paulis
                take gate noise like any other physical gate (LightStim's
                circuit-level model depolarizes after X/Y/Z).  False: the
                layer is tagged noiseless, modelling a classically tracked
                Pauli frame.

        Returns:
            The appended circuit: Algorithm 2, and with the frame correction
            also the separating TICK and the frame layer, i.e. exactly
            :func:`targeted_hadamard_circuit`.  The returned copy carries no
            ``noiseless`` tags; the builder's circuit does when requested.
        """
        x_support, z_support = self._resolve_supports(builder, patch, logical_id)

        algorithm2 = targeted_hadamard_algorithm2(x_support, z_support)
        builder.apply_unitary_block(algorithm2, noiseless=noiseless)
        emitted = algorithm2
        if pauli_frame_correction:
            frame = logical_y_frame_circuit(x_support, z_support)
            builder.apply_unitary_block(
                frame, noiseless=(noiseless or not noisy_frame_correction)
            )
            emitted = algorithm2.copy()
            emitted.append("TICK")   # apply_unitary_block separates the blocks
            emitted += frame
        return emitted

    def targeted_s(
        self,
        builder: CircuitBuilder,
        patch: QECPatch,
        logical_id: int,
        noiseless: bool = False,
    ) -> stim.Circuit:
        """Apply the targeted logical S to one logical qubit of ``patch``.

        Steps 1-4 of Algorithm 1 (CNOT fan-in from J \\ {ρ} into ρ, S on ρ,
        fan-in again).  The paper's step 5 (X on ρ) is not emitted: steps 1-4
        already equal exp(−iπ/4·Z̄), an exact S̄ that fixes every stabilizer
        generator with sign and maps X̄ ↦ i·X̄·Z̄; the extra X would flip
        every Z-type check through ρ (module docstring).  No Pauli frame
        layer is needed, unlike the Hadamard.

        Args:
            builder: CircuitBuilder driving the experiment.
            patch: Global HGPCode patch (returned by ``system.add_patch``);
                a local patch is rejected with ValueError.
            logical_id: Index of the canonical logical pair, as for
                :meth:`targeted_hadamard`.
            noiseless: If True, tag every emitted gate as noiseless.

        Returns:
            The appended circuit, i.e. :func:`targeted_phase_circuit`.
        """
        return self._apply_phase(builder, patch, logical_id, "S", noiseless)

    def targeted_s_dag(
        self,
        builder: CircuitBuilder,
        patch: QECPatch,
        logical_id: int,
        noiseless: bool = False,
    ) -> stim.Circuit:
        """Apply the targeted logical S† (step 3 of Algorithm 1 uses S†)."""
        return self._apply_phase(builder, patch, logical_id, "S_DAG", noiseless)

    def _apply_phase(self, builder, patch, logical_id, gate, noiseless):
        x_support, z_support = self._resolve_supports(builder, patch, logical_id)
        circuit = targeted_phase_circuit(z_support, pivot_qubit(x_support, z_support), gate)
        builder.apply_unitary_block(circuit, noiseless=noiseless)
        return circuit

    def _resolve_supports(self, builder, patch, logical_id):
        """Validate the call and return the global (I, J) supports of the pair."""
        if not isinstance(patch, HGPCode):
            raise TypeError(f"Expected an HGPCode patch, got {type(patch).__name__}.")
        # A local (unregistered) patch carries local indices; emitting them as
        # global ones would silently put the gate on another patch's qubits.
        require_global_patch(builder, patch)
        if isinstance(logical_id, bool) or not isinstance(logical_id, (int, np.integer)):
            raise TypeError(
                f"logical_id must be an integer index, got {logical_id!r} "
                f"({type(logical_id).__name__})."
            )
        logical_id = int(logical_id)
        supports = logical_supports(patch)
        if logical_id not in supports:
            raise ValueError(
                f"Unknown logical_id {logical_id}; this HGPCode patch "
                f"(n={patch.num_data_qubits}, k={len(supports)}) has logicals "
                f"{sorted(supports)}."
            )
        return supports[logical_id]["X"], supports[logical_id]["Z"]


__all__ = [
    "HGPCodeLogicalOpSet",
    "logical_supports",
    "logical_y_frame_circuit",
    "pivot_qubit",
    "targeted_hadamard_algorithm2",
    "targeted_hadamard_circuit",
    "targeted_phase_circuit",
]
