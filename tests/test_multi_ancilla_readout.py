"""Explicit disposable-ancilla sets for Bell and flag readout blocks."""
import copy

import numpy as np
import pytest
import stim

from lightstim.ir.builder import CircuitBuilder
from lightstim.ir.qec_patch import QECPatch
from lightstim.ir.qec_system import QECSystem
from lightstim.ir.tracker import SyndromeTracker


class _TwoAncillaCheck(QECPatch):
    def _process_params(self):
        pass

    def build(self):
        self.num_logicals = 1
        for q in range(2):
            self.add_qubit(q, 0, role='data')
        for q in range(2, 4):
            self.add_qubit(q, 0, role='syndrome_z')
        self.create_stim_stabilizer({(0, 0): 'Z', (1, 0): 'Z'}, (2, 0), 'Z')
        self.create_stim_logical({(0, 0): 'Z'}, 'Z')
        self.create_stim_logical({(0, 0): 'X', (1, 0): 'X'}, 'X')


def _builder():
    system = QECSystem()
    system.add_patch(_TwoAncillaCheck(), name='pair')
    tracker = SyndromeTracker(4, 1)
    builder = CircuitBuilder(tracker, system)
    builder.initialize({0: 'Z', 1: 'Z'}, 4)
    return builder


def _assert_noiseless(builder):
    builder.apply_data_readout({0: 'Z', 1: 'Z'})
    circuit = builder.to_stim_circuit()
    assert circuit.num_observables == 1
    circuit.detector_error_model()
    assert not circuit.compile_detector_sampler(seed=91).sample(64, append_observables=True).any()
    return circuit


def test_registered_flag_ancilla_can_be_disposable_without_own_stabilizer_record():
    builder = _builder()
    assert set(builder.system.active_syndrome_indices) == {2}
    assert builder.system.syndrome_indices == {2, 3}
    block = stim.Circuit('R 2 3\nCX 0 2 1 2\nM 2 3')
    builder.apply_syndrome_extraction(block, rounds=3, syndrome_qubits={2, 3})
    assert builder.tracker.total_measurements == 6
    circuit = _assert_noiseless(builder)
    assert circuit.num_detectors >= 6


def test_bell_check_random_individual_results_have_deterministic_parity():
    builder = _builder()
    for _ in range(2):
        builder.initialize({2: 'X', 3: 'Z'}, 4)
        builder.apply_unitary_block(stim.Circuit('CX 2 3'))
        builder.apply_syndrome_extraction(stim.Circuit('CZ 2 0 3 1\nMX 2 3'),
                                          syndrome_qubits={2, 3})
    circuit = _assert_noiseless(builder)
    raw = circuit.compile_sampler(seed=37).sample(256)
    for i in (0, 2):
        assert set(raw[:, i]) == {False, True}
        assert np.array_equal(raw[:, i], raw[:, i + 1])
        assert circuit.has_flow(stim.Flow(measurements=[i, i + 1]), unsigned=True)


@pytest.mark.parametrize('explicit', [{0, 2, 3}, {2, 3, 99}, {2}, {3}])
def test_invalid_disposable_sets_fail_before_circuit_or_tracker_mutation(explicit):
    builder = _builder()
    before = builder.circuit.copy()
    banks = [(bank.matrix.copy(), copy.deepcopy(bank.records))
             for bank in (builder.tracker.stabilizers, builder.tracker.logicals)]
    with pytest.raises(ValueError):
        builder.apply_syndrome_extraction(
            stim.Circuit('R 2 3\nCX 0 2 1 2\nM 2 3'), syndrome_qubits=explicit)
    assert builder.circuit == before
    assert builder.tracker.total_measurements == 0
    for bank, (matrix, records) in zip((builder.tracker.stabilizers, builder.tracker.logicals), banks):
        assert np.array_equal(bank.matrix, matrix)
        assert bank.records == records


def test_default_single_ancilla_path_remains_unchanged():
    builder = _builder()
    builder.apply_syndrome_extraction(stim.Circuit('R 2\nCX 0 2 1 2\nM 2'), rounds=2)
    _assert_noiseless(builder)
