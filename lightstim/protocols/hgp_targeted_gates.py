"""Verification circuits for targeted logical gates on hypergraph-product patches.

Mirror of :func:`lightstim.protocols.fold_transversal.build_gate_verification_circuit`
for :class:`HGPCode` patches::

    init(basis) → SE rounds → targeted gate(s) → SE rounds → transversal readout

A gate is addressed as ``(op_name, logical_id)``, e.g.
``("targeted_hadamard", 4)``.  With ``init_basis="Z"`` and
``measure_basis="X"`` only the logical qubits that received an odd number of
Hadamards have a deterministic readout, and the tracker emits one observable
per such qubit; the untouched logical qubits are left unresolved.  A circuit
in which no logical is resolvable is rejected with ValueError.
"""

from __future__ import annotations

from typing import Literal, Optional, Sequence, Tuple, Type

import stim

from lightstim.ir.builder import CircuitBuilder
from lightstim.ir.logical_executor import LogicalExecutor
from lightstim.ir.qec_system import QECSystem
from lightstim.ir.tracker import SyndromeTracker
from lightstim.noise.config import NoiseConfig
from lightstim.qec_code.HGP import (
    HGPCode,
    HGPCodeLogicalOpSet,
    HGPProductColorationExtractionBlock,
)


GateSpec = Tuple[str, int]


def build_hgp_gate_verification_circuit(
    patch: HGPCode,
    gates: Sequence[GateSpec],
    init_basis: Literal["Z", "X"] = "Z",
    measure_basis: Literal["Z", "X"] = "X",
    rounds: int = 2,
    extraction_block_class: Type = HGPProductColorationExtractionBlock,
    se_block_kwargs: Optional[dict] = None,
    noise_params: Optional[NoiseConfig] = None,
    noise_model: str = "circuit_level",
    noiseless_gates: bool = False,
    gate_kwargs: Optional[dict] = None,
    patch_name: str = "hgp",
) -> stim.Circuit:
    """Build a single-patch HGP circuit that applies targeted logical gates.

    Args:
        patch: Local HGPCode patch (it is placed into a fresh QECSystem).
        gates: Sequence of ``(op_name, logical_id)`` applied in order, where
            ``op_name`` is a method of :class:`HGPCodeLogicalOpSet`.
        init_basis: Transversal initialization basis of all data qubits.
        measure_basis: Transversal readout basis of all data qubits.
        rounds: Syndrome-extraction rounds before and after the gates.
        extraction_block_class: Extraction block class for this patch.
        se_block_kwargs: Extra keyword arguments for the extraction block.
        noise_params: Optional NoiseConfig; None returns the clean circuit.
        noise_model: Noise model string (default 'circuit_level').
        noiseless_gates: If True, tag the logical-gate layers as noiseless.
        gate_kwargs: Extra keyword arguments passed to every gate call, e.g.
            ``{"pauli_frame_correction": False}``.  ``noiseless`` is not
            allowed here; use ``noiseless_gates``.
        patch_name: Name of the patch inside the QECSystem.

    Returns:
        stim.Circuit
    """
    for name, basis in (("init_basis", init_basis), ("measure_basis", measure_basis)):
        if basis not in ("X", "Z"):
            raise ValueError(f"{name} must be 'X' or 'Z', got {basis!r}.")
    if rounds < 1:
        raise ValueError(
            f"rounds must be >= 1 so the tracker can establish the logical "
            f"operators before and after the gates; got {rounds}."
        )
    if gate_kwargs and "noiseless" in gate_kwargs:
        raise ValueError(
            "Pass noiseless_gates=... instead of gate_kwargs={'noiseless': ...}."
        )

    system = QECSystem()
    global_patch = system.add_patch(patch, name=patch_name)

    tracker = SyndromeTracker(
        num_qubits=system.num_qubits,
        expected_num_logicals=system.num_logicals,
    )
    builder = CircuitBuilder(tracker=tracker, system_config=system, if_detector=True)
    system.register_tracker(tracker)
    system.register_builder(builder)

    executor = LogicalExecutor(builder)
    executor.register_op_set(
        HGPCode, HGPCodeLogicalOpSet(extraction_block_class=extraction_block_class)
    )

    builder.write_coordinates()
    se_block = extraction_block_class(system, **(se_block_kwargs or {}))
    measurement_blocks = getattr(se_block, "measurement_blocks", None)

    data_indices = sorted(system.data_indices)
    builder.initialize({q: init_basis for q in data_indices}, system.num_qubits)
    system.active_qubit_indices.update(data_indices)

    builder.apply_syndrome_extraction(
        se_block.circuit, rounds=rounds, measurement_blocks=measurement_blocks
    )
    for op_name, logical_id in gates:
        executor.apply_logical_operation(
            op_name, [global_patch], logical_id=logical_id, noiseless=noiseless_gates,
            **(gate_kwargs or {}),
        )
    builder.apply_syndrome_extraction(
        se_block.circuit, rounds=rounds, measurement_blocks=measurement_blocks
    )
    builder.apply_data_readout({q: measure_basis for q in data_indices})
    if builder.circuit.num_observables == 0:
        raise ValueError(
            "No logical qubit is resolvable by this circuit: with "
            f"init_basis={init_basis!r} and measure_basis={measure_basis!r} a "
            "logical is read out only if it received an odd number of "
            "Hadamards (X<->Z) or an even number (same basis); gates="
            f"{list(gates)!r}."
        )

    if noise_params is not None:
        return builder.build_noisy_circuit(noise_params, noise_model)
    return builder.circuit


__all__ = ["GateSpec", "build_hgp_gate_verification_circuit"]
