"""Record-controlled Pauli frames must agree with the physical Stim circuit."""
import copy

import numpy as np
import pytest
import stim

from lightstim.ir.builder import CircuitBuilder
from lightstim.ir.logical_history import LogicalHistoryRelation
from lightstim.ir.qec_patch import QECPatch
from lightstim.ir.qec_system import QECSystem
from lightstim.ir.tracker import SyndromeTracker
from lightstim.noise.config import NoiseConfig
from lightstim.noise.injector import NoiseInjector


class _FeedbackRegister(QECPatch):
    """One logical data qubit plus an independently measured X-check qubit."""
    def _process_params(self):
        pass

    def build(self):
        self.num_logicals = 1
        for q in range(2):
            self.add_qubit(q, 0, role='data')
        self.add_qubit(2, 0, role='syndrome_x')
        self.create_stim_stabilizer({(0, 0): 'X'}, (2, 0), 'X')
        for letter in 'XZ':
            self.create_stim_logical({(1, 0): letter}, letter)


def _builder(target_basis='Z', *, entangle=False, logical_tracking='slabs'):
    system = QECSystem()
    system.add_patch(_FeedbackRegister(), name='test')
    tracker = SyndromeTracker(3, 1, logical_tracking=logical_tracking)
    builder = CircuitBuilder(tracker, system)
    builder.initialize({0: 'X', 1: target_basis}, 3)
    builder.apply_syndrome_extraction(stim.Circuit('RX 2\nCX 2 0\nMX 2'))
    if entangle:
        builder.apply_unitary_block(stim.Circuit('CX 0 1'))
    builder.apply_mid_data_readout({0: 'Z'})
    assert builder.circuit.num_measurements == 2
    return builder


def _snapshot(tracker):
    return [(bank.matrix.copy(), copy.deepcopy(bank.records))
            for bank in (tracker.stabilizers, tracker.logicals, tracker.absorbed_ops)]


def _assert_snapshot(tracker, snapshot):
    for bank, (matrix, records) in zip(
            (tracker.stabilizers, tracker.logicals, tracker.absorbed_ops), snapshot):
        assert np.array_equal(bank.matrix, matrix)
        assert bank.records == records


def _assert_unsigned_annotation_flows(circuit):
    offset = 0
    observables = {}
    for inst in circuit.flattened():
        if inst.name in ('DETECTOR', 'OBSERVABLE_INCLUDE'):
            support = {offset + target.value for target in inst.targets_copy()}
            if inst.name == 'DETECTOR':
                assert circuit.has_flow(stim.Flow(measurements=sorted(support)), unsigned=True)
            else:
                key = int(inst.gate_args_copy()[0])
                observables.setdefault(key, set()).symmetric_difference_update(support)
        offset += inst.num_measurements
    assert observables
    for support in observables.values():
        assert circuit.has_flow(stim.Flow(measurements=sorted(support)), unsigned=True)


@pytest.mark.parametrize('letter', ['X', 'Y', 'Z'])
@pytest.mark.parametrize('invert', [False, True])
@pytest.mark.parametrize('logical_tracking', ['legacy', 'slabs'])
def test_random_feedback_after_clifford_has_automatic_observables(letter, invert, logical_tracking):
    basis = 'X' if letter == 'Z' else 'Z'
    builder = _builder(basis, logical_tracking=logical_tracking)
    ticks = builder.circuit.num_ticks
    builder.apply_pauli_feedback({1: letter}, [1], invert=invert)
    assert builder.circuit.num_ticks == ticks
    builder.apply_unitary_block(stim.Circuit('H 1'))
    builder.apply_data_readout({1: 'Z' if basis == 'X' else 'X'})
    circuit = builder.to_stim_circuit()
    assert circuit.num_observables == 1
    raw = circuit.compile_sampler(seed=78).sample(256)
    assert set(raw[:, 1]) == {False, True}
    assert np.all(raw[:, 2] == (raw[:, 1] ^ invert))
    assert not circuit.compile_detector_sampler(seed=51).sample(128, append_observables=True).any()
    _assert_unsigned_annotation_flows(circuit)
    circuit.detector_error_model()


@pytest.mark.parametrize('logical_tracking', ['legacy', 'slabs'])
def test_feedback_updates_three_live_banks_and_leaves_history_unchanged(logical_tracking):
    tracker = SyndromeTracker(3, logical_tracking=logical_tracking)
    tracker.total_measurements = 4
    # Independent physical signs for Z0, Z1 and Z2. The bank partition is
    # immaterial to Pauli conjugation; all three must obey the same law.
    for q, bank in enumerate((tracker.stabilizers, tracker.logicals, tracker.absorbed_ops)):
        row = np.zeros((1, 6), dtype=np.uint8)
        row[0, 3 + q] = 1
        bank.add_stabilizers(row, [[q]])
    tracker.logical_history = [LogicalHistoryRelation((0, 1), 1, (0,))]
    before = _snapshot(tracker)
    history = list(tracker.logical_history)
    tracker.process_pauli_feedback({0: 'X', 1: 'Y', 2: 'Z'}, [1, 3, 3])
    assert tracker.stabilizers.records == [[0, 1]]
    assert tracker.logicals.records == [[]]
    assert tracker.absorbed_ops.records == [[2]]
    assert tracker.logical_history == history
    for bank, (matrix, _) in zip((tracker.stabilizers, tracker.logicals, tracker.absorbed_ops), before):
        assert np.array_equal(bank.matrix, matrix)
    tracker.process_unitary_block(stim.Circuit('H 0 1 2'))
    assert tracker.stabilizers.records == [[0, 1]]
    assert tracker.logicals.records == [[]]
    assert tracker.absorbed_ops.records == [[2]]
    assert tracker.stabilizers.matrix[0, 0] and tracker.logicals.matrix[0, 1]


def test_duplicate_parities_cancel_in_builder_and_tracker():
    builder = _builder()
    circuit = builder.circuit.copy()
    snapshot = _snapshot(builder.tracker)
    builder.apply_pauli_feedback({1: 'X'}, [1, 1])
    assert builder.circuit == circuit
    _assert_snapshot(builder.tracker, snapshot)
    builder.tracker.process_pauli_feedback({1: 'X'}, [1, 1])
    _assert_snapshot(builder.tracker, snapshot)


@pytest.mark.parametrize('pauli,records', [({1: 'bad'}, [1]), ({3: 'X'}, [1]),
    ({-1: 'X'}, [1]), ({0.5: 'X'}, [1]), ({1: 'X'}, [-1]),
    ({1: 'X'}, [2]), ({1: 'X'}, [1, 0.5]), ({1: 'X'}, [1, '1'])])
def test_invalid_feedback_is_atomic(pauli, records):
    builder = _builder()
    for call in (builder.apply_pauli_feedback, builder.tracker.process_pauli_feedback):
        circuit = builder.circuit.copy()
        snapshot = _snapshot(builder.tracker)
        with pytest.raises(ValueError):
            call(pauli, records)
        assert builder.circuit == circuit
        _assert_snapshot(builder.tracker, snapshot)


def test_builder_rejects_record_stream_mismatch_atomically():
    builder = _builder()
    builder.tracker.total_measurements += 1
    circuit = builder.circuit.copy()
    snapshot = _snapshot(builder.tracker)
    with pytest.raises(ValueError, match='synchronized'):
        builder.apply_pauli_feedback({1: 'X'}, [1])
    assert builder.circuit == circuit
    _assert_snapshot(builder.tracker, snapshot)


def test_noisy_measurement_propagates_through_conditional_correction_into_dem():
    builder = _builder(entangle=True)
    builder.apply_pauli_feedback({1: 'X'}, [1])
    builder.apply_unitary_block(stim.Circuit('H 1'))
    builder.apply_data_readout({1: 'X'})
    clean = builder.to_stim_circuit()
    assert not clean.compile_detector_sampler(seed=23).sample(128, append_observables=True).any()
    _assert_unsigned_annotation_flows(clean)
    noisy = stim.Circuit()
    for inst in clean:
        if inst.name == 'M' and inst.targets_copy() == [stim.GateTarget(0)]:
            noisy.append('M', [0], 0.125)
        else:
            noisy.append(inst)
    errors = [inst for inst in noisy.detector_error_model() if inst.type == 'error']
    assert len(errors) == 1
    assert errors[0].args_copy() == [0.125]
    assert errors[0].targets_copy() == [stim.target_logical_observable_id(0)]
    det, obs = noisy.compile_detector_sampler(seed=5).sample(4096, separate_observables=True)
    assert not det.any()
    assert 0.08 < obs.mean() < 0.17


def test_virtual_feedback_adds_neither_gate_noise_nor_idle_timestep():
    circuit = stim.Circuit('CX[noiseless] rec[-1] 1\nZ[noiseless] 0')
    cfg = NoiseConfig(p_1q=0.1, p_2q=0.1, p_idle=0.1)
    for injector in (NoiseInjector.from_circuit_level(cfg, [0, 1]),
                     NoiseInjector.from_circuit_level_with_idling(cfg, [0, 1])):
        assert injector.inject_noise(circuit, active_qubits={0, 1}) == circuit
