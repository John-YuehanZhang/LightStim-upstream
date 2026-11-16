"""Disposable readout of an ancilla prepared in an earlier physical phase."""
import numpy as np
import pytest
import stim

from lightstim.ir.builder import CircuitBuilder
from lightstim.ir.qec_patch import QECPatch
from lightstim.ir.qec_system import QECSystem
from lightstim.ir.tracker import SyndromeTracker


class _PairCode(QECPatch):
    def _process_params(self):
        pass

    def build(self):
        self.num_logicals = 1
        self.add_qubit(0, 0, role="data")
        self.add_qubit(1, 0, role="data")
        self.add_qubit(2, 0, role="syndrome_z")
        self.create_stim_stabilizer({(0, 0): "Z", (1, 0): "Z"}, (2, 0), "Z")
        self.create_stim_logical({(0, 0): "Z"}, "Z")
        self.create_stim_logical({(0, 0): "X", (1, 0): "X"}, "X")


def _build(basis, policy, separate):
    system = QECSystem()
    system.add_patch(_PairCode(), name="p")
    tracker = SyndromeTracker(3, 1, logical_tracking=policy)
    builder = CircuitBuilder(tracker, system)
    builder.initialize({0: basis, 1: basis}, 3)
    for _ in range(3):
        if separate:
            builder.initialize({2: "X"}, 3)
            block = stim.Circuit("CZ 2 0 2 1\nMX 2")
        else:
            block = stim.Circuit("RX 2\nCZ 2 0 2 1\nMX 2")
        builder.apply_syndrome_extraction(block)
        # No fictitious reset factor or measured identity row remains live.
        for bank in (tracker.stabilizers, tracker.logicals):
            assert not bank.matrix[:, [2, 5]].any()
            assert np.all(np.any(bank.matrix, axis=1))
        assert tracker.logicals.count == 1
    builder.apply_data_readout({0: basis, 1: basis})
    return builder.to_stim_circuit()


@pytest.mark.parametrize("basis", ["X", "Z"])
@pytest.mark.parametrize("policy", ["legacy", "slabs"])
def test_external_preparation_matches_in_block_reset(basis, policy):
    standard = _build(basis, policy, False)
    external = _build(basis, policy, True)
    assert external.num_observables == standard.num_observables == 1
    assert external.num_detectors == standard.num_detectors
    external.detector_error_model()
    standard.detector_error_model()
    samples = external.compile_detector_sampler(seed=103).sample(256, append_observables=True)
    assert not samples.any()
    raw = external.compile_sampler(seed=17).sample(256)
    assert np.array_equal(raw[:, 0], raw[:, 1])
    assert np.array_equal(raw[:, 1], raw[:, 2])
    if basis == "X":
        # The first random syndrome is preserved, not silently replaced by 0.
        assert set(raw[:, 0]) == {False, True}


def test_reset_free_code_check_stays_a_detector_not_a_logical_history_output():
    circuit = _build("Z", "slabs", True)
    assert circuit.num_observables == 1
    assert all(inst.tag != "logical-history" for inst in circuit.flattened())


@pytest.mark.parametrize("policy", ["legacy", "slabs"])
def test_reset_free_logical_measurement_keeps_history_after_ancilla_discard(policy):
    system = QECSystem()
    system.add_patch(_PairCode(), name="p")
    tracker = SyndromeTracker(3, 1, logical_tracking=policy)
    builder = CircuitBuilder(tracker, system)
    builder.initialize({0: "Z", 1: "Z"}, 3)
    builder.apply_syndrome_extraction(stim.Circuit("RX 2\nCZ 2 0 2 1\nMX 2"))
    assert tracker.logicals.count == 1
    builder.initialize({2: "X"}, 3)
    # Probe the single logical Z, rather than the preceding ZZ code check.
    builder.apply_syndrome_extraction(stim.Circuit("CZ 2 0\nMX 2"))
    # The modern policy dresses the measured logical direction; legacy keeps
    # its existing explicit representative. Both must retain the real record.
    assert tracker.logicals.count == (1 if policy == "legacy" else 0)
    assert tracker.absorbed_ops.count == (0 if policy == "legacy" else 1)
    assert len(tracker.logical_history) == 1
    assert tracker.logical_history[0].records == (1,)
    assert not tracker.stabilizers.matrix[:, [2, 5]].any()
    builder.apply_data_readout({0: "Z", 1: "Z"})
    circuit = builder.to_stim_circuit()
    assert any(inst.tag == "logical-history" for inst in circuit.flattened())
    circuit.detector_error_model()
    assert not circuit.compile_detector_sampler(seed=41).sample(128, append_observables=True).any()


@pytest.mark.parametrize("basis", ["X", "Z"])
def test_external_ancilla_refreshes_code_check_to_latest_record_after_frame_fix(basis):
    system = QECSystem()
    system.add_patch(_PairCode(), name="p")
    tracker = SyndromeTracker(3, 1)
    builder = CircuitBuilder(tracker, system)
    builder.initialize({0: basis, 1: basis}, 3)
    builder.apply_syndrome_extraction(stim.Circuit("RX 2\nCZ 2 0 2 1\nMX 2"))
    # Choosing the +1 code sector makes its old record expression empty.
    # A fresh auxiliary source must still take priority in the next refresh.
    builder.apply_pauli_feedback({1: "X"}, [0])
    assert tracker.stabilizers.records == [[]]
    for round_index in range(1, 4):
        builder.initialize({2: "X"}, 3)
        builder.apply_syndrome_extraction(stim.Circuit("CZ 2 0 2 1\nMX 2"))
        assert tracker.stabilizers.records == [[round_index]]
        detectors = [inst for inst in builder.circuit.flattened() if inst.name == "DETECTOR"]
        last_records = {t.value for t in detectors[-1].targets_copy()}
        assert last_records == ({-1} if round_index == 1 else {-2, -1})
    builder.apply_data_readout({0: basis, 1: basis})
    circuit = builder.to_stim_circuit()
    circuit.detector_error_model()
    assert not circuit.compile_detector_sampler(seed=29).sample(128, append_observables=True).any()


class _BellPairCode(_PairCode):
    def build(self):
        super().build()
        self.add_qubit(3, 0, role="syndrome_z")


def test_external_bell_pair_refreshes_joint_check_to_latest_two_records():
    system = QECSystem()
    system.add_patch(_BellPairCode(), name="p")
    tracker = SyndromeTracker(4, 1)
    builder = CircuitBuilder(tracker, system)
    builder.initialize({0: "Z", 1: "Z"}, 4)
    for round_index in range(4):
        builder.initialize({2: "X", 3: "Z"}, 4)
        builder.apply_unitary_block(stim.Circuit("CX 2 3"))
        builder.apply_syndrome_extraction(stim.Circuit("CZ 2 0 3 1\nMX 2 3"),
                                          syndrome_qubits={2, 3})
        assert tracker.stabilizers.records == [[2 * round_index, 2 * round_index + 1]]
        if round_index >= 2:
            detectors = [inst for inst in builder.circuit.flattened() if inst.name == "DETECTOR"]
            assert {t.value for t in detectors[-1].targets_copy()} == {-4, -3, -2, -1}
        if round_index == 0:
            builder.apply_pauli_feedback({1: "X"}, [0, 1])
            assert tracker.stabilizers.records == [[]]
    builder.apply_data_readout({0: "Z", 1: "Z"})
    circuit = builder.to_stim_circuit()
    circuit.detector_error_model()
    assert not circuit.compile_detector_sampler(seed=91).sample(128, append_observables=True).any()


class _RedundantChecks(QECPatch):
    def _process_params(self):
        pass

    def build(self):
        self.num_logicals = 1
        for q in range(3):
            self.add_qubit(q, 0, role="data")
        for q in range(3, 6):
            self.add_qubit(q, 0, role="syndrome_z")
        for ancilla, pair in zip(range(3, 6), ((0, 1), (1, 2), (0, 2))):
            self.create_stim_stabilizer({(q, 0): "Z" for q in pair}, (ancilla, 0), "Z")
        self.create_stim_logical({(0, 0): "Z"}, "Z")
        self.create_stim_logical({(q, 0): "X" for q in range(3)}, "X")


def test_redundant_checks_each_keep_their_own_latest_readout():
    system = QECSystem()
    system.add_patch(_RedundantChecks(), name="p")
    tracker = SyndromeTracker(6, 1)
    builder = CircuitBuilder(tracker, system)
    builder.initialize({q: "Z" for q in range(3)}, 6)
    for round_index in range(3):
        builder.initialize({q: "X" for q in range(3, 6)}, 6)
        builder.apply_syndrome_extraction(stim.Circuit(
            "CZ 3 0 3 1 4 1 4 2 5 0 5 2\nMX 3 4 5"))
        # rank(S)=2, but retain all three check/record representatives.
        assert tracker.stabilizers.count == 3
        assert tracker.stabilizers.records == [[3 * round_index + i] for i in range(3)]
        if round_index:
            detectors = [inst for inst in builder.circuit.flattened() if inst.name == "DETECTOR"]
            for i, detector in enumerate(detectors[-3:]):
                records = {t.value for t in detector.targets_copy()}
                assert i - 3 in records
                # The relation solver may use the other two redundant code
                # checks. They must still be from the immediately prior round.
                assert records - {i - 3} <= {-6, -5, -4}
    builder.apply_data_readout({q: "Z" for q in range(3)})
    circuit = builder.to_stim_circuit()
    circuit.detector_error_model()
    assert not circuit.compile_detector_sampler(seed=51).sample(128, append_observables=True).any()
