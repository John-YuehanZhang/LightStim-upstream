"""Targeted logical Paulis X̄, Ȳ, Z̄ on hypergraph-product patches."""

from __future__ import annotations

import pathlib
import sys

import pytest
import stim

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from conftest import assert_noiseless, build_quiet
from test_hgp_logical_gates import (
    INSTANCES,
    _global_patch,
    _non_self_product_codes,
    _padded_tableau,
    _pauli,
    _raw_observable_parity,
    _two_patch_system,
)

from lightstim.ir.qec_system import QECSystem
from lightstim.protocols.hgp_targeted_gates import build_hgp_gate_verification_circuit
from lightstim.qec_code.HGP import (
    HGPCodeLogicalOpSet,
    hgp_18_2_3,
    logical_pauli_circuit,
    logical_supports,
    logical_y_frame_circuit,
    pivot_qubit,
    targeted_hadamard_circuit,
    targeted_phase_circuit,
)


X, Y, Z, H = "targeted_x", "targeted_y", "targeted_z", "targeted_hadamard"


def _all_codes():
    codes = dict(INSTANCES)
    for name, (factory, _) in _non_self_product_codes().items():
        codes[name] = factory
    return codes


# ---------------------------------------------------------------------------
# Exact Clifford (Pauli) action
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(_all_codes()))
def test_logical_paulis_commute_with_stabilizers_and_conjugate_the_target_only(name):
    system = QECSystem()
    patch = system.add_patch(_all_codes()[name](), name="hgp")
    n = system.num_qubits
    stabilizers = [_pauli(n, s["data_indices"], s["type"]) for s in patch.stabilizers]
    supports = logical_supports(patch)

    for logical_id, support in supports.items():
        x_support, z_support = support["X"], support["Z"]
        x_logical = _pauli(n, x_support, "X")
        z_logical = _pauli(n, z_support, "Z")
        expected = {"X": (x_logical, -z_logical), "Z": (-x_logical, z_logical), "Y": (-x_logical, -z_logical)}
        for letter, (image_x, image_z) in expected.items():
            circuit = logical_pauli_circuit(letter, x_support, z_support)
            tableau = _padded_tableau(circuit, n)
            assert all(tableau(s) == s for s in stabilizers)          # commutes, signs included
            assert tableau(x_logical) == image_x
            assert tableau(z_logical) == image_z
            for other, other_support in supports.items():
                if other != logical_id:
                    for basis in ("X", "Z"):
                        pauli = _pauli(n, other_support[basis], basis)
                        assert tableau(pauli) == pauli
            # The layer is the logical operator itself, up to a global phase.
            as_pauli = stim.PauliString(n)
            for inst in circuit:
                for t in inst.targets_copy():
                    as_pauli[t.value] = inst.name
            reference = {"X": x_logical, "Z": z_logical, "Y": x_logical * z_logical * 1j}[letter]
            assert as_pauli == reference or as_pauli == -reference


def test_layer_shapes_and_the_y_frame_alias():
    system, patch = _global_patch(hgp_18_2_3)
    for support in logical_supports(patch).values():
        x_support, z_support = support["X"], support["Z"]
        rho = pivot_qubit(x_support, z_support)
        x_layer = logical_pauli_circuit("X", x_support, z_support)
        z_layer = logical_pauli_circuit("Z", x_support, z_support)
        y_layer = logical_pauli_circuit("Y", x_support, z_support)
        assert [(i.name, sorted(t.value for t in i.targets_copy())) for i in x_layer] == [("X", sorted(x_support))]
        assert [(i.name, sorted(t.value for t in i.targets_copy())) for i in z_layer] == [("Z", sorted(z_support))]
        y_ops = {i.name: sorted(t.value for t in i.targets_copy()) for i in y_layer}
        assert y_ops == {"X": sorted(q for q in x_support if q != rho),
                         "Z": sorted(q for q in z_support if q != rho), "Y": [rho]}
        assert logical_y_frame_circuit(x_support, z_support) == y_layer
    with pytest.raises(ValueError, match="letter must be"):
        logical_pauli_circuit("T", [0, 1], [1, 2])


def test_identities_tie_the_paulis_to_s_and_h():
    """X̄·Z̄ ∝ Ȳ, S̄² = Z̄, H̄·X̄·H̄ = Z̄ and H̄·Z̄·H̄ = X̄ as tableaus."""
    system, patch = _global_patch(hgp_18_2_3)
    n = system.num_qubits
    for support in logical_supports(patch).values():
        x_support, z_support = support["X"], support["Z"]
        rho = pivot_qubit(x_support, z_support)
        px = logical_pauli_circuit("X", x_support, z_support)
        py = logical_pauli_circuit("Y", x_support, z_support)
        pz = logical_pauli_circuit("Z", x_support, z_support)
        s_gate = targeted_phase_circuit(z_support, rho)
        h_gate = targeted_hadamard_circuit(x_support, z_support)
        assert _padded_tableau(px + pz, n) == _padded_tableau(py, n)
        assert _padded_tableau(s_gate + s_gate, n) == _padded_tableau(pz, n)
        assert _padded_tableau(h_gate + px + h_gate, n) == _padded_tableau(pz, n)
        assert _padded_tableau(h_gate + pz + h_gate, n) == _padded_tableau(px, n)


# ---------------------------------------------------------------------------
# End-to-end: preparing |1̄⟩ / |−̄⟩ on one chosen logical qubit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,logical_id", [("hgp_13_1_3", 0), ("hgp_18_2_3", 0), ("hgp_18_2_3", 1), ("hgp_225_9_4", 4)])
@pytest.mark.parametrize("gate,init_basis", [(X, "Z"), (Y, "Z"), (Z, "X"), (Y, "X")])
def test_targeted_pauli_flips_exactly_one_logical_in_the_raw_parity(name, logical_id, gate, init_basis):
    """Sampler sees no flip (reference sample); the raw readout parity is 1 on the gated logical only."""
    circuit = build_quiet(lambda: build_hgp_gate_verification_circuit(
        INSTANCES[name](), [(gate, logical_id)], init_basis=init_basis, measure_basis=init_basis,
        rounds=2, noise_params=None,
    ))
    k = len(logical_supports(QECSystem().add_patch(INSTANCES[name](), name="p")))
    assert circuit.num_observables == k
    assert_noiseless(circuit)
    means = sorted(_raw_observable_parity(circuit).mean(axis=0).tolist())
    assert means == [0.0] * (k - 1) + [1.0]


def test_commuting_pauli_leaves_the_state_and_pauli_frame_after_hadamard():
    """Z̄ on |0̄⟩ is invisible; X̄ then H then Z readout is the same as H on |1̄⟩ (raw parity 1)."""
    quiet = build_quiet(lambda: build_hgp_gate_verification_circuit(
        hgp_18_2_3(), [(Z, 0), (Z, 1)], init_basis="Z", measure_basis="Z", rounds=2, noise_params=None))
    assert quiet.num_observables == 2
    assert_noiseless(quiet)
    assert not _raw_observable_parity(quiet).any()

    flipped = build_quiet(lambda: build_hgp_gate_verification_circuit(
        hgp_18_2_3(), [(X, 0), (H, 0)], init_basis="Z", measure_basis="X", rounds=2, noise_params=None))
    assert flipped.num_observables == 1                     # H|1̄⟩ = |−̄⟩ read out in X
    assert_noiseless(flipped)
    assert _raw_observable_parity(flipped).all()


def test_local_patch_is_rejected_and_global_patch_targets_its_own_qubits():
    system, first, local_second, second, builder = _two_patch_system()
    op_set = HGPCodeLogicalOpSet()
    with pytest.raises(ValueError, match="global patch"):
        op_set.targeted_x(builder, local_second, logical_id=0)
    support = logical_supports(second)[1]
    for method, letter, expected in ((op_set.targeted_x, "X", set(support["X"])),
                                     (op_set.targeted_z, "Z", set(support["Z"])),
                                     (op_set.targeted_y, "Y", set(support["X"]) | set(support["Z"]))):
        emitted = method(builder, second, logical_id=1)
        touched = {t.value for inst in emitted for t in inst.targets_copy()}
        assert touched == expected
        assert touched <= set(second.data_indices)
        assert not (touched & set(first.data_indices))
        assert emitted == logical_pauli_circuit(letter, support["X"], support["Z"])
    with pytest.raises(ValueError, match="Unknown logical_id 5"):
        op_set.targeted_y(builder, second, logical_id=5)
