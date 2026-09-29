"""Circuit operations the gate performs itself, so that the specification under
test is not supplied by the agent.

  standard_noise(circuit, p)      strip all noise and re-inject uniform circuit-level
                                  depolarising noise, including idling
  logical_segment(circuit, data)  the operation without the preparation of the
                                  code-block data qubits and without their final
                                  readout; logical flows are checked on it

Noise model (documented for the paper). For rate p:
  * after every single-qubit unitary gate on q: DEPOLARIZE1(p) on q
  * after every two-qubit unitary gate on (a, b): DEPOLARIZE2(p) on (a, b)
  * after every reset R/RX/RY on q: a flip that undoes the preparation with
    probability p (X_ERROR after R, Z_ERROR after RX, X_ERROR after RY)
  * before every single-qubit measurement M/MX/MY on q: a flip that flips the
    outcome with probability p; MR* measure-and-reset is treated as both
  * multi-qubit Pauli product measurements MPP: the outcome is flipped with
    probability p (modelled by DEPOLARIZE1 on the involved qubits before it, and a
    result-flip parameter on the instruction)
  * idling: in every TICK-delimited moment, every qubit that exists in the
    circuit and is not acted on in that moment receives DEPOLARIZE1(p)
Instructions tagged 'noiseless' are NOT exempted: every operation is noisy.
"""
from __future__ import annotations

from typing import Iterable, List, Set

import stim

NOISE = {"DEPOLARIZE1", "DEPOLARIZE2", "X_ERROR", "Y_ERROR", "Z_ERROR", "PAULI_CHANNEL_1", "PAULI_CHANNEL_2",
         "E", "ELSE_CORRELATED_ERROR", "CORRELATED_ERROR", "HERALDED_ERASE", "HERALDED_PAULI_CHANNEL_1",
         "I_ERROR", "II_ERROR"}
ANNOT = {"DETECTOR", "OBSERVABLE_INCLUDE", "QUBIT_COORDS", "SHIFT_COORDS", "TICK", "MPAD"}
RESET_FLIP = {"R": "X_ERROR", "RZ": "X_ERROR", "RX": "Z_ERROR", "RY": "X_ERROR"}
MEAS_FLIP = {"M": "X_ERROR", "MZ": "X_ERROR", "MX": "Z_ERROR", "MY": "X_ERROR"}
MR = {"MR": ("X_ERROR", "X_ERROR"), "MRZ": ("X_ERROR", "X_ERROR"), "MRX": ("Z_ERROR", "Z_ERROR"),
      "MRY": ("X_ERROR", "X_ERROR")}


def _targets(inst) -> List[int]:
    return [t.value for t in inst.targets_copy() if t.is_qubit_target or t.is_x_target or t.is_y_target
            or t.is_z_target]


def all_qubits(c: stim.Circuit) -> Set[int]:
    qs = set()
    for inst in c.flattened():
        if inst.name in ANNOT:
            continue
        qs.update(_targets(inst))
    return qs


def standard_noise(circuit: stim.Circuit, p: float) -> stim.Circuit:
    clean = circuit.without_noise().flattened()
    qubits = sorted(all_qubits(clean) | set(clean.get_final_qubit_coordinates()))
    out = stim.Circuit()
    busy: Set[int] = set()
    moment_has_ops = False

    def close_moment():
        nonlocal busy, moment_has_ops
        idle = [q for q in qubits if q not in busy]
        if moment_has_ops and idle:
            out.append("DEPOLARIZE1", idle, p)
        busy, moment_has_ops = set(), False

    for inst in clean:
        name = inst.name
        if name == "TICK":
            close_moment()
            out.append(inst)
            continue
        if name in ANNOT or name in NOISE:
            out.append(inst)
            continue
        gd = stim.gate_data(name)
        ts = _targets(inst)
        if name in MR:
            before, after = MR[name]
            out.append(before, ts, p)
            out.append(inst)
            out.append(after, ts, p)
        elif name in RESET_FLIP:
            out.append(inst)
            out.append(RESET_FLIP[name], ts, p)
        elif name in MEAS_FLIP:
            out.append(MEAS_FLIP[name], ts, p)
            out.append(inst)
        elif name == "MPP" or gd.produces_measurements:
            out.append("DEPOLARIZE1", sorted(set(ts)), p)
            out.append(stim.CircuitInstruction(name, inst.targets_copy(), [p]))
        elif gd.is_two_qubit_gate:
            out.append(inst)
            out.append("DEPOLARIZE2", ts, p)
        elif gd.is_unitary:
            out.append(inst)
            out.append("DEPOLARIZE1", ts, p)
        else:
            out.append(inst)
        busy.update(ts)
        moment_has_ops = True
    close_moment()
    return out


def logical_segment(circuit: stim.Circuit, data_qubits: Iterable[int]) -> stim.Circuit:
    """Remove, for each code-block data qubit, a reset that precedes every other
    operation on it and a single-qubit measurement that follows every other
    operation on it. Annotations (detectors, observables) and noise are dropped.
    Everything else is kept unchanged, so the segment performs the same operation."""
    data = set(data_qubits)
    insts = [i for i in circuit.without_noise().flattened() if i.name not in ("DETECTOR", "OBSERVABLE_INCLUDE")]
    first_op, last_op = {}, {}
    for idx, inst in enumerate(insts):
        if inst.name in ANNOT:
            continue
        for q in _targets(inst):
            first_op.setdefault(q, idx)
            last_op[q] = idx
    out = stim.Circuit()
    for idx, inst in enumerate(insts):
        name = inst.name
        if name in RESET_FLIP or name in MEAS_FLIP:
            ts = inst.targets_copy()
            keep = []
            for t in ts:
                q = t.value
                is_prep = name in RESET_FLIP and q in data and first_op.get(q) == idx
                is_read = name in MEAS_FLIP and q in data and last_op.get(q) == idx
                if not (is_prep or is_read):
                    keep.append(t)
            if keep:
                out.append(stim.CircuitInstruction(name, keep, inst.gate_args_copy()))
            continue
        out.append(inst)
    return out
