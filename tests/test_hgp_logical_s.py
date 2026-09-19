"""Targeted logical S on hypergraph-product patches (Patra-Barg Algorithm 1, steps 1-4)."""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest
import stim

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from conftest import assert_noiseless, build_quiet
from test_hgp_logical_gates import (
    INSTANCES,
    _global_patch,
    _in_stabilizer_group,
    _non_self_product_codes,
    _padded_tableau,
    _pauli,
    _raw_observable_parity,
    _two_patch_system,
)

from lightstim.noise.config import NoiseConfig
from lightstim.protocols.hgp_targeted_gates import build_hgp_gate_verification_circuit
from lightstim.qec_code.HGP import (
    HGPCodeLogicalOpSet,
    hgp_13_1_3,
    hgp_18_2_3,
    logical_supports,
    pivot_qubit,
    targeted_phase_circuit,
)
from lightstim.ir.qec_system import QECSystem


S, SDAG, H = "targeted_s", "targeted_s_dag", "targeted_hadamard"
NOISE = NoiseConfig(p_1q=1e-3, p_2q=1e-3, p_meas=1e-3, p_reset=1e-3, p_idle=1e-3)


def _logical_y(n, x_support, z_support):
    """Ȳ = i·X̄·Z̄ (Hermitian; X̄ and Z̄ overlap on one qubit)."""
    return _pauli(n, x_support, "X") * _pauli(n, z_support, "Z") * 1j


def _all_codes():
    codes = {name: (factory, None) for name, factory in INSTANCES.items()}
    for name, (factory, sectors) in _non_self_product_codes().items():
        codes[name] = (factory, sectors)
    return codes


# ---------------------------------------------------------------------------
# Exact Clifford action
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(_all_codes()))
def test_steps_1_to_4_are_an_exact_logical_s(name):
    factory, _ = _all_codes()[name]
    system = QECSystem()
    patch = system.add_patch(factory(), name="hgp")
    n = system.num_qubits
    stabilizers = [_pauli(n, s["data_indices"], s["type"]) for s in patch.stabilizers]
    supports = logical_supports(patch)

    for logical_id, support in supports.items():
        x_support, z_support = support["X"], support["Z"]
        rho = pivot_qubit(x_support, z_support)
        x_logical = _pauli(n, x_support, "X")
        z_logical = _pauli(n, z_support, "Z")
        y_logical = _logical_y(n, x_support, z_support)

        for gate, expected_x in (("S", y_logical), ("S_DAG", -y_logical)):
            tableau = _padded_tableau(targeted_phase_circuit(z_support, rho, gate), n)
            assert all(tableau(s) == s for s in stabilizers)      # signs included
            assert tableau(x_logical) == expected_x               # X̄ -> ±i·X̄·Z̄ exactly
            assert tableau(z_logical) == z_logical                # Z̄ fixed exactly
            for other, other_support in supports.items():
                if other != logical_id:
                    for letter in ("X", "Z"):
                        pauli = _pauli(n, other_support[letter], letter)
                        assert tableau(pauli) == pauli


def test_any_pivot_in_the_z_support_gives_the_same_gate():
    system, patch = _global_patch(hgp_18_2_3)
    n = system.num_qubits
    for support in logical_supports(patch).values():
        x_support, z_support = support["X"], support["Z"]
        reference = _padded_tableau(targeted_phase_circuit(z_support, pivot_qubit(x_support, z_support)), n)
        for pivot in z_support:
            assert _padded_tableau(targeted_phase_circuit(z_support, pivot), n) == reference


@pytest.mark.parametrize("name", sorted(INSTANCES))
def test_paper_step_5_would_flip_exactly_the_z_checks_through_x(name):
    """Evidence for omitting step 5: the X on x is not a logical operator."""
    system, patch = _global_patch(INSTANCES[name])
    n = system.num_qubits
    for support in logical_supports(patch).values():
        x_support, z_support = support["X"], support["Z"]
        rho = pivot_qubit(x_support, z_support)
        with_step_5 = targeted_phase_circuit(z_support, rho)
        with_step_5.append("TICK")
        with_step_5.append("X", [rho])                              # the paper's step 5
        tableau = _padded_tableau(with_step_5, n)

        flipped = {(s["type"], tuple(s["data_indices"])) for s in patch.stabilizers
                   if tableau(_pauli(n, s["data_indices"], s["type"])) == -_pauli(n, s["data_indices"], s["type"])}
        z_checks_through_x = {("Z", tuple(s["data_indices"])) for s in patch.stabilizers
                              if s["type"] == "Z" and rho in s["data_indices"]}
        assert flipped == z_checks_through_x and flipped
        assert tableau(_pauli(n, z_support, "Z")) == -_pauli(n, z_support, "Z")
        assert tableau(_pauli(n, x_support, "X")) == -_logical_y(n, x_support, z_support)


def test_gate_sequence_matches_paper_figure_3a_without_the_final_x():
    system, patch = _global_patch(hgp_18_2_3)
    support = logical_supports(patch)[0]
    x_support, z_support = support["X"], support["Z"]
    rho = pivot_qubit(x_support, z_support)
    others = [q for q in z_support if q != rho]
    assert len(others) == 2                                          # weight-3 Z̄ as in the paper

    circuit = targeted_phase_circuit(z_support, rho)
    ops = [(inst.name, [t.value for t in inst.targets_copy()]) for inst in circuit if inst.name != "TICK"]
    expected = [("CX", [y, rho]) for y in others] + [("S", [rho])] + [("CX", [y, rho]) for y in others]
    assert ops == expected


@pytest.mark.parametrize("name", sorted(INSTANCES))
def test_support_and_gate_counts(name):
    system, patch = _global_patch(INSTANCES[name])
    for support in logical_supports(patch).values():
        z_support = support["Z"]
        rho = pivot_qubit(support["X"], z_support)
        circuit = targeted_phase_circuit(z_support, rho)
        touched = {t.value for inst in circuit if inst.name != "TICK" for t in inst.targets_copy()}
        assert touched == set(z_support)                              # χ = |J|
        cnots = [inst for inst in circuit if inst.name == "CX"]
        assert len(cnots) == 2 * (len(z_support) - 1)
        assert all(inst.targets_copy()[1].value == rho for inst in cnots)   # every CNOT targets x
        assert sum(1 for inst in circuit if inst.name == "S") == 1


def test_s_dag_inverts_s_and_s_squared_is_logical_z():
    system, patch = _global_patch(hgp_18_2_3)
    n = system.num_qubits
    stabilizers = [_pauli(n, s["data_indices"], s["type"]) for s in patch.stabilizers]
    for support in logical_supports(patch).values():
        x_support, z_support = support["X"], support["Z"]
        rho = pivot_qubit(x_support, z_support)
        s_circuit = targeted_phase_circuit(z_support, rho, "S")
        sdag_circuit = targeted_phase_circuit(z_support, rho, "S_DAG")
        assert _padded_tableau(s_circuit + sdag_circuit, n) == stim.Tableau(n)
        squared = _padded_tableau(s_circuit + s_circuit, n)
        assert all(squared(s) == s for s in stabilizers)
        assert squared(_pauli(n, x_support, "X")) == -_pauli(n, x_support, "X")   # Z̄ X̄ Z̄ = −X̄
        assert squared(_pauli(n, z_support, "Z")) == _pauli(n, z_support, "Z")


def test_targeted_phase_circuit_validates_inputs():
    with pytest.raises(ValueError, match="gate must be"):
        targeted_phase_circuit([6, 7, 8], 8, "T")
    with pytest.raises(ValueError, match="not in the Z support"):
        targeted_phase_circuit([6, 7, 8], 5)


# ---------------------------------------------------------------------------
# End-to-end circuits through the builder / tracker
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,gates,init_basis,measure_basis,expected_observables",
    [
        ("hgp_13_1_3", [(S, 0), (SDAG, 0)], "X", "X", 1),      # S·S† = I
        ("hgp_13_1_3", [(S, 0)] * 4, "X", "X", 1),             # S⁴ = I
        ("hgp_13_1_3", [(S, 0)], "Z", "Z", 1),                 # S fixes |0̄⟩
        ("hgp_18_2_3", [(S, 1), (SDAG, 1)], "X", "X", 2),
        ("hgp_18_2_3", [(SDAG, 0), (S, 0)], "X", "X", 2),
        ("hgp_18_2_3", [(S, 0), (S, 1)], "Z", "Z", 2),
        ("hgp_225_9_4", [(S, 4), (SDAG, 4)], "X", "X", 9),
        ("hgp_225_9_4", [(S, 4)] * 4, "X", "X", 9),
    ],
)
def test_gate_verification_circuits_are_noiseless(name, gates, init_basis, measure_basis, expected_observables):
    circuit = build_quiet(lambda: build_hgp_gate_verification_circuit(
        INSTANCES[name](), gates, init_basis=init_basis, measure_basis=measure_basis,
        rounds=2, noise_params=None,
    ))
    assert circuit.num_detectors > 0
    assert circuit.num_observables == expected_observables
    assert_noiseless(circuit)
    assert not _raw_observable_parity(circuit).any()


def test_s_squared_shows_up_as_a_logical_z_in_the_raw_parity():
    """|+̄⟩ → S² = Z̄ → |−̄⟩: sampler sees no flip, raw X̄ parity is 1 on the gated logical only."""
    circuit = build_quiet(lambda: build_hgp_gate_verification_circuit(
        hgp_18_2_3(), [(S, 0), (S, 0)], init_basis="X", measure_basis="X", rounds=2, noise_params=None,
    ))
    assert circuit.num_observables == 2
    assert_noiseless(circuit)
    parity = _raw_observable_parity(circuit)
    assert sorted(parity.mean(axis=0).tolist()) == [0.0, 1.0]


def test_single_s_with_x_readout_is_unresolvable():
    with pytest.raises(ValueError, match="No logical qubit is resolvable"):
        build_quiet(lambda: build_hgp_gate_verification_circuit(
            hgp_13_1_3(), [(S, 0)], init_basis="X", measure_basis="X"))


def test_s_and_hadamard_compose_in_one_circuit():
    """H·S·S†·H = I on logical 0, other logical untouched: Z init, Z readout."""
    circuit = build_quiet(lambda: build_hgp_gate_verification_circuit(
        hgp_18_2_3(), [(H, 0), (S, 0), (SDAG, 0), (H, 0)], init_basis="Z", measure_basis="Z",
        rounds=2, noise_params=None,
    ))
    assert circuit.num_observables == 2
    assert_noiseless(circuit)
    assert not _raw_observable_parity(circuit).any()


def test_noisy_circuit_builds_a_detector_error_model():
    noisy = build_quiet(lambda: build_hgp_gate_verification_circuit(
        hgp_18_2_3(), [(S, 0), (SDAG, 0)], init_basis="X", measure_basis="X", rounds=2,
        noise_params=NOISE, noise_model="circuit_level",
    ))
    assert noisy.num_observables == 2
    dem = noisy.detector_error_model()
    assert dem.num_detectors == noisy.num_detectors
    assert_noiseless(noisy.without_noise())


def test_local_patch_is_rejected_and_global_patch_targets_its_own_qubits():
    system, first, local_second, second, builder = _two_patch_system()
    op_set = HGPCodeLogicalOpSet()
    with pytest.raises(ValueError, match="global patch"):
        op_set.targeted_s(builder, local_second, logical_id=1)
    emitted = op_set.targeted_s(builder, second, logical_id=1)
    support = logical_supports(second)[1]
    touched = {t.value for inst in emitted if inst.name != "TICK" for t in inst.targets_copy()}
    assert touched == set(support["Z"])
    assert touched <= set(second.data_indices)
    assert not (touched & set(first.data_indices))
    assert emitted == targeted_phase_circuit(support["Z"], pivot_qubit(support["X"], support["Z"]))
    with pytest.raises(ValueError, match="Unknown logical_id 99"):
        op_set.targeted_s_dag(builder, second, logical_id=99)
