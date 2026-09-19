"""Hypergraph-product quantum error-correcting codes."""

from .algebra import CanonicalKernelBasis, canonical_kernel_basis
from .binary_parity_check import BinaryParityCheck
from .code_patch import HGPCode
from .instances import (
    hgp_13_1_3,
    hgp_13_1_3_seed,
    hgp_18_2_3,
    hgp_18_2_3_seed,
    hgp_225_9_4,
    hgp_225_9_4_seed,
)
from .operation import (
    HGPCodeLogicalOpSet,
    logical_supports,
    logical_y_frame_circuit,
    pivot_qubit,
    targeted_cnot_circuit,
    targeted_hadamard_algorithm2,
    targeted_hadamard_circuit,
    targeted_phase_circuit,
)
from .SE_block import (
    HGPCodeExtractionBlock,
    HGPProductColorLayer,
    HGPProductColorationExtractionBlock,
)

__all__ = [
    "BinaryParityCheck",
    "CanonicalKernelBasis",
    "HGPCode",
    "HGPCodeExtractionBlock",
    "HGPCodeLogicalOpSet",
    "HGPProductColorLayer",
    "HGPProductColorationExtractionBlock",
    "canonical_kernel_basis",
    "hgp_13_1_3",
    "hgp_13_1_3_seed",
    "hgp_18_2_3",
    "hgp_18_2_3_seed",
    "hgp_225_9_4",
    "hgp_225_9_4_seed",
    "logical_supports",
    "logical_y_frame_circuit",
    "pivot_qubit",
    "targeted_cnot_circuit",
    "targeted_hadamard_algorithm2",
    "targeted_hadamard_circuit",
    "targeted_phase_circuit",
]
