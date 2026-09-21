"""Punctured ancilla codes and the homomorphic CNOT (Xu et al. Def. 3/4, Alg. 1, Sec. V A)."""

from __future__ import annotations

import itertools
import pathlib
import sys

import numpy as np
import pytest
import stim

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from conftest import assert_noiseless

from lightstim.ir.builder import CircuitBuilder
from lightstim.ir.logical_executor import LogicalExecutor
from lightstim.ir.qec_system import QECSystem
from lightstim.ir.tracker import SyndromeTracker
from lightstim.noise.config import NoiseConfig
from lightstim.qec_code.generic_css import GenericCSSColorationExtractionBlock
from lightstim.qec_code.HGP import (
    BinaryParityCheck,
    HGPCode,
    HGPCodeLogicalOpSet,
    PuncturedHGPCode,
    hgp_13_1_3,
    hgp_18_2_3,
    hgp_225_9_4,
    homomorphic_cnot_circuit,
    homomorphic_logical_pairs,
    homomorphic_qubit_pairs,
    information_bits,
    puncture_parity_check,
    register_hgp_op_set,
    retained_bit_positions,
)
from lightstim.utils.linear_algebra import row_echelon


HAMMING = np.array(
    [[1, 0, 1, 0, 1, 0, 1], [0, 1, 1, 0, 0, 1, 1], [0, 0, 0, 1, 1, 1, 1]], dtype=np.uint8
)
HAMMING_REDUNDANT = np.vstack([HAMMING, HAMMING[0] ^ HAMMING[1], HAMMING[1] ^ HAMMING[2]])
REP62 = np.array(
    [[1, 1, 0, 0, 0, 0], [0, 1, 1, 0, 0, 0], [0, 0, 0, 1, 1, 0], [0, 0, 0, 0, 1, 1]], dtype=np.uint8
)
NOISE = NoiseConfig(p_1q=1e-3, p_2q=1e-3, p_meas=1e-3, p_reset=1e-3, p_idle=1e-3)

CASES = {
    "hgp_225_9_4": hgp_225_9_4,                                  # 3 x 3 grid, full rank
    "hgp_13_1_3": hgp_13_1_3,                                    # k = 1 per axis
    "hgp_18_2_3": hgp_18_2_3,                                    # redundant check, C1xC2 logical
    "hamming2": lambda: HGPCode(HAMMING, d=3),                   # 4 x 4 grid
    "hamming_redundant2": lambda: HGPCode(HAMMING_REDUNDANT, d=3),  # 4x4 + 2x2 C1xC2 logicals
    "rep62_x_hamming": lambda: HGPCode(REP62, HAMMING, d=2),     # rectangular, non-symmetric
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _pauli(n, indices, letter):
    chars = ["_"] * n
    for q in indices:
        chars[q] = letter
    return stim.PauliString("".join(chars))


def _tableau(circuit, n):
    padded = stim.Circuit()
    padded.append("I", list(range(n)))
    return stim.Tableau.from_circuit(padded + circuit)


def _in_group(rows, n, pauli, letter):
    text = str(pauli)[1:]
    if any(ch not in ("_", letter) for ch in text):
        return False
    matrix = np.zeros((len(rows), n), dtype=np.uint8)
    for r, idx in enumerate(rows):
        matrix[r, list(idx)] = 1
    vector = np.zeros(n, dtype=np.uint8)
    vector[[i for i, ch in enumerate(text) if ch == letter]] = 1
    return row_echelon(np.vstack([matrix, vector]))[1] == row_echelon(matrix)[1]


def _records(patch):
    out = {}
    for r in patch.logical_ops:
        out.setdefault(int(r["logical_id"]), {})[r["type"]] = tuple(sorted(int(q) for q in r["data_indices"]))
    return out


class _SystemOnly:
    def __init__(self, system):
        self.system = system


def _two_patch_system(factory, axis, delete):
    """Register the data patch and its punctured ancilla side by side."""
    system = QECSystem()
    data = system.add_patch(factory(), name="data")
    x_max = max(c[0] for c in data.qubit_coords.values())
    ancilla = system.add_patch(PuncturedHGPCode(factory(), axis, delete), offset=(x_max + 3, 0), name="anc")
    return system, data, ancilla


def _deletions(factory, axis):
    seed = factory().h2 if axis == "horizontal" else factory().h1
    info = information_bits(seed)
    if len(info) < 2:
        return [()]
    return [(), (info[0],), tuple(info[1:])]


def _classical_distance(dense):
    n = dense.shape[1]
    best = None
    for weight in range(1, n + 1):
        for support in itertools.combinations(range(n), weight):
            v = np.zeros(n, dtype=np.uint8)
            v[list(support)] = 1
            if not np.any((dense @ v) % 2):
                return weight
    return best


# ---------------------------------------------------------------------------
# Classical puncturing (Def. 4, Sec. III E)
# ---------------------------------------------------------------------------

def test_puncture_deletes_columns_and_keeps_order():
    punctured = puncture_parity_check(HAMMING, [1, 4])
    assert punctured.shape == (3, 5)
    assert np.array_equal(punctured.to_dense(), HAMMING[:, [0, 2, 3, 5, 6]])
    assert retained_bit_positions(7, [1, 4]) == {0: 0, 2: 1, 3: 2, 5: 3, 6: 4}
    with pytest.raises(ValueError, match="Cannot delete bit 7"):
        puncture_parity_check(HAMMING, [7])


def test_information_bits_are_the_paper_information_set():
    """Every kernel basis vector has a 1 on its own information bit and 0 on the others (Eq. 18)."""
    from lightstim.qec_code.HGP import canonical_kernel_basis

    for dense in (HAMMING, HAMMING_REDUNDANT, REP62, hgp_225_9_4().h1.to_dense()):
        info = information_bits(dense)
        basis = canonical_kernel_basis(dense)
        assert len(info) == dense.shape[1] - row_echelon(dense)[1]        # n - rank(H)
        assert np.array_equal(basis.basis[:, list(info)], np.eye(len(info), dtype=np.uint8))
        # The complement is a set of rank(H) independent columns.
        complement = [j for j in range(dense.shape[1]) if j not in info]
        assert row_echelon(dense[:, complement].T.copy())[1] == len(complement)


@pytest.mark.parametrize("dense", [HAMMING, HAMMING_REDUNDANT, REP62])
def test_puncturing_information_bits_keeps_rank_and_distance(dense):
    info = information_bits(dense)
    original_distance = _classical_distance(dense)
    for r in range(1, len(info)):
        for delete in itertools.combinations(info, r):
            punctured = puncture_parity_check(dense, delete).to_dense()
            assert row_echelon(punctured)[1] == row_echelon(dense)[1]
            assert _classical_distance(punctured) >= original_distance


def test_puncturing_a_non_information_bit_can_keep_the_logical_bit():
    """Why the restriction exists: deleting a non-information bit may drop the
    rank instead of removing a logical bit (Eq. 19 no longer applies)."""
    dense = np.array([[1, 0, 0], [0, 1, 1]], dtype=np.uint8)      # k = 1, info bit = 2
    assert information_bits(dense) == (2,)
    punctured = puncture_parity_check(dense, [0]).to_dense()       # delete a non-information bit
    assert row_echelon(punctured)[1] == 1                          # rank 2 -> 1
    assert punctured.shape[1] - row_echelon(punctured)[1] == 1     # k stays 1: nothing removed
    with pytest.raises(ValueError, match="information set"):
        PuncturedHGPCode(HGPCode(dense), "horizontal", [0])


# ---------------------------------------------------------------------------
# PuncturedHGPCode construction and validation
# ---------------------------------------------------------------------------

def test_punctured_ancilla_sizes_and_metadata():
    parent = hgp_225_9_4()
    info = information_bits(parent.h2)
    delete = info[1:]
    ancilla = PuncturedHGPCode(parent, "horizontal", delete)
    n1, m1 = parent.h1.num_bits, parent.h1.num_checks
    n2, m2 = parent.h2.num_bits, parent.h2.num_checks
    assert ancilla.h2.shape == (m2, n2 - len(delete))
    assert ancilla.num_data_qubits == n1 * (n2 - len(delete)) + m1 * m2
    assert ancilla.num_logicals == 3            # one column of the 3 x 3 grid
    assert ancilla.code_distance == parent.code_distance
    assert ancilla.puncture.axis == "horizontal"
    assert ancilla.puncture.deleted_bits == tuple(sorted(delete))
    assert ancilla.puncture.control == "parent"
    assert ancilla.puncture.matches_parent(parent)
    assert isinstance(ancilla, HGPCode)

    vertical = PuncturedHGPCode(parent, "vertical", information_bits(parent.h1)[:1])
    assert vertical.h1.shape == (m1, n1 - 1)
    assert vertical.num_logicals == 6
    assert vertical.puncture.control == "ancilla"


def test_punctured_ancilla_rejects_bad_requests():
    parent = HGPCode(HAMMING)
    info = information_bits(HAMMING)
    non_info = next(j for j in range(7) if j not in info)
    with pytest.raises(ValueError, match="information set"):
        PuncturedHGPCode(parent, "horizontal", [non_info])
    with pytest.raises(ValueError, match="keep at least one"):
        PuncturedHGPCode(parent, "horizontal", info)
    with pytest.raises(ValueError, match="axis"):
        PuncturedHGPCode(parent, "diagonal", [])
    with pytest.raises(TypeError, match="HGPCode"):
        PuncturedHGPCode("not a patch", "horizontal", [])
    with pytest.raises(ValueError, match="Cannot delete bit"):
        PuncturedHGPCode(parent, "horizontal", [99])
    for bad in ("24", [1.5], [True], [np.float64(2.0)]):
        with pytest.raises(TypeError, match="integer"):
            PuncturedHGPCode(parent, "horizontal", bad)
    assert PuncturedHGPCode(parent, "horizontal", [np.int64(info[0])]).puncture.deleted_bits == (info[0],)


def test_get_info_carries_the_puncture():
    parent = hgp_225_9_4()
    ancilla = PuncturedHGPCode(parent, "vertical", information_bits(parent.h1)[:1])
    info = ancilla.get_info()["puncture"]
    assert info["axis"] == "vertical" and info["control"] == "ancilla"
    assert info["deleted_bits"] == (information_bits(parent.h1)[0],)
    assert info["parent_h1_shape"] == parent.h1.shape


def test_deleting_nothing_reproduces_the_parent():
    parent = hgp_18_2_3()
    ancilla = PuncturedHGPCode(parent, "horizontal", [])
    assert np.array_equal(ancilla.h2.to_dense(), parent.h2.to_dense())
    assert ancilla.num_data_qubits == parent.num_data_qubits
    assert ancilla.num_logicals == parent.num_logicals
    pairs = homomorphic_qubit_pairs(parent, ancilla)
    assert [a for a, _ in pairs] == [b for _, b in pairs]      # identity on local indices


# ---------------------------------------------------------------------------
# Qubit pairing (Alg. 1 step 2) and logical pairing (Eq. 34)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("axis", ["horizontal", "vertical"])
def test_pairs_cover_every_ancilla_qubit_once_and_skip_deleted_columns(name, axis):
    factory = CASES[name]
    for delete in _deletions(factory, axis):
        system, data, ancilla = _two_patch_system(factory, axis, delete)
        pairs = homomorphic_qubit_pairs(data, ancilla, _SystemOnly(system))
        assert sorted(a for _, a in pairs) == sorted(ancilla.data_indices)
        parents = [p for p, _ in pairs]
        assert len(set(parents)) == len(parents)
        assert set(parents) <= set(data.data_indices)
        # Deleted data qubits: whole columns (rows) of V1 x V2, nothing of C1 x C2.
        missing = set(data.data_indices) - set(parents)
        seed = data.h2 if axis == "horizontal" else data.h1
        other = data.h1 if axis == "horizontal" else data.h2
        assert len(missing) == len(delete) * other.num_bits
        # Local pairing (local patches, no builder) has the same size.
        local_pairs = homomorphic_qubit_pairs(factory(), PuncturedHGPCode(factory(), axis, delete))
        assert len(local_pairs) == len(pairs)


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("axis", ["horizontal", "vertical"])
def test_logical_pairs_match_grid_coordinates(name, axis):
    factory = CASES[name]
    for delete in _deletions(factory, axis):
        system, data, ancilla = _two_patch_system(factory, axis, delete)
        mapping = homomorphic_logical_pairs(data, ancilla, _SystemOnly(system))
        assert sorted(mapping) == list(range(ancilla.num_logicals))
        assert len(set(mapping.values())) == len(mapping)          # injective
        by_id = lambda patch, i: next(r for r in patch.logical_pairs if r["logical_id"] == i)
        seed = data.h2 if axis == "horizontal" else data.h1
        from lightstim.qec_code.HGP import canonical_kernel_basis
        parent_pivots = canonical_kernel_basis(seed).pivots
        ancilla_pivots = canonical_kernel_basis(ancilla.h2 if axis == "horizontal" else ancilla.h1).pivots
        position = retained_bit_positions(seed.num_bits, delete)
        for ancilla_id, data_id in mapping.items():
            a, d = by_id(ancilla, ancilla_id), by_id(data, data_id)
            assert a["sector"] == d["sector"]
            if a["sector"] == "bit_bit":
                axis_index = 1 if axis == "horizontal" else 0
                other = 1 - axis_index
                assert a["seed_indices"][other] == d["seed_indices"][other]
                # Same physical column/row: the ancilla pivot is the retained position of the parent pivot.
                assert ancilla_pivots[a["seed_indices"][axis_index]] == position[parent_pivots[d["seed_indices"][axis_index]]]
            else:
                assert tuple(a["seed_indices"]) == tuple(d["seed_indices"])
        # Unmapped data logicals are exactly the ones on the deleted columns/rows.
        unmapped = set(range(data.num_logicals)) - set(mapping.values())
        for data_id in unmapped:
            d = by_id(data, data_id)
            assert d["sector"] == "bit_bit"
            axis_index = 1 if axis == "horizontal" else 0
            assert parent_pivots[d["seed_indices"][axis_index]] in delete


def test_pairs_reject_wrong_parent_and_local_patches():
    system, data, ancilla = _two_patch_system(hgp_225_9_4, "horizontal", (9,))
    other = system.add_patch(HGPCode(HAMMING), offset=(80, 0), name="other")
    with pytest.raises(ValueError, match="different code"):
        homomorphic_qubit_pairs(other, ancilla, _SystemOnly(system))
    with pytest.raises(ValueError, match="global patch"):
        homomorphic_qubit_pairs(hgp_225_9_4(), ancilla, _SystemOnly(system))
    with pytest.raises(ValueError, match="global patch"):
        homomorphic_qubit_pairs(data, PuncturedHGPCode(hgp_225_9_4(), "horizontal", (9,)), _SystemOnly(system))
    with pytest.raises(TypeError, match="PuncturedHGPCode"):
        homomorphic_qubit_pairs(data, data, _SystemOnly(system))
    # Global patches without a builder would mix global and local indices.
    with pytest.raises(ValueError, match="global patch"):
        homomorphic_qubit_pairs(data, ancilla)
    with pytest.raises(ValueError, match="global patch"):
        homomorphic_logical_pairs(data, ancilla)


# ---------------------------------------------------------------------------
# Exact Clifford action (stim tableau): Def. 3 validity and Eq. 35-36
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("axis", ["horizontal", "vertical"])
def test_homomorphic_cnot_preserves_stabilizers_and_acts_as_logical_cnots(name, axis):
    factory = CASES[name]
    for delete in _deletions(factory, axis):
        system, data, ancilla = _two_patch_system(factory, axis, delete)
        n = system.num_qubits
        pairs = homomorphic_qubit_pairs(data, ancilla, _SystemOnly(system))
        control = ancilla.puncture.control
        tableau = _tableau(homomorphic_cnot_circuit(pairs, control), n)

        # (a) The joint stabilizer group is mapped into itself, signs +1.
        x_rows = [s["data_indices"] for p in (data, ancilla) for s in p.stabilizers if s["type"] == "X"]
        z_rows = [s["data_indices"] for p in (data, ancilla) for s in p.stabilizers if s["type"] == "Z"]
        for rows, letter in ((x_rows, "X"), (z_rows, "Z")):
            for row in rows:
                image = tableau(_pauli(n, row, letter))
                assert image.sign == 1
                assert _in_group(rows, n, image, letter)

        # (b) Exact logical CNOTs between paired logicals (Eq. 35), in the
        #     direction of the puncture axis (Sec. V A).
        data_records, ancilla_records = _records(data), _records(ancilla)
        mapping = homomorphic_logical_pairs(data, ancilla, _SystemOnly(system))
        for ancilla_id, data_id in mapping.items():
            if control == "parent":
                c, t = data_records[data_id], ancilla_records[ancilla_id]
            else:
                c, t = ancilla_records[ancilla_id], data_records[data_id]
            xc, xt = _pauli(n, c["X"], "X"), _pauli(n, t["X"], "X")
            zc, zt = _pauli(n, c["Z"], "Z"), _pauli(n, t["Z"], "Z")
            assert tableau(xc) == xc * xt
            assert tableau(zt) == zc * zt
            assert tableau(xt) == xt
            assert tableau(zc) == zc

        # (c) Data logicals on the deleted columns/rows are untouched (Eq. 36).
        for data_id in set(data_records) - set(mapping.values()):
            for letter in ("X", "Z"):
                pauli = _pauli(n, data_records[data_id][letter], letter)
                assert tableau(pauli) == pauli


def test_nested_puncture_builds_the_mask_code_of_fig_2a():
    """Q' keeps one column of Q; Q'' = Q' with one row deleted (Fig. 2(a) mask code)."""
    parent = hgp_225_9_4()
    info_h2, info_h1 = information_bits(parent.h2), information_bits(parent.h1)
    q1 = PuncturedHGPCode(parent, "horizontal", info_h2[1:])        # 3 x 1 logical grid
    q2 = PuncturedHGPCode(q1, "vertical", info_h1[:1])              # 2 x 1: first row removed
    assert q1.num_logicals == 3 and q2.num_logicals == 2
    assert q2.puncture.control == "ancilla"
    assert q2.puncture.matches_parent(q1) and not q2.puncture.matches_parent(parent)

    system = QECSystem()
    g1 = system.add_patch(q1, name="q1")
    g2 = system.add_patch(q2, offset=(60, 0), name="q2")
    n = system.num_qubits
    pairs = homomorphic_qubit_pairs(g1, g2, _SystemOnly(system))
    tableau = _tableau(homomorphic_cnot_circuit(pairs, "ancilla"), n)
    mapping = homomorphic_logical_pairs(g1, g2, _SystemOnly(system))
    assert len(mapping) == 2
    r1, r2 = _records(g1), _records(g2)
    for q2_id, q1_id in mapping.items():
        xc = _pauli(n, r2[q2_id]["X"], "X")
        assert tableau(xc) == xc * _pauli(n, r1[q1_id]["X"], "X")      # Q'' controls
    (untouched,) = set(r1) - set(mapping.values())
    for letter in ("X", "Z"):
        pauli = _pauli(n, r1[untouched][letter], letter)
        assert tableau(pauli) == pauli


# ---------------------------------------------------------------------------
# Op set and executor
# ---------------------------------------------------------------------------

def _builder_for(system):
    tracker = SyndromeTracker(num_qubits=system.num_qubits, expected_num_logicals=system.num_logicals)
    builder = CircuitBuilder(tracker=tracker, system_config=system, if_detector=True)
    system.register_tracker(tracker)
    system.register_builder(builder)
    builder.write_coordinates()
    return builder


def test_op_set_emits_one_cx_layer_in_the_puncture_direction():
    for axis, control in (("horizontal", "parent"), ("vertical", "ancilla")):
        system, data, ancilla = _two_patch_system(hgp_225_9_4, axis, (9,))
        builder = _builder_for(system)
        data_qubits = sorted(system.data_indices)
        builder.initialize({q: "Z" for q in data_qubits}, system.num_qubits)
        system.active_qubit_indices.update(data_qubits)
        before = len(builder.circuit.flattened())
        emitted = HGPCodeLogicalOpSet().homomorphic_cnot(builder, data, ancilla)
        pairs = homomorphic_qubit_pairs(data, ancilla, builder)
        assert emitted == homomorphic_cnot_circuit(pairs, control)
        cx = [inst for inst in builder.circuit.flattened()[before:] if inst.name == "CX"]
        assert len(cx) == 1
        targets = [t.value for t in cx[0].targets_copy()]
        controls, targets_ = targets[0::2], targets[1::2]
        if control == "parent":
            assert set(controls) <= set(data.data_indices) and set(targets_) == set(ancilla.data_indices)
        else:
            assert set(controls) == set(ancilla.data_indices) and set(targets_) <= set(data.data_indices)


def test_op_set_validation():
    system, data, ancilla = _two_patch_system(hgp_225_9_4, "horizontal", (9,))
    builder = _builder_for(system)
    builder.initialize({q: "Z" for q in sorted(system.data_indices)}, system.num_qubits)
    op_set = HGPCodeLogicalOpSet()
    with pytest.raises(TypeError, match="PuncturedHGPCode"):
        op_set.homomorphic_cnot(builder, data, data)
    with pytest.raises(TypeError, match="HGPCode"):
        op_set.homomorphic_cnot(builder, "data", ancilla)
    with pytest.raises(ValueError, match="global patch"):
        op_set.homomorphic_cnot(builder, hgp_225_9_4(), ancilla)
    # The inherited transversal CNOT cannot pair a smaller ancilla (it
    # requires the same patch type and size and pairs by sorted index): this
    # is why the homomorphic CNOT pairs by coordinates instead.
    with pytest.raises(ValueError, match="mismatch"):
        op_set.transversal_cnot(builder, data, ancilla)


def test_executor_dispatch():
    system, data, ancilla = _two_patch_system(hgp_18_2_3, "horizontal", ())
    builder = _builder_for(system)
    builder.initialize({q: "Z" for q in sorted(system.data_indices)}, system.num_qubits)
    executor = LogicalExecutor(builder)
    executor.register_op_set(HGPCode, HGPCodeLogicalOpSet())
    executor.apply_logical_operation("homomorphic_cnot", [data, ancilla])
    cx = [inst for inst in builder.circuit.flattened() if inst.name == "CX"]
    assert len(cx) == 1 and len(cx[0].targets_copy()) == 2 * len(ancilla.data_indices)


def test_executor_runs_the_nested_fig_2a_steps_with_register_hgp_op_set():
    """Step 1 of Fig. 2(a) has the ancilla Q' as first patch (Q'' -> Q'); the
    executor dispatches on the exact class, so PuncturedHGPCode must be
    registered too, which register_hgp_op_set does."""
    parent = hgp_225_9_4()
    q1 = PuncturedHGPCode(parent, "horizontal", information_bits(parent.h2)[1:])
    q2 = PuncturedHGPCode(q1, "vertical", information_bits(parent.h1)[:1])
    system = QECSystem()
    data = system.add_patch(parent, name="data")
    g1 = system.add_patch(q1, offset=(40, 0), name="q1")
    g2 = system.add_patch(q2, offset=(80, 0), name="q2")
    builder = _builder_for(system)
    builder.initialize({q: "Z" for q in sorted(system.data_indices)}, system.num_qubits)

    executor = LogicalExecutor(builder)
    executor.register_op_set(HGPCode, HGPCodeLogicalOpSet())
    with pytest.raises(ValueError, match="No LogicalOpSet registered for PuncturedHGPCode"):
        executor.apply_logical_operation("homomorphic_cnot", [g1, g2])

    executor = LogicalExecutor(builder)
    op_set = register_hgp_op_set(executor)
    assert isinstance(op_set, HGPCodeLogicalOpSet)
    executor.apply_logical_operation("homomorphic_cnot", [g1, g2])      # step 1: Q'' -> Q'
    executor.apply_logical_operation("homomorphic_cnot", [data, g1])    # step 2: Q  -> Q'
    cx = [inst for inst in builder.circuit.flattened() if inst.name == "CX"]
    assert len(cx) == 2
    assert len(cx[0].targets_copy()) == 2 * len(g2.data_indices)
    assert len(cx[1].targets_copy()) == 2 * len(g1.data_indices)


# ---------------------------------------------------------------------------
# End to end: two patches, SE, homomorphic CNOT, readout
# ---------------------------------------------------------------------------

def _end_to_end(factory, axis, delete, basis, rounds=2, noise=None):
    system, data, ancilla = _two_patch_system(factory, axis, delete)
    builder = _builder_for(system)
    se_block = GenericCSSColorationExtractionBlock(system)
    blocks = getattr(se_block, "measurement_blocks", None)
    data_qubits = sorted(system.data_indices)
    builder.initialize({q: basis for q in data_qubits}, system.num_qubits)
    system.active_qubit_indices.update(data_qubits)
    builder.apply_syndrome_extraction(se_block.circuit, rounds=rounds, measurement_blocks=blocks)
    detectors_before = builder.circuit.num_detectors
    HGPCodeLogicalOpSet().homomorphic_cnot(builder, data, ancilla)
    builder.apply_syndrome_extraction(se_block.circuit, rounds=rounds, measurement_blocks=blocks)
    # The CNOT layer preserves the joint stabilizer group: full detector count after it.
    num_stabilizers = len(data.stabilizers) + len(ancilla.stabilizers)
    assert builder.circuit.num_detectors - detectors_before == rounds * num_stabilizers
    builder.apply_data_readout({q: basis for q in data_qubits})
    circuit = builder.circuit
    if noise is not None:
        circuit = builder.build_noisy_circuit(noise, "circuit_level")
    return circuit, data, ancilla


@pytest.mark.parametrize("name", ["hgp_225_9_4", "hgp_18_2_3", "hamming_redundant2"])
@pytest.mark.parametrize("axis,basis", [("horizontal", "Z"), ("vertical", "X")])
def test_end_to_end_noiseless_with_both_blocks_read_out(name, axis, basis):
    """Z-type use (horizontal puncture, |0> ancilla) and X-type use (vertical, |+> ancilla)."""
    factory = CASES[name]
    delete = _deletions(factory, axis)[-1]
    circuit, data, ancilla = _end_to_end(factory, axis, delete, basis)
    assert circuit.num_observables == data.num_logicals + ancilla.num_logicals
    assert_noiseless(circuit)


def test_end_to_end_noisy_has_a_detector_error_model():
    circuit, data, ancilla = _end_to_end(hgp_225_9_4, "horizontal", (10, 11), "Z", noise=NOISE)
    dem = circuit.detector_error_model(decompose_errors=False)
    assert dem.num_observables == data.num_logicals + ancilla.num_logicals
    assert dem.num_detectors == circuit.num_detectors
