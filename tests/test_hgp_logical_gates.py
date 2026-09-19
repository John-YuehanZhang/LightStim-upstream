"""Targeted logical Hadamard on hypergraph-product patches (Patra-Barg Algorithm 2)."""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest
import stim

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from conftest import assert_noiseless, build_quiet

from lightstim.ir.builder import CircuitBuilder
from lightstim.ir.qec_system import QECSystem
from lightstim.ir.tracker import SyndromeTracker
from lightstim.noise.config import NoiseConfig
from lightstim.protocols.hgp_targeted_gates import build_hgp_gate_verification_circuit
from lightstim.qec_code.generic_css import GenericCSSColorationExtractionBlock
from lightstim.qec_code.HGP import (
    HGPCodeLogicalOpSet,
    hgp_13_1_3,
    hgp_18_2_3,
    hgp_225_9_4,
    logical_supports,
    logical_y_frame_circuit,
    pivot_qubit,
    targeted_hadamard_algorithm2,
    targeted_hadamard_circuit,
)
from lightstim.qec_code.surface_code.unrotated import UnrotatedSurfaceCode
from lightstim.utils.linear_algebra import row_echelon


INSTANCES = {
    "hgp_13_1_3": hgp_13_1_3,
    "hgp_18_2_3": hgp_18_2_3,
    "hgp_225_9_4": hgp_225_9_4,
}
TH = "targeted_hadamard"
NOISE = NoiseConfig(p_1q=1e-3, p_2q=1e-3, p_meas=1e-3, p_reset=1e-3, p_idle=1e-3)


def _global_patch(factory):
    system = QECSystem()
    patch = system.add_patch(factory(), name="hgp")
    return system, patch


def _pauli(num_qubits, indices, letter):
    chars = ["_"] * num_qubits
    for q in indices:
        chars[q] = letter
    return stim.PauliString("".join(chars))


def _padded_tableau(circuit, num_qubits):
    padded = stim.Circuit()
    padded.append("I", list(range(num_qubits)))
    padded += circuit
    return stim.Tableau.from_circuit(padded)


def _stabilizer_matrix(patch, num_qubits, letter):
    rows = [s["data_indices"] for s in patch.stabilizers if s["type"] == letter]
    matrix = np.zeros((len(rows), num_qubits), dtype=np.uint8)
    for r, indices in enumerate(rows):
        matrix[r, indices] = 1
    return matrix


def _in_stabilizer_group(patch, num_qubits, pauli, letter):
    """True if ``pauli`` is a pure-``letter`` operator inside the ``letter`` stabilizer group."""
    text = str(pauli)[1:]
    if any(ch not in ("_", letter) for ch in text):
        return False
    matrix = _stabilizer_matrix(patch, num_qubits, letter)
    vector = np.zeros(num_qubits, dtype=np.uint8)
    vector[[i for i, ch in enumerate(text) if ch == letter]] = 1
    return row_echelon(np.vstack([matrix, vector]))[1] == row_echelon(matrix)[1]


def _raw_observable_parity(circuit, shots=64, seed=1):
    """Raw measurement parity of every OBSERVABLE_INCLUDE (no noiseless-reference subtraction)."""
    raw = circuit.compile_sampler(seed=seed).sample(shots)
    columns = []
    for inst in circuit.flattened():
        if inst.name == "OBSERVABLE_INCLUDE":
            recs = [circuit.num_measurements + t.value for t in inst.targets_copy()]
            columns.append(np.bitwise_xor.reduce(raw[:, recs], axis=1))
    return np.array(columns).T


# ---------------------------------------------------------------------------
# Exact Clifford action (stim tableau), all bundled instances, all logicals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(INSTANCES))
def test_algorithm2_plus_frame_exchanges_target_and_fixes_everything_else(name):
    system, patch = _global_patch(INSTANCES[name])
    n = system.num_qubits
    stabilizers = [_pauli(n, s["data_indices"], s["type"]) for s in patch.stabilizers]
    supports = logical_supports(patch)
    assert len(supports) == system.num_logicals

    for logical_id, support in supports.items():
        x_support, z_support = support["X"], support["Z"]
        tableau = _padded_tableau(targeted_hadamard_circuit(x_support, z_support), n)

        # Stabilizer generators are fixed, signs included.
        assert all(tableau(s) == s for s in stabilizers)

        # Target: X̄ -> +Z̄ and Z̄ -> +X̄ modulo stabilizers.
        x_logical = _pauli(n, x_support, "X")
        z_logical = _pauli(n, z_support, "Z")
        image_x = tableau(x_logical)
        image_z = tableau(z_logical)
        assert image_x.sign == 1 and _in_stabilizer_group(patch, n, image_x * z_logical, "Z")
        assert image_z.sign == 1 and _in_stabilizer_group(patch, n, image_z * x_logical, "X")

        # Every other logical Pauli is fixed exactly.
        for other, other_support in supports.items():
            if other == logical_id:
                continue
            for letter in ("X", "Z"):
                pauli = _pauli(n, other_support[letter], letter)
                assert tableau(pauli) == pauli


@pytest.mark.parametrize("name", sorted(INSTANCES))
def test_verbatim_algorithm2_is_hadamard_up_to_the_logical_y(name):
    """Steps 1-8 alone: stabilizers fixed with sign, target mapped X̄ -> -Z̄, Z̄ -> -X̄."""
    system, patch = _global_patch(INSTANCES[name])
    n = system.num_qubits
    stabilizers = [_pauli(n, s["data_indices"], s["type"]) for s in patch.stabilizers]
    for support in logical_supports(patch).values():
        x_support, z_support = support["X"], support["Z"]
        verbatim = _padded_tableau(targeted_hadamard_algorithm2(x_support, z_support), n)
        assert all(verbatim(s) == s for s in stabilizers)
        x_logical = _pauli(n, x_support, "X")
        z_logical = _pauli(n, z_support, "Z")
        # _in_stabilizer_group ignores the sign; the sign is asserted separately.
        assert verbatim(x_logical).sign == -1
        assert _in_stabilizer_group(patch, n, verbatim(x_logical) * z_logical, "Z")
        assert verbatim(z_logical).sign == -1
        assert _in_stabilizer_group(patch, n, verbatim(z_logical) * x_logical, "X")


def test_frame_layer_is_the_logical_y_of_the_target():
    system, patch = _global_patch(hgp_18_2_3)
    n = system.num_qubits
    stabilizers = [_pauli(n, s["data_indices"], s["type"]) for s in patch.stabilizers]
    supports = logical_supports(patch)
    for logical_id, support in supports.items():
        frame = _padded_tableau(logical_y_frame_circuit(support["X"], support["Z"]), n)
        assert all(frame(s) == s for s in stabilizers)
        x_logical = _pauli(n, support["X"], "X")
        z_logical = _pauli(n, support["Z"], "Z")
        assert frame(x_logical) == -x_logical      # Ȳ anticommutes with X̄ ...
        assert frame(z_logical) == -z_logical      # ... and with Z̄
        for other, other_support in supports.items():
            if other != logical_id:
                for letter in ("X", "Z"):
                    pauli = _pauli(n, other_support[letter], letter)
                    assert frame(pauli) == pauli


# ---------------------------------------------------------------------------
# Transcription checks against the paper's toric-code example (Figure 4)
# ---------------------------------------------------------------------------

def test_algorithm2_gate_sequence_matches_paper_figure_4_structure():
    system, patch = _global_patch(hgp_18_2_3)
    support = logical_supports(patch)[0]
    x_support, z_support = support["X"], support["Z"]
    rho = pivot_qubit(x_support, z_support)
    i_star = [q for q in x_support if q != rho]
    j_star = [q for q in z_support if q != rho]
    assert len(i_star) == 2 and len(j_star) == 2  # weight-3 logicals, as in the paper

    circuit = targeted_hadamard_algorithm2(x_support, z_support)
    ops = [(inst.name, [t.value for t in inst.targets_copy()])
           for inst in circuit if inst.name != "TICK"]

    expected = [("H", [rho])]
    expected += [("CX", [x, rho]) for x in j_star]          # J* controls into ρ
    expected += [("CX", [rho, y]) for y in i_star]          # ρ controls into I*
    expected += [("H", sorted(x_support))]                  # H layer on I
    expected += [("CZ", [y, rho]) for y in i_star]          # CZ ρ–I*
    expected += [("H", sorted(x_support))]                  # H layer on I
    expected += [("CZ", [x, rho]) for x in j_star]          # CZ ρ–J*
    expected += [("Y", [rho])]
    assert ops == expected


@pytest.mark.parametrize("name", sorted(INSTANCES))
def test_support_and_gate_counts(name):
    system, patch = _global_patch(INSTANCES[name])
    for support in logical_supports(patch).values():
        x_support, z_support = support["X"], support["Z"]
        size_i, size_j = len(x_support), len(z_support)
        rho = pivot_qubit(x_support, z_support)

        circuit = targeted_hadamard_algorithm2(x_support, z_support)
        touched = {t.value for inst in circuit if inst.name != "TICK" for t in inst.targets_copy()}
        assert touched == set(x_support) | set(z_support)
        assert len(touched) == size_i + size_j - 1                       # χ = |I| + |J| − 1
        two_qubit = [inst for inst in circuit if inst.name in ("CX", "CZ")]
        assert len(two_qubit) == 2 * (size_i + size_j - 2)
        for inst in two_qubit:                                           # every 2q gate touches ρ
            assert rho in {t.value for t in inst.targets_copy()}

        frame = logical_y_frame_circuit(x_support, z_support)
        frame_targets = [t.value for inst in frame for t in inst.targets_copy()]
        assert sorted(frame_targets) == sorted(touched)                  # one Pauli per support qubit
        assert {inst.name for inst in frame} == {"X", "Z", "Y"}


def test_pivot_qubit_requires_a_single_overlap():
    with pytest.raises(ValueError):
        pivot_qubit([0, 1], [2, 3])
    with pytest.raises(ValueError):
        pivot_qubit([0, 1], [0, 1])
    assert pivot_qubit([0, 1], [1, 2]) == 1


# ---------------------------------------------------------------------------
# End-to-end circuits through the builder / tracker
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,gates,init_basis,measure_basis,expected_observables",
    [
        ("hgp_13_1_3", [(TH, 0)], "Z", "X", 1),
        ("hgp_13_1_3", [(TH, 0)], "X", "Z", 1),
        ("hgp_18_2_3", [(TH, 0)], "Z", "X", 1),
        ("hgp_18_2_3", [(TH, 1)], "X", "Z", 1),
        ("hgp_18_2_3", [(TH, 0), (TH, 1)], "Z", "X", 2),
        ("hgp_18_2_3", [(TH, 1), (TH, 1)], "Z", "Z", 2),
        ("hgp_225_9_4", [(TH, 4)], "Z", "X", 1),
        ("hgp_225_9_4", [(TH, i) for i in range(9)], "X", "Z", 9),
        ("hgp_225_9_4", [(TH, 4), (TH, 4)], "Z", "Z", 9),
    ],
)
def test_gate_verification_circuits_are_noiseless(
    name, gates, init_basis, measure_basis, expected_observables
):
    circuit = build_quiet(lambda: build_hgp_gate_verification_circuit(
        INSTANCES[name](), gates, init_basis=init_basis, measure_basis=measure_basis,
        rounds=2, noise_params=None,
    ))
    assert circuit.num_detectors > 0
    assert circuit.num_observables == expected_observables
    assert_noiseless(circuit)
    assert not _raw_observable_parity(circuit).any()          # exact H̄: raw parity 0
    dem = circuit.detector_error_model()
    assert dem.num_observables == expected_observables


def test_without_frame_correction_the_raw_parity_is_flipped_but_the_sampler_is_not():
    """Steps 1-8 alone (Ȳ·H̄): the detector sampler sees no flip, the raw parity is always 1."""
    circuit = build_quiet(lambda: build_hgp_gate_verification_circuit(
        hgp_18_2_3(), [(TH, 0)], init_basis="Z", measure_basis="X", rounds=2,
        noise_params=None, gate_kwargs={"pauli_frame_correction": False},
    ))
    assert circuit.num_observables == 1
    assert_noiseless(circuit)                                  # relative to the noiseless reference
    assert _raw_observable_parity(circuit).all()               # constant logical Pauli Ȳ


def test_generic_css_extraction_block_also_supports_the_gate():
    circuit = build_quiet(lambda: build_hgp_gate_verification_circuit(
        hgp_18_2_3(), [(TH, 0)], init_basis="Z", measure_basis="X", rounds=2,
        extraction_block_class=GenericCSSColorationExtractionBlock, noise_params=None,
    ))
    assert circuit.num_observables == 1
    assert_noiseless(circuit)


def _depolarize1_target_count(circuit):
    return sum(len(inst.targets_copy()) for inst in circuit.flattened() if inst.name == "DEPOLARIZE1")


def test_noisy_circuit_builds_a_detector_error_model_and_frame_noise_switch_works():
    noisy = build_quiet(lambda: build_hgp_gate_verification_circuit(
        hgp_18_2_3(), [(TH, 0)], init_basis="Z", measure_basis="X", rounds=2,
        noise_params=NOISE, noise_model="circuit_level",
    ))
    assert noisy.num_observables == 1
    dem = noisy.detector_error_model()
    assert dem.num_detectors == noisy.num_detectors
    assert _depolarize1_target_count(noisy) > 0
    assert_noiseless(noisy.without_noise())

    frame_quiet = build_quiet(lambda: build_hgp_gate_verification_circuit(
        hgp_18_2_3(), [(TH, 0)], init_basis="Z", measure_basis="X", rounds=2,
        noise_params=NOISE, noise_model="circuit_level",
        gate_kwargs={"noisy_frame_correction": False},
    ))
    # The frame layer has |I| + |J| − 1 = 5 single-qubit Paulis on the [[18,2,3]] patch.
    assert _depolarize1_target_count(noisy) - _depolarize1_target_count(frame_quiet) == 5


def test_op_set_rejects_non_hgp_patch_and_unknown_logical():
    system = QECSystem()
    surface = system.add_patch(UnrotatedSurfaceCode(distance=3), name="sc")
    tracker = SyndromeTracker(num_qubits=system.num_qubits, expected_num_logicals=system.num_logicals)
    builder = CircuitBuilder(tracker=tracker, system_config=system, if_detector=True)
    with pytest.raises(TypeError):
        HGPCodeLogicalOpSet().targeted_hadamard(builder, surface, logical_id=0)

    system = QECSystem()
    hgp = system.add_patch(hgp_18_2_3(), name="hgp")
    tracker = SyndromeTracker(num_qubits=system.num_qubits, expected_num_logicals=system.num_logicals)
    builder = CircuitBuilder(tracker=tracker, system_config=system, if_detector=True)
    with pytest.raises(ValueError, match="Unknown logical_id 99"):
        HGPCodeLogicalOpSet().targeted_hadamard(builder, hgp, logical_id=99)


# ---------------------------------------------------------------------------
# Review follow-ups: multi-patch systems and non-self-product HGP codes
# ---------------------------------------------------------------------------

def _two_patch_system():
    system = QECSystem()
    first = system.add_patch(hgp_13_1_3(), name="a")
    local_second = hgp_18_2_3()
    second = system.add_patch(local_second, name="b", offset=(20, 0))
    tracker = SyndromeTracker(num_qubits=system.num_qubits, expected_num_logicals=system.num_logicals)
    builder = CircuitBuilder(tracker=tracker, system_config=system, if_detector=True)
    system.register_tracker(tracker)
    system.register_builder(builder)
    builder.write_coordinates()
    data = sorted(system.data_indices)
    builder.initialize({q: "Z" for q in data}, system.num_qubits)
    system.active_qubit_indices.update(data)
    return system, first, local_second, second, builder


def test_local_patch_is_rejected_and_global_patch_targets_its_own_qubits():
    system, first, local_second, second, builder = _two_patch_system()
    op_set = HGPCodeLogicalOpSet()
    with pytest.raises(ValueError, match="global patch"):
        op_set.targeted_hadamard(builder, local_second, logical_id=1)

    emitted = op_set.targeted_hadamard(builder, second, logical_id=1)
    touched = {t.value for inst in emitted if inst.name != "TICK" for t in inst.targets_copy()}
    support = logical_supports(second)[1]
    assert touched == set(support["X"]) | set(support["Z"])
    assert touched <= set(second.data_indices)
    assert not (touched & set(first.data_indices))
    assert not (touched & set(system.syndrome_indices))
    assert emitted == targeted_hadamard_circuit(support["X"], support["Z"])


def _seed(shape, rows):
    from lightstim.qec_code.HGP import BinaryParityCheck
    return BinaryParityCheck.from_row_supports(shape, rows)


def _non_self_product_codes():
    from lightstim.qec_code.HGP import BinaryParityCheck, HGPCode
    rep3 = _seed((2, 3), ((0, 1), (1, 2)))
    cyc3 = BinaryParityCheck.from_cyclic_polynomial((0, 1), size=3)
    hamming = _seed((3, 7), ((0, 1, 2, 3), (0, 1, 4, 5), (0, 2, 4, 6)))
    # Redundant fourth row (row0 xor row1) gives ker(H^T) != 0, hence CC-sector logicals.
    hamming_redundant = _seed((4, 7), ((0, 1, 2, 3), (0, 1, 4, 5), (0, 2, 4, 6), (2, 3, 4, 5)))
    return {
        "rep3_x_hamming": (lambda: HGPCode(rep3, hamming, d=3), {"bit_bit"}),
        "hamming_x_rep3": (lambda: HGPCode(hamming, rep3, d=3), {"bit_bit"}),
        "cyc3_x_hamming_redundant": (lambda: HGPCode(cyc3, hamming_redundant, d=3), {"bit_bit", "check_check"}),
        "hamming_redundant_x_cyc3": (lambda: HGPCode(hamming_redundant, cyc3, d=3), {"bit_bit", "check_check"}),
    }


@pytest.mark.parametrize("name", sorted(_non_self_product_codes()))
def test_non_self_product_codes_get_an_exact_hadamard_in_both_sectors(name):
    factory, expected_sectors = _non_self_product_codes()[name]
    system = QECSystem()
    patch = system.add_patch(factory(), name="hgp")
    n = system.num_qubits
    stabilizers = [_pauli(n, s["data_indices"], s["type"]) for s in patch.stabilizers]
    supports = logical_supports(patch)
    assert {pair["sector"] for pair in patch.logical_pairs} == expected_sectors

    for logical_id, support in supports.items():
        x_support, z_support = support["X"], support["Z"]
        exact = _padded_tableau(targeted_hadamard_circuit(x_support, z_support), n)
        verbatim = _padded_tableau(targeted_hadamard_algorithm2(x_support, z_support), n)
        x_logical = _pauli(n, x_support, "X")
        z_logical = _pauli(n, z_support, "Z")
        assert all(exact(s) == s for s in stabilizers)
        assert all(verbatim(s) == s for s in stabilizers)
        assert exact(x_logical).sign == 1 and _in_stabilizer_group(patch, n, exact(x_logical) * z_logical, "Z")
        assert exact(z_logical).sign == 1 and _in_stabilizer_group(patch, n, exact(z_logical) * x_logical, "X")
        assert verbatim(x_logical).sign == -1 and verbatim(z_logical).sign == -1
        for other, other_support in supports.items():
            if other != logical_id:
                for letter in ("X", "Z"):
                    pauli = _pauli(n, other_support[letter], letter)
                    assert exact(pauli) == pauli


# ---------------------------------------------------------------------------
# Second-review follow-ups: input validation
# ---------------------------------------------------------------------------

def test_logical_supports_rejects_malformed_records():
    class Duplicated:
        logical_ops = [
            {"logical_id": 0, "type": "X", "data_indices": [0, 1, 2]},
            {"logical_id": 0, "type": "X", "data_indices": [7, 8, 9]},
            {"logical_id": 0, "type": "Z", "data_indices": [2, 3, 4]},
        ]

    class Missing:
        logical_ops = [{"type": "X", "data_indices": [0, 1, 2]}]

    with pytest.raises(ValueError, match="more than one X record"):
        logical_supports(Duplicated())
    with pytest.raises(ValueError, match="logical_id"):
        logical_supports(Missing())


def test_targeted_hadamard_requires_an_integer_logical_id():
    system = QECSystem()
    hgp = system.add_patch(hgp_18_2_3(), name="hgp")
    tracker = SyndromeTracker(num_qubits=system.num_qubits, expected_num_logicals=system.num_logicals)
    builder = CircuitBuilder(tracker=tracker, system_config=system, if_detector=True)
    system.register_tracker(tracker)
    system.register_builder(builder)
    builder.write_coordinates()
    builder.initialize({q: "Z" for q in sorted(system.data_indices)}, system.num_qubits)
    op_set = HGPCodeLogicalOpSet()
    for bad in (True, 1.0, "1", None):
        with pytest.raises(TypeError, match="logical_id must be an integer"):
            op_set.targeted_hadamard(builder, hgp, logical_id=bad)
    emitted = op_set.targeted_hadamard(builder, hgp, logical_id=np.int64(1))
    support = logical_supports(hgp)[1]
    assert emitted == targeted_hadamard_circuit(support["X"], support["Z"])


def test_harness_rejects_zero_rounds_and_noiseless_in_gate_kwargs():
    with pytest.raises(ValueError, match="rounds"):
        build_hgp_gate_verification_circuit(hgp_13_1_3(), [(TH, 0)], rounds=0)
    with pytest.raises(ValueError, match="noiseless_gates"):
        build_hgp_gate_verification_circuit(
            hgp_13_1_3(), [(TH, 0)], gate_kwargs={"noiseless": True}
        )


def test_harness_rejects_bad_bases_and_unresolvable_circuits():
    for kwargs in ({"init_basis": "Y"}, {"measure_basis": "Y"}, {"init_basis": "z"}):
        with pytest.raises(ValueError, match="must be 'X' or 'Z'"):
            build_hgp_gate_verification_circuit(hgp_13_1_3(), [(TH, 0)], **kwargs)
    # No gate: Z init with X readout resolves nothing.
    with pytest.raises(ValueError, match="No logical qubit is resolvable"):
        build_quiet(lambda: build_hgp_gate_verification_circuit(
            hgp_13_1_3(), [], init_basis="Z", measure_basis="X"))
    # Two Hadamards on the only logical: Z init with X readout resolves nothing either.
    with pytest.raises(ValueError, match="No logical qubit is resolvable"):
        build_quiet(lambda: build_hgp_gate_verification_circuit(
            hgp_13_1_3(), [(TH, 0), (TH, 0)], init_basis="Z", measure_basis="X"))


def test_noiseless_silences_the_frame_layer_too():
    system = QECSystem()
    hgp = system.add_patch(hgp_18_2_3(), name="hgp")
    tracker = SyndromeTracker(num_qubits=system.num_qubits, expected_num_logicals=system.num_logicals)
    builder = CircuitBuilder(tracker=tracker, system_config=system, if_detector=True)
    system.register_tracker(tracker)
    system.register_builder(builder)
    builder.write_coordinates()
    builder.initialize({q: "Z" for q in sorted(system.data_indices)}, system.num_qubits)
    HGPCodeLogicalOpSet().targeted_hadamard(
        builder, hgp, logical_id=0, noiseless=True, noisy_frame_correction=True)
    gates = [inst for inst in builder.circuit if inst.name in ("H", "CX", "CZ", "X", "Y", "Z")]
    assert gates and all(inst.tag == "noiseless" for inst in gates)
