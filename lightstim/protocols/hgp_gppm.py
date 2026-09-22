"""Grid Pauli product measurements (GPPMs) on HGP patches via punctured ancillas.

Transcribed from Xu et al., arXiv:2407.18490, Sec. IV D 2 (Algorithm 1),
Sec. V A (Fig. 2(a), Algorithm 2) and Sec. V A's direction rule, restricted
to the three measurements that selective teleportation needs (no augmenting,
no merged columns):

* **Z-type, one logical column.**  Algorithm 1 with ``E_h = {{b}}``: the
  ancilla ``Q' = HGP(H1, H2^S)`` keeps only information bit ``b`` of the
  horizontal seed, is prepared in ``|0>`` with ``d`` rounds of stabilizer
  measurement (step 3), receives ``Q``-controlled homomorphic CNOTs (step 4)
  and is measured transversally in the Z basis (step 5).  This measures
  ``Z̄_{i,b}`` for every row ``i`` (Theorem 9).
* **X-type, one logical row.**  The same with the vertical seed punctured,
  the ancilla prepared in ``|+>``, ``Q'``-controlled CNOTs and an X readout
  (Sec. V A: the homomorphism reverses direction); measures ``X̄_{a,j}`` for
  every column ``j``.
* **Single logical qubit (Fig. 2(a)).**  Step 1: a mask code ``Q''`` is
  punctured from ``Q'`` on the vertical seed so that it lacks row ``a``, is
  prepared in ``|+>``, and an X-type GPPM from ``Q''`` onto ``Q'`` resets
  every logical of ``Q'`` except ``(a, b)`` to ``|+>``.  Step 2: the Z-type
  GPPM from ``Q`` onto ``Q'`` then measures only ``Z̄_{a,b}``, because a CNOT
  onto a ``|+>`` target does nothing.
* Cross-block ``Z̄_{a,b} Z̄'_{a,b}`` (Algorithm 3 steps 3-5): the same
  ancilla receives the homomorphic CNOTs of two data blocks before the Z
  readout.  :func:`apply_gppm` takes any number of data patches.

Pauli frame.  Algorithm 2 notes that the classical frame updates are
omitted; here nothing is applied physically either.  The readout records
are returned (``GPPMResult.outcome_records``), and the tracker keeps the
measured logical relation as a logical row (e.g. ``Z̄ Z̄'`` after a
cross-block measurement), so a later readout of the participating blocks
in the measured basis yields an observable such as ``Z̄ ⊕ m`` automatically
(this is how the verification harness checks the measured value).

LightStim transcription, deviations and usage rules
---------------------------------------------------
The ancilla block is read out with ``CircuitBuilder.apply_data_readout`` on
its data qubits only (the same call pattern as reading out a
lattice-surgery corridor), after its stabilizers are removed from the
system's active set.  The data blocks continue.

Tracker limitation behind the rules below (to be addressed in the tracker,
``SyndromeTracker.process_data_measurement`` step 3): when a readout is
processed, every tracked row is tested on its own for being supported on
the measured qubits; rows are never combined.  After a homomorphic CNOT
the ancilla's check rows read ``check_data · check_ancilla`` and the
logical rows read ``X̄_data X̄_target``, so facts that only a product of two
rows would expose are missed:

* Deviation: ``rounds_between`` (default 1) rounds of syndrome extraction
  are run on the whole system between the homomorphic CNOT and the ancilla
  readout.  The paper reads the ancilla out right after the CNOTs, and the
  readout detectors ``m ⊕ r_ancilla-check ⊕ r_data-check`` are computable
  from the existing records; the tracker does not combine the two rows, so
  with ``rounds_between=0`` the readout emits no detectors and a single
  readout error flips the outcome undetected (measured circuit-level
  distance 1).  The extra round re-measures the ancilla checks, giving
  single-block rows, and restores distance ``d``.  It measures stabilizers
  of the joint state only (the CNOT is a code homomorphism), so the logical
  action is unchanged; the cost is one code cycle on every active block.
* Rule: the final readout must cover every block that took part in a GPPM
  (data and target blocks together).  Reading out only one of them drops
  the observables that a product of rows would determine, silently.
* Rule: one registered ancilla per GPPM.  A read-out ancilla cannot be
  re-initialised and re-activated (the tracker's logical budget rejects it);
  Algorithm 3's column loop uses a fresh ancilla patch per column here.
* Limitation: a GPPM on logicals whose value is already fixed by the
  tracked state (an unmasked column/row measurement while sibling logicals
  sit in the measured-basis eigenstate) makes the tracker fail its
  logical-count checks.  The masked single-logical gadget and chains on a
  data block prepared in ``|0>`` or ``|+>`` are not affected; the
  verification harness prepares the data block in the complementary basis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from lightstim.ir.builder import CircuitBuilder
from lightstim.qec_code._operation_utils import require_global_patch
from lightstim.qec_code.HGP import (
    HGPCode,
    HGPCodeLogicalOpSet,
    HGPProductColorationExtractionBlock,
    PuncturedHGPCode,
    homomorphic_logical_pairs,
)


Basis = Literal["X", "Z"]


@dataclass(frozen=True)
class GPPMResult:
    """Bookkeeping of one GPPM readout.

    ``outcome_records[(data_patch_name, data_logical_id)]`` lists the absolute
    measurement-record indices whose parity is the measured value of that
    data logical (``Z̄`` for a Z-type, ``X̄`` for an X-type GPPM).  With
    several data patches the key ``("*", ancilla_logical_id)`` gives the
    joint product over all of them (e.g. ``Z̄ Z̄'``).

    The parity is the raw outcome on one representative of the ancilla's
    logical operator (its canonical support).  Like any transversal
    readout it is only reliable together with the ancilla-check detectors
    emitted at the readout, which a decoder uses to correct it; the
    observables the tracker emits later already account for this.
    """

    basis: Basis
    ancilla_name: str
    readout_qubits: Tuple[int, ...]
    first_record: int
    logical_pairs: Dict[str, Dict[int, int]] = field(default_factory=dict)
    outcome_records: Dict[Tuple[str, int], Tuple[int, ...]] = field(default_factory=dict)


def _patch_name(builder: CircuitBuilder, patch: HGPCode) -> str:
    uids = getattr(patch, "_registered_stabilizer_uids", None)
    for name, (registered, _) in builder.system.patches.items():
        if uids is not None and uids == registered._registered_stabilizer_uids:
            return name
    raise ValueError("Pass the global patch returned by system.add_patch(), not a local patch.")


def gppm_basis_for(ancilla: PuncturedHGPCode) -> Basis:
    """The measurement basis a punctured ancilla serves (Sec. V A).

    Horizontal puncture: data-controlled CNOTs, ancilla in ``|0>``, Z readout.
    Vertical puncture: ancilla-controlled CNOTs, ancilla in ``|+>``, X readout.
    """
    return "Z" if ancilla.puncture.axis == "horizontal" else "X"


def initialize_ancilla(builder: CircuitBuilder, ancilla: PuncturedHGPCode, *, noiseless: bool = False) -> Basis:
    """Prepare every data qubit of ``ancilla`` in the basis its GPPM needs (Alg. 2 step 3).

    The caller runs the stabilizer-measurement rounds afterwards (``d``
    rounds in the paper), together with the data block.  Returns the basis.
    """
    require_global_patch(builder, ancilla)
    basis = gppm_basis_for(ancilla)
    qubits = sorted(int(q) for q in ancilla.data_indices)
    builder.initialize({q: basis for q in qubits}, builder.system.num_qubits, noiseless=noiseless)
    builder.system.active_qubit_indices.update(qubits)
    return basis


def apply_gppm(
    builder: CircuitBuilder,
    data_patches: Sequence[HGPCode],
    ancilla: PuncturedHGPCode,
    extraction_block_class=HGPProductColorationExtractionBlock,
    *,
    rounds_between: int = 1,
    noiseless: bool = False,
    op_set: Optional[HGPCodeLogicalOpSet] = None,
    se_block_kwargs: Optional[dict] = None,
) -> GPPMResult:
    """Homomorphic CNOTs, ``rounds_between`` SE rounds, then read the ancilla out.

    Args:
        builder: CircuitBuilder driving the experiment.
        data_patches: One global HGP patch (single-block PPM) or several
            (joint product, Alg. 3 steps 3-5).  Each must be the parent
            of ``ancilla`` (same seeds).
        ancilla: Global :class:`PuncturedHGPCode`, already initialised
            (:func:`initialize_ancilla`) and stabilized for ``d`` rounds.
        extraction_block_class: Extraction block used for the rounds between
            the CNOTs and the readout; built on the system so it covers all
            active patches.
        rounds_between: Stabilizer rounds between the CNOT layer(s) and the
            readout (module docstring; ``0`` reproduces the paper's timing
            but leaves the readout without detectors in LightStim).
        noiseless: Tag the CNOT layers and the readout as noiseless.
        op_set: Op set to use (default: a fresh ``HGPCodeLogicalOpSet``).
        se_block_kwargs: Extra arguments for the extraction block.

    Returns:
        :class:`GPPMResult`.  After this call the ancilla's stabilizers are
        inactive and its data qubits measured; the data blocks continue.
    """
    if not data_patches:
        raise ValueError("apply_gppm needs at least one data patch.")
    if not isinstance(ancilla, PuncturedHGPCode):
        raise TypeError(f"ancilla must be a PuncturedHGPCode, got {type(ancilla).__name__}.")
    if rounds_between < 0:
        raise ValueError(f"rounds_between must be >= 0, got {rounds_between}.")
    op_set = op_set or HGPCodeLogicalOpSet()
    basis = gppm_basis_for(ancilla)
    ancilla_name = _patch_name(builder, ancilla)
    system = builder.system
    ancilla_uids = set(ancilla._registered_stabilizer_uids)
    if not ancilla_uids <= set(system.active_stabilizer_indices):
        raise ValueError(
            f"Ancilla {ancilla_name!r} is not (or no longer) active: a read-out ancilla "
            "cannot be reused; register a fresh ancilla patch for every GPPM."
        )
    data_names = [_patch_name(builder, patch) for patch in data_patches]
    if len(set(data_names)) != len(data_names):
        raise ValueError(f"Each data patch may appear once; got {data_names}.")

    logical_pairs: Dict[str, Dict[int, int]] = {}
    for data_patch in data_patches:
        name = _patch_name(builder, data_patch)
        logical_pairs[name] = homomorphic_logical_pairs(data_patch, ancilla, builder)
        op_set.homomorphic_cnot(builder, data_patch, ancilla, noiseless=noiseless)

    if rounds_between:
        block = extraction_block_class(system, **(se_block_kwargs or {}))
        builder.apply_syndrome_extraction(
            block.circuit, rounds=rounds_between,
            measurement_blocks=getattr(block, "measurement_blocks", None),
        )

    # Retire the ancilla's stabilizers, then read its data qubits out; the
    # tracker keeps the measured logical relations as logical rows.
    system.active_stabilizer_indices = set(system.active_stabilizer_indices) - ancilla_uids
    readout_qubits = tuple(sorted(int(q) for q in ancilla.data_indices))
    first_record = builder.tracker.total_measurements
    builder.apply_data_readout({q: basis for q in readout_qubits}, noiseless=noiseless)
    record_of = {q: first_record + position for position, q in enumerate(readout_qubits)}

    # Outcome = parity of the readout over the support of the ancilla's
    # logical operator of the measured type.
    ancilla_support: Dict[int, Tuple[int, ...]] = {}
    for record in ancilla.logical_ops:
        if str(record["type"]) == basis:
            ancilla_support[int(record["logical_id"])] = tuple(sorted(int(q) for q in record["data_indices"]))
    outcome_records: Dict[Tuple[str, int], Tuple[int, ...]] = {}
    for ancilla_id, support in ancilla_support.items():
        records = tuple(record_of[q] for q in support)
        if len(data_patches) == 1:
            name = next(iter(logical_pairs))
            outcome_records[(name, logical_pairs[name][ancilla_id])] = records
        else:
            outcome_records[("*", ancilla_id)] = records
    return GPPMResult(
        basis=basis,
        ancilla_name=ancilla_name,
        readout_qubits=readout_qubits,
        first_record=first_record,
        logical_pairs=logical_pairs,
        outcome_records=outcome_records,
    )


# ---------------------------------------------------------------------------
# Single logical qubit (Fig. 2(a)): ancilla + mask code
# ---------------------------------------------------------------------------

def logical_grid_bits(patch: HGPCode, logical_id: int) -> Tuple[int, int]:
    """Seed information bits ``(row bit of H1, column bit of H2)`` of a V1xV2 logical.

    In the canonical basis the logical ``(l1, l2)`` sits on the pivot column
    of kernel vector ``l1`` of H1 (its row) and the pivot column of kernel
    vector ``l2`` of H2 (its column); these are exactly the seed bits that
    :class:`PuncturedHGPCode` keeps or deletes.
    """
    record = next((r for r in patch.logical_pairs if int(r["logical_id"]) == int(logical_id)), None)
    if record is None:
        raise ValueError(f"Unknown logical_id {logical_id}; this patch has {patch.num_logicals} logicals.")
    if record["sector"] != "bit_bit":
        raise ValueError(
            f"Logical {logical_id} lives on the C1xC2 sector (redundant checks); the paper's "
            "grid gadgets address V1xV2 logicals only."
        )
    l1, l2 = record["seed_indices"]
    return int(patch.kernel_h1.pivots[l1]), int(patch.kernel_h2.pivots[l2])


def single_logical_ancillas(patch: HGPCode, logical_id: int, basis: Basis) -> Tuple[PuncturedHGPCode, PuncturedHGPCode]:
    """Local ``(ancilla, mask)`` codes for measuring one logical qubit (Fig. 2(a)).

    Z-type: the ancilla keeps only the logical's column of the horizontal
    seed (all other information bits punctured) and the mask is the ancilla
    with the logical's row punctured from the vertical seed.  X-type: rows
    and columns exchanged.  Both are local patches; register them with
    ``QECSystem.add_patch`` before use.
    """
    if basis not in ("X", "Z"):
        raise ValueError(f"basis must be 'X' or 'Z', got {basis!r}.")
    row_bit, column_bit = logical_grid_bits(patch, logical_id)
    from lightstim.qec_code.HGP import information_bits

    info_h1, info_h2 = information_bits(patch.h1), information_bits(patch.h2)
    if basis == "Z":
        ancilla = PuncturedHGPCode(patch, "horizontal", [j for j in info_h2 if j != column_bit])
        mask = PuncturedHGPCode(ancilla, "vertical", [row_bit])
    else:
        ancilla = PuncturedHGPCode(patch, "vertical", [i for i in info_h1 if i != row_bit])
        mask = PuncturedHGPCode(ancilla, "horizontal", [column_bit])
    return ancilla, mask


def apply_masked_gppm(
    builder: CircuitBuilder,
    data_patch: HGPCode,
    ancilla: PuncturedHGPCode,
    mask: PuncturedHGPCode,
    extraction_block_class=HGPProductColorationExtractionBlock,
    *,
    rounds_between: int = 1,
    noiseless: bool = False,
    op_set: Optional[HGPCodeLogicalOpSet] = None,
    se_block_kwargs: Optional[dict] = None,
) -> GPPMResult:
    """Fig. 2(a): mask step (``mask`` -> ``ancilla``) then the GPPM (``data`` -> ``ancilla``).

    ``ancilla`` and ``mask`` are the registered patches of
    :func:`single_logical_ancillas` (mask punctured from the ancilla on the
    other axis), both already initialised with :func:`initialize_ancilla`
    and stabilized.  Step 1 resets every ancilla logical that the mask
    still contains to the mask's basis eigenstate, so step 2 measures only
    the ancilla logicals the mask lacks.  The returned result keeps the
    outcome records of those logicals only.
    """
    if not isinstance(mask, PuncturedHGPCode) or not mask.puncture.matches_parent(ancilla):
        raise ValueError("mask must be a PuncturedHGPCode punctured from the ancilla.")
    if gppm_basis_for(mask) == gppm_basis_for(ancilla):
        raise ValueError("mask must be punctured on the other axis than the ancilla (Fig. 2(a)).")
    masked = set(homomorphic_logical_pairs(ancilla, mask, builder).values())   # ancilla logicals reset by the mask
    apply_gppm(builder, [ancilla], mask, extraction_block_class, rounds_between=rounds_between,
               noiseless=noiseless, op_set=op_set, se_block_kwargs=se_block_kwargs)
    result = apply_gppm(builder, [data_patch], ancilla, extraction_block_class, rounds_between=rounds_between,
                        noiseless=noiseless, op_set=op_set, se_block_kwargs=se_block_kwargs)
    (name,) = result.logical_pairs
    pairs = result.logical_pairs[name]
    kept = {(name, data_id): records for (n, data_id), records in result.outcome_records.items()
            for ancilla_id, d_id in pairs.items() if d_id == data_id and ancilla_id not in masked}
    return GPPMResult(
        basis=result.basis,
        ancilla_name=result.ancilla_name,
        readout_qubits=result.readout_qubits,
        first_record=result.first_record,
        logical_pairs={name: {a: d for a, d in pairs.items() if a not in masked}},
        outcome_records=kept,
    )


__all__ = [
    "GPPMResult",
    "apply_gppm",
    "apply_masked_gppm",
    "gppm_basis_for",
    "initialize_ancilla",
    "logical_grid_bits",
    "single_logical_ancillas",
]
