"""Grid Pauli product measurements on HGP patches (Xu et al. Alg. 1, Fig. 2(a), Alg. 3 steps 3-5)."""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest
import stim

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from conftest import assert_noiseless

from lightstim.ir.builder import CircuitBuilder
from lightstim.ir.qec_system import QECSystem
from lightstim.ir.tracker import SyndromeTracker
from lightstim.noise.config import NoiseConfig
from lightstim.protocols.hgp_gppm import (
    GPPMResult,
    apply_gppm,
    apply_masked_gppm,
    gppm_basis_for,
    initialize_ancilla,
    logical_grid_bits,
    single_logical_ancillas,
)
from lightstim.qec_code.HGP import (
    HGPCode,
    HGPProductColorationExtractionBlock,
    PuncturedHGPCode,
    hgp_13_1_3,
    hgp_225_9_4,
    information_bits,
)


NOISE = NoiseConfig(p_1q=1e-3, p_2q=1e-3, p_meas=1e-3, p_reset=1e-3, p_idle=1e-3)
HAMMING = np.array(
    [[1, 0, 1, 0, 1, 0, 1], [0, 1, 1, 0, 0, 1, 1], [0, 0, 0, 1, 1, 1, 1]], dtype=np.uint8
)


def hamming2() -> HGPCode:
    return HGPCode(HAMMING, d=3)


OTHER = {"Z": "X", "X": "Z"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _system(*patches):
    """patches: (name, local patch); placed side by side on the x axis."""
    system = QECSystem()
    registered, x = {}, 0
    for name, patch in patches:
        registered[name] = system.add_patch(patch, offset=(x, 0), name=name)
        x = max(c[0] for c in registered[name].qubit_coords.values()) + 3
    tracker = SyndromeTracker(num_qubits=system.num_qubits, expected_num_logicals=system.num_logicals)
    builder = CircuitBuilder(tracker=tracker, system_config=system, if_detector=True)
    system.register_tracker(tracker)
    system.register_builder(builder)
    builder.write_coordinates()
    return system, registered, builder


def _init_data(builder, patch, basis):
    qubits = sorted(patch.data_indices)
    builder.initialize({q: basis for q in qubits}, builder.system.num_qubits)
    builder.system.active_qubit_indices.update(qubits)


def _se(builder, rounds):
    block = HGPProductColorationExtractionBlock(builder.system)
    builder.apply_syndrome_extraction(block.circuit, rounds=rounds, measurement_blocks=block.measurement_blocks)


def _distance(circuit):
    dem = circuit.detector_error_model(decompose_errors=False)
    max_degree = max(
        (sum(1 for t in inst.targets_copy() if t.is_relative_detector_id())
         for inst in dem.flattened() if inst.type == "error"),
        default=0,
    )
    errors = circuit.search_for_undetectable_logical_errors(
        dont_explore_detection_event_sets_with_size_above=max_degree + 1,
        dont_explore_edges_with_degree_above=max_degree + 1,
        dont_explore_edges_increasing_symptom_degree=False,
    )
    return len(errors)


def _raw_parities(circuit, records_list, shots=32, seed=7):
    """Raw parities of the given absolute record index tuples."""
    raw = circuit.compile_sampler(seed=seed).sample(shots)
    return [np.bitwise_xor.reduce(raw[:, list(records)], axis=1) for records in records_list]


def _final_readout_records(builder, patch, logical_id, basis):
    """Absolute record indices of the final readout on the support of a data logical."""
    support = next(
        sorted(int(q) for q in r["data_indices"])
        for r in patch.logical_ops if int(r["logical_id"]) == logical_id and r["type"] == basis
    )
    return support


def _column_gppm(factory, basis, rounds=2, rounds_between=1, final_basis=None):
    """Data block in the complementary basis, ancilla for one column/row, GPPM, data readout."""
    parent = factory()
    axis = "horizontal" if basis == "Z" else "vertical"
    seed = parent.h2 if basis == "Z" else parent.h1
    ancilla_local = PuncturedHGPCode(factory(), axis, information_bits(seed)[1:])
    system, regs, builder = _system(("data", factory()), ("anc", ancilla_local))
    data, ancilla = regs["data"], regs["anc"]
    _init_data(builder, data, OTHER[basis])
    assert initialize_ancilla(builder, ancilla) == basis
    _se(builder, rounds)
    detectors_before = builder.circuit.num_detectors
    result = apply_gppm(builder, [data], ancilla, rounds_between=rounds_between)
    readout_detectors = builder.circuit.num_detectors - detectors_before - rounds_between * (
        len(data.stabilizers) + len(ancilla.stabilizers)
    )
    _se(builder, rounds)
    final_basis = final_basis or basis
    first_final = builder.tracker.total_measurements
    data_qubits = sorted(data.data_indices)
    builder.apply_data_readout({q: final_basis for q in data_qubits})
    final_record = {q: first_final + i for i, q in enumerate(data_qubits)}
    return system, data, ancilla, builder, result, readout_detectors, final_record


# ---------------------------------------------------------------------------
# Basis rule and ancilla preparation
# ---------------------------------------------------------------------------

def test_basis_rule_follows_the_puncture_axis():
    parent = hgp_225_9_4()
    assert gppm_basis_for(PuncturedHGPCode(parent, "horizontal", [])) == "Z"
    assert gppm_basis_for(PuncturedHGPCode(parent, "vertical", [])) == "X"


def test_initialize_ancilla_prepares_the_gppm_basis_and_requires_a_global_patch():
    parent = hgp_225_9_4()
    for axis, basis, gate in (("horizontal", "Z", "R"), ("vertical", "X", "RX")):
        ancilla_local = PuncturedHGPCode(hgp_225_9_4(), axis, information_bits(parent.h2 if basis == "Z" else parent.h1)[1:])
        system, regs, builder = _system(("data", hgp_225_9_4()), ("anc", ancilla_local))
        _init_data(builder, regs["data"], "Z")
        assert initialize_ancilla(builder, regs["anc"]) == basis
        resets = [inst for inst in builder.circuit.flattened() if inst.name == gate]
        assert any(set(t.value for t in inst.targets_copy()) >= set(regs["anc"].data_indices) for inst in resets)
        with pytest.raises(ValueError, match="global patch"):
            initialize_ancilla(builder, ancilla_local)


def test_logical_grid_bits_and_single_logical_ancillas():
    parent = hgp_225_9_4()
    info_h1, info_h2 = information_bits(parent.h1), information_bits(parent.h2)
    for logical_id in range(parent.num_logicals):
        row_bit, column_bit = logical_grid_bits(parent, logical_id)
        assert row_bit in info_h1 and column_bit in info_h2
        for basis in ("Z", "X"):
            ancilla, mask = single_logical_ancillas(parent, logical_id, basis)
            assert gppm_basis_for(ancilla) == basis and gppm_basis_for(mask) == OTHER[basis]
            assert mask.puncture.matches_parent(ancilla)
            # The ancilla keeps one column (row); the mask lacks exactly the target's row (column).
            assert ancilla.num_logicals == (len(info_h1) if basis == "Z" else len(info_h2))
            assert mask.num_logicals == ancilla.num_logicals - 1
    with pytest.raises(ValueError, match="Unknown logical_id"):
        logical_grid_bits(parent, 99)
    with pytest.raises(ValueError, match="basis"):
        single_logical_ancillas(parent, 0, "Y")


def test_c1c2_sector_logicals_are_rejected_by_the_grid_gadgets():
    from lightstim.qec_code.HGP import hgp_18_2_3

    toric = hgp_18_2_3()
    cc = next(int(r["logical_id"]) for r in toric.logical_pairs if r["sector"] == "check_check")
    with pytest.raises(ValueError, match="C1xC2"):
        logical_grid_bits(toric, cc)


# ---------------------------------------------------------------------------
# One column / one row (Algorithm 1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("factory", [hgp_225_9_4, hamming2], ids=["hgp_225_9_4", "hamming2"])
@pytest.mark.parametrize("basis", ["Z", "X"])
def test_column_gppm_measures_every_logical_of_the_column(factory, basis):
    system, data, ancilla, builder, result, readout_detectors, final_record = _column_gppm(factory, basis)
    circuit = builder.circuit
    column = ancilla.num_logicals

    # Readout: one detector per ancilla check of the measured type (rounds_between=1).
    assert readout_detectors == sum(1 for s in ancilla.stabilizers if s["type"] == basis)
    # Bookkeeping.
    assert isinstance(result, GPPMResult) and result.basis == basis and result.ancilla_name == "anc"
    assert result.readout_qubits == tuple(sorted(ancilla.data_indices))
    assert set(result.logical_pairs) == {"data"} and len(result.logical_pairs["data"]) == column
    assert set(result.outcome_records) == {("data", d) for d in result.logical_pairs["data"].values()}
    # The tracker resolves the measured logicals at the final readout: Z̄ ⊕ m.
    assert circuit.num_observables == column
    assert_noiseless(circuit)
    # The returned records are the outcome: parity(records) == parity(final readout on the logical support).
    checks = []
    for (_, data_id), records in result.outcome_records.items():
        support = _final_readout_records(builder, data, data_id, basis)
        checks.append(tuple(records) + tuple(final_record[q] for q in support))
    assert all(not parity.any() for parity in _raw_parities(circuit, checks))
    # Noisy circuit is consistent.
    noisy = builder.build_noisy_circuit(NOISE, "circuit_level")
    dem = noisy.detector_error_model(decompose_errors=False)
    assert dem.num_detectors == noisy.num_detectors and dem.num_observables == column


@pytest.mark.parametrize("basis", ["Z", "X"])
def test_column_gppm_leaves_the_other_logicals_untouched(basis):
    """Final readout in the complementary basis: every logical outside the column survives."""
    system, data, ancilla, builder, result, _, _ = _column_gppm(hgp_225_9_4, basis, final_basis=OTHER[basis])
    assert builder.circuit.num_observables == data.num_logicals - ancilla.num_logicals
    assert_noiseless(builder.circuit)


def test_gppm_without_the_intermediate_round_emits_no_readout_detectors():
    """Documented deviation: rounds_between=0 reproduces the paper's timing but the
    tracker then cannot compare the readout with the ancilla's stabilizers."""
    _, _, ancilla, builder, _, readout_detectors, _ = _column_gppm(hamming2, "Z", rounds_between=0)
    assert readout_detectors == 0
    assert_noiseless(builder.circuit)


@pytest.mark.parametrize("basis", ["Z", "X"])
def test_column_gppm_on_13_1_3_has_full_circuit_level_distance(basis):
    """Full-copy ancilla (k = 1, nothing to puncture): the Steane measurement keeps d = 3;
    without the intermediate round a single readout error goes undetected (distance 1)."""
    for rounds_between, expected in ((1, 3), (0, 1)):
        _, _, _, builder, _, _, _ = _column_gppm(hgp_13_1_3, basis, rounds=3, rounds_between=rounds_between)
        assert _distance(builder.build_noisy_circuit(NOISE, "circuit_level")) == expected


# ---------------------------------------------------------------------------
# Single logical qubit with a mask (Fig. 2(a))
# ---------------------------------------------------------------------------

def _single_logical(factory, basis, logical_id, rounds=2, final_basis=None):
    parent = factory()
    ancilla_local, mask_local = single_logical_ancillas(parent, logical_id, basis)
    system, regs, builder = _system(("data", factory()), ("anc", ancilla_local), ("mask", mask_local))
    data, ancilla, mask = regs["data"], regs["anc"], regs["mask"]
    _init_data(builder, data, OTHER[basis])
    initialize_ancilla(builder, ancilla)
    initialize_ancilla(builder, mask)
    _se(builder, rounds)
    result = apply_masked_gppm(builder, data, ancilla, mask)
    _se(builder, rounds)
    first_final = builder.tracker.total_measurements
    data_qubits = sorted(data.data_indices)
    builder.apply_data_readout({q: final_basis or basis for q in data_qubits})
    final_record = {q: first_final + i for i, q in enumerate(data_qubits)}
    return data, ancilla, mask, builder, result, final_record


@pytest.mark.parametrize("factory", [hgp_225_9_4, hamming2], ids=["hgp_225_9_4", "hamming2"])
@pytest.mark.parametrize("basis", ["Z", "X"])
@pytest.mark.parametrize("logical_id", [0, 4])
def test_masked_gppm_measures_exactly_one_logical(factory, basis, logical_id):
    data, ancilla, mask, builder, result, final_record = _single_logical(factory, basis, logical_id)
    circuit = builder.circuit
    assert result.logical_pairs == {"data": {a: d for a, d in result.logical_pairs["data"].items()}}
    assert list(result.logical_pairs["data"].values()) == [logical_id]
    assert set(result.outcome_records) == {("data", logical_id)}
    # Only the target is resolvable in the measured basis ...
    assert circuit.num_observables == 1
    assert_noiseless(circuit)
    records = result.outcome_records[("data", logical_id)]
    support = _final_readout_records(builder, data, logical_id, basis)
    (parity,) = _raw_parities(circuit, [tuple(records) + tuple(final_record[q] for q in support)])
    assert not parity.any()


@pytest.mark.parametrize("basis", ["Z", "X"])
def test_masked_gppm_leaves_every_other_logical_untouched(basis):
    data, ancilla, mask, builder, result, _ = _single_logical(hgp_225_9_4, basis, 4, final_basis=OTHER[basis])
    assert builder.circuit.num_observables == data.num_logicals - 1
    assert_noiseless(builder.circuit)


def test_masked_gppm_on_hamming2_has_full_circuit_level_distance():
    data, ancilla, mask, builder, result, _ = _single_logical(hamming2, "Z", 5, rounds=3)
    assert _distance(builder.build_noisy_circuit(NOISE, "circuit_level")) == 3


def test_masked_gppm_validation():
    parent = hgp_225_9_4()
    ancilla_local, mask_local = single_logical_ancillas(parent, 0, "Z")
    wrong_axis = PuncturedHGPCode(ancilla_local, "horizontal", [])          # same axis as the ancilla
    system, regs, builder = _system(("data", hgp_225_9_4()), ("anc", ancilla_local), ("mask", mask_local), ("wrong", wrong_axis))
    _init_data(builder, regs["data"], "X")
    for name in ("anc", "mask", "wrong"):
        initialize_ancilla(builder, regs[name])
    _se(builder, 1)
    with pytest.raises(ValueError, match="other axis"):
        apply_masked_gppm(builder, regs["data"], regs["anc"], regs["wrong"])
    with pytest.raises(ValueError, match="punctured from the ancilla"):
        apply_masked_gppm(builder, regs["data"], regs["data"], regs["mask"])
    with pytest.raises(TypeError, match="PuncturedHGPCode"):
        apply_gppm(builder, [regs["data"]], regs["data"])
    with pytest.raises(ValueError, match="at least one data patch"):
        apply_gppm(builder, [], regs["anc"])
    with pytest.raises(ValueError, match="rounds_between"):
        apply_gppm(builder, [regs["data"]], regs["anc"], rounds_between=-1)
    with pytest.raises(ValueError, match="appear once"):
        apply_gppm(builder, [regs["data"], regs["data"]], regs["anc"])


def test_a_read_out_ancilla_cannot_be_reused():
    parent = hgp_225_9_4()
    ancilla_local = PuncturedHGPCode(hgp_225_9_4(), "horizontal", information_bits(parent.h2)[1:])
    system, regs, builder = _system(("data", hgp_225_9_4()), ("anc", ancilla_local))
    _init_data(builder, regs["data"], "X")
    initialize_ancilla(builder, regs["anc"])
    _se(builder, 1)
    apply_gppm(builder, [regs["data"]], regs["anc"])
    with pytest.raises(ValueError, match="cannot be reused"):
        apply_gppm(builder, [regs["data"]], regs["anc"])


# ---------------------------------------------------------------------------
# Cross-block product Z̄ Z̄' (Algorithm 3 steps 3-5)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("factory", [hgp_225_9_4, hamming2], ids=["hgp_225_9_4", "hamming2"])
def test_cross_block_zz_gppm(factory):
    parent = factory()
    ancilla_local = PuncturedHGPCode(factory(), "horizontal", information_bits(parent.h2)[1:])
    system, regs, builder = _system(("data", factory()), ("target", factory()), ("anc", ancilla_local))
    data, target, ancilla = regs["data"], regs["target"], regs["anc"]
    _init_data(builder, data, "X")
    _init_data(builder, target, "X")
    initialize_ancilla(builder, ancilla)
    _se(builder, 2)
    result = apply_gppm(builder, [data, target], ancilla)
    _se(builder, 2)
    first_final = builder.tracker.total_measurements
    qubits = sorted(data.data_indices) + sorted(target.data_indices)
    builder.apply_data_readout({q: "Z" for q in qubits})
    final_record = {q: first_final + i for i, q in enumerate(qubits)}
    circuit = builder.circuit

    column = ancilla.num_logicals
    assert set(result.logical_pairs) == {"data", "target"}
    assert set(result.outcome_records) == {("*", a) for a in range(column)}
    assert circuit.num_observables == column           # Z̄ ⊕ Z̄' ⊕ m per row of the column
    assert_noiseless(circuit)
    checks = []
    for ancilla_id, records in ((a, result.outcome_records[("*", a)]) for a in range(column)):
        d_id = result.logical_pairs["data"][ancilla_id]
        t_id = result.logical_pairs["target"][ancilla_id]
        checks.append(tuple(records)
                      + tuple(final_record[q] for q in _final_readout_records(builder, data, d_id, "Z"))
                      + tuple(final_record[q] for q in _final_readout_records(builder, target, t_id, "Z")))
    assert all(not parity.any() for parity in _raw_parities(circuit, checks))
    if factory is hamming2:
        assert _distance(builder.build_noisy_circuit(NOISE, "circuit_level")) == 3
