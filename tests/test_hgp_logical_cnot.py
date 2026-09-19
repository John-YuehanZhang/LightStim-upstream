"""Targeted logical CNOT on hypergraph-product patches (Patra-Barg Algorithm 3)."""

from __future__ import annotations

import itertools
import pathlib
import sys

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

from lightstim.ir.qec_system import QECSystem
from lightstim.noise.config import NoiseConfig
from lightstim.protocols.hgp_targeted_gates import build_hgp_gate_verification_circuit
from lightstim.qec_code.HGP import (
    HGPCodeLogicalOpSet,
    hgp_18_2_3,
    hgp_225_9_4,
    logical_supports,
    pivot_qubit,
    targeted_cnot_circuit,
)


CNOT, H = "targeted_cnot", "targeted_hadamard"
NOISE = NoiseConfig(p_1q=1e-3, p_2q=1e-3, p_meas=1e-3, p_reset=1e-3, p_idle=1e-3)


def cnot(control_id, target_id):
    return (CNOT, {"control_id": control_id, "target_id": target_id})


def _codes_with_pairs():
    codes = {"hgp_18_2_3": hgp_18_2_3, "hgp_225_9_4": hgp_225_9_4}
    for name, (factory, _) in _non_self_product_codes().items():
        codes[name] = factory
    return codes


# ---------------------------------------------------------------------------
# Exact Clifford action for every ordered pair of logicals and every pivot
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(_codes_with_pairs()))
def test_algorithm3_is_an_exact_logical_cnot_for_every_ordered_pair(name):
    system = QECSystem()
    patch = system.add_patch(_codes_with_pairs()[name](), name="hgp")
    n = system.num_qubits
    stabilizers = [_pauli(n, s["data_indices"], s["type"]) for s in patch.stabilizers]
    supports = logical_supports(patch)
    sectors = {pair["logical_id"]: pair["sector"] for pair in patch.logical_pairs}
    pairs = list(itertools.permutations(sorted(supports), 2))
    if len(pairs) > 20:                      # [[225,9,4]] has 72 ordered pairs; sample across the block
        pairs = pairs[::4]
    seen_sector_pairs = set()

    for control, target in pairs:
        i_c, i_t = supports[control]["Z"], supports[target]["X"]
        assert not (set(i_c) & set(i_t))     # the remark before Algorithm 3
        seen_sector_pairs.add((sectors[control], sectors[target]))
        x_c = _pauli(n, supports[control]["X"], "X")
        z_c = _pauli(n, supports[control]["Z"], "Z")
        x_t = _pauli(n, supports[target]["X"], "X")
        z_t = _pauli(n, supports[target]["Z"], "Z")
        for pivot in i_c:
            tableau = _padded_tableau(targeted_cnot_circuit(i_c, i_t, pivot), n)
            assert all(tableau(s) == s for s in stabilizers)
            image_xc = tableau(x_c) * (x_c * x_t)          # X̄_c -> X̄_c X̄_t modulo X stabilizers
            image_zt = tableau(z_t) * (z_c * z_t)          # Z̄_t -> Z̄_c Z̄_t modulo Z stabilizers
            assert image_xc.sign == 1 and _in_stabilizer_group(patch, n, image_xc, "X")
            assert image_zt.sign == 1 and _in_stabilizer_group(patch, n, image_zt, "Z")
            assert tableau(x_t) == x_t and tableau(z_c) == z_c
            for other, other_support in supports.items():
                if other not in (control, target):
                    for letter in ("X", "Z"):
                        pauli = _pauli(n, other_support[letter], letter)
                        assert tableau(pauli) == pauli
    if name == "hgp_18_2_3":
        assert seen_sector_pairs == {("bit_bit", "check_check"), ("check_check", "bit_bit")}


def test_gate_sequence_matches_paper_figure_5_structure():
    """Toric code, control in the left (VV) sector, target in the right (CC) sector."""
    system, patch = _global_patch(hgp_18_2_3)
    supports = logical_supports(patch)
    sectors = {pair["logical_id"]: pair["sector"] for pair in patch.logical_pairs}
    control = next(i for i, sec in sectors.items() if sec == "bit_bit")
    target = next(i for i, sec in sectors.items() if sec == "check_check")
    i_c, i_t = supports[control]["Z"], supports[target]["X"]
    assert len(i_c) == 3 and len(i_t) == 3
    pivot = min(i_c)                                     # Figure 5 fans into the first qubit of the column
    others = [q for q in i_c if q != pivot]

    circuit = targeted_cnot_circuit(i_c, i_t, pivot)
    ops = [(inst.name, [t.value for t in inst.targets_copy()]) for inst in circuit if inst.name != "TICK"]
    expected = ([("CX", [y, pivot]) for y in others]
                + [("CX", [pivot, y]) for y in i_t]
                + [("CX", [y, pivot]) for y in others])
    assert ops == expected
    assert all(name == "CX" for name, _ in ops)


def test_support_and_gate_counts_and_double_application():
    system, patch = _global_patch(hgp_225_9_4)
    n = system.num_qubits
    supports = logical_supports(patch)
    for control, target in ((0, 1), (4, 8), (7, 2)):
        i_c, i_t = supports[control]["Z"], supports[target]["X"]
        pivot = pivot_qubit(supports[control]["X"], i_c)
        circuit = targeted_cnot_circuit(i_c, i_t, pivot)
        touched = {t.value for inst in circuit if inst.name != "TICK" for t in inst.targets_copy()}
        assert touched == set(i_c) | set(i_t)
        cnots = [inst for inst in circuit if inst.name == "CX"]
        assert len(cnots) == 2 * (len(i_c) - 1) + len(i_t)
        assert all(pivot in {t.value for t in inst.targets_copy()} for inst in cnots)
        assert _padded_tableau(circuit + circuit, n) == stim.Tableau(n)      # CNOT² = I


def test_harness_rejects_noiseless_and_clashing_keys_in_dict_specs():
    with pytest.raises(ValueError, match="noiseless_gates"):
        build_hgp_gate_verification_circuit(
            hgp_18_2_3(), [(CNOT, {"control_id": 0, "target_id": 1, "noiseless": True})])
    with pytest.raises(ValueError, match="both in the gate spec"):
        build_hgp_gate_verification_circuit(
            hgp_18_2_3(), [cnot(0, 1)], gate_kwargs={"control_id": 0})


def test_targeted_cnot_circuit_validates_inputs():
    with pytest.raises(ValueError, match="not in the control Z support"):
        targeted_cnot_circuit([6, 7, 8], [15, 16, 17], 5)
    with pytest.raises(ValueError, match="disjoint"):
        targeted_cnot_circuit([6, 7, 8], [8, 9], 6)


# ---------------------------------------------------------------------------
# End-to-end circuits through the builder / tracker
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,gates,init_basis,measure_basis,expected_observables",
    [
        ("hgp_18_2_3", [cnot(0, 1)], "Z", "Z", 2),                    # |0̄0̄⟩ is fixed
        ("hgp_18_2_3", [cnot(1, 0)], "X", "X", 2),                    # |+̄+̄⟩ is fixed
        ("hgp_225_9_4", [cnot(0, 1)], "Z", "Z", 9),
        ("hgp_225_9_4", [cnot(3, 4), cnot(4, 3), cnot(3, 4)], "Z", "Z", 9),   # SWAP of |0̄0̄⟩
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


@pytest.mark.parametrize("name,control,target", [("hgp_18_2_3", 0, 1), ("hgp_18_2_3", 1, 0), ("hgp_225_9_4", 2, 5)])
@pytest.mark.parametrize("measure_basis", ["Z", "X"])
def test_bell_pair_parity_is_the_only_resolvable_observable(name, control, target, measure_basis):
    """H on the control then CNOT: Z̄_c Z̄_t (Z readout) or X̄_c X̄_t (X readout) is deterministic."""
    circuit = build_quiet(lambda: build_hgp_gate_verification_circuit(
        INSTANCES[name](), [(H, control), cnot(control, target)],
        init_basis="Z", measure_basis=measure_basis, rounds=2, noise_params=None,
    ))
    k = len(logical_supports(QECSystem().add_patch(INSTANCES[name](), name="p")))
    # Z readout: the Bell parity plus every untouched |0̄⟩; X readout: the Bell parity only.
    assert circuit.num_observables == (k - 1 if measure_basis == "Z" else 1)
    assert_noiseless(circuit)
    assert not _raw_observable_parity(circuit).any()


def test_two_cnots_cancel_on_an_entangled_state():
    """H_c, CNOT, CNOT, H_c returns |0̄0̄⟩: the second CNOT undoes the first on a Bell pair."""
    circuit = build_quiet(lambda: build_hgp_gate_verification_circuit(
        hgp_18_2_3(), [(H, 0), cnot(0, 1), cnot(0, 1), (H, 0)], init_basis="Z", measure_basis="Z",
        rounds=2, noise_params=None,
    ))
    assert circuit.num_observables == 2
    assert_noiseless(circuit)
    assert not _raw_observable_parity(circuit).any()


def test_noisy_bell_circuit_builds_a_detector_error_model():
    noisy = build_quiet(lambda: build_hgp_gate_verification_circuit(
        hgp_18_2_3(), [(H, 0), cnot(0, 1)], init_basis="Z", measure_basis="Z", rounds=2,
        noise_params=NOISE, noise_model="circuit_level",
    ))
    assert noisy.num_observables == 1
    dem = noisy.detector_error_model()
    assert dem.num_detectors == noisy.num_detectors
    assert_noiseless(noisy.without_noise())


def test_local_patch_is_rejected_and_global_patch_targets_its_own_qubits():
    system, first, local_second, second, builder = _two_patch_system()
    op_set = HGPCodeLogicalOpSet()
    with pytest.raises(ValueError, match="global patch"):
        op_set.targeted_cnot(builder, local_second, control_id=0, target_id=1)
    emitted = op_set.targeted_cnot(builder, second, control_id=0, target_id=1)
    supports = logical_supports(second)
    touched = {t.value for inst in emitted if inst.name != "TICK" for t in inst.targets_copy()}
    assert touched == set(supports[0]["Z"]) | set(supports[1]["X"])
    assert touched <= set(second.data_indices)
    assert not (touched & set(first.data_indices))
    with pytest.raises(ValueError, match="must differ"):
        op_set.targeted_cnot(builder, second, control_id=1, target_id=1)
    with pytest.raises(ValueError, match="Unknown logical_id 7"):
        op_set.targeted_cnot(builder, second, control_id=0, target_id=7)
    with pytest.raises(TypeError, match="logical_id must be an integer"):
        op_set.targeted_cnot(builder, second, control_id=0, target_id=1.0)
