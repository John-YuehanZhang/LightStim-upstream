"""Explain every sub-distance rotated-surface-code memory configuration of
phase0/schedule_calibration.out by the circuit locations of a minimum-weight
undetectable logical error (MILP witness -> stim explain).

For each witness mechanism we print: tick, gate, qubit coordinates, the Pauli
that is applied, and -- for two-qubit faults on an ancilla/data CNOT -- the
data-qubit Pauli it spreads to after the remaining CNOTs of that ancilla
(the "hook"). Also prints, per schedule, the CNOT order of X and Z checks and
the resulting hook orientation.

  PYTHONPATH=. python agent_for_qec/phase0/hook_witness.py > agent_for_qec/phase0/hook_witness.out
"""
import sys
sys.path.insert(0, 'agent_for_qec/tools')
import stim
from verify_stack import _dem_matrices, min_weight_vector_milp
from lightstim.qec_code.surface_code.rotated.code_patch import RotatedSurfaceCode
from lightstim.qec_code.surface_code.rotated.SE_block import RotatedSurfaceCodeExtractionBlock
from lightstim.protocols.memory import MemoryExperiment
from lightstim.noise.config import NoiseConfig

p = 1e-3
nc = NoiseConfig(p_1q=p, p_2q=p, p_meas=p, p_reset=p, p_idle=p)
NAMES = {(+1, +1): 'NE', (-1, +1): 'NW', (+1, -1): 'SE', (-1, -1): 'SW'}


def schedule_summary():
    print("## CNOT order per check (deltas are (dx,dy) from ancilla; names follow SE_block.py comments)")
    for s, ticks in RotatedSurfaceCodeExtractionBlock.SCHEDULES.items():
        xo = [NAMES[t[0]] for t in ticks]
        zo = [NAMES[t[1]] for t in ticks]
        # hook = the last two data qubits of a weight-4 check (fault on ancilla after 2 CNOTs)
        def orient(order):
            a, b = [k for k, v in NAMES.items() if v in order[2:]]
            return 'horizontal (same y)' if a[1] == b[1] else 'vertical (same x)'
        print(f"{s:14s} X-check order {xo} -> X-hook on {xo[2:]} = {orient(xo)} ; "
              f"Z-check order {zo} -> Z-hook on {zo[2:]} = {orient(zo)}")
    print("Logical ops (code_patch.py): Z_L = Z on row y=1 (horizontal), X_L = X on column x=1 (vertical).")
    print("Z-basis memory is broken by X_L-like errors (vertical X chains) -> dangerous hook: vertical X pair (X checks).")
    print("X-basis memory is broken by Z_L-like errors (horizontal Z chains) -> dangerous hook: horizontal Z pair (Z checks).")
    print()


def build(d, basis, sched):
    kw = dict(qec_patch=RotatedSurfaceCode(distance=d), rounds=d, basis=basis, noise_params=nc,
              noise_model='circuit_level')
    if sched != 'generic_coloration':
        kw.update(extraction_block_class=RotatedSurfaceCodeExtractionBlock, se_block_kwargs={'scheduling': sched})
    return MemoryExperiment(**kw).build()


def explain(d, basis, sched, timeout=600):
    c = build(d, basis, sched)
    coords = c.get_final_qubit_coordinates()
    dem = c.detector_error_model(decompose_errors=False, flatten_loops=True)
    H, L, keys, _ = _dem_matrices(dem)
    r = min_weight_vector_milp(H, L, time_limit_s=timeout)
    print(f"=== d={d} basis={basis} block={sched}: MILP status={r.status} weight={r.weight}")
    if r.witness is None:
        return
    flt = stim.DetectorErrorModel()
    for j in r.witness:
        dets, obs = keys[j]
        tg = [stim.target_relative_detector_id(x) for x in sorted(dets)] + \
             [stim.target_logical_observable_id(x) for x in sorted(obs)]
        flt.append("error", 0.1, tg)
    ex = c.explain_detector_error_model_errors(dem_filter=flt, reduce_to_one_representative_error=True)
    data_pauli = {}
    for e in ex:
        loc = e.circuit_error_locations[0]
        ps = loc.flipped_pauli_product
        desc = " ".join(f"{t.gate_target.pauli_type}{tuple(int(v) for v in coords[t.gate_target.value][:2])}"
                        for t in ps)
        gate = loc.instruction_targets
        gq = [tuple(int(v) for v in coords[t.gate_target.value][:2]) for t in gate.targets_in_range] \
            if gate is not None else []
        mflip = loc.flipped_measurement.record_index if loc.flipped_measurement is not None else None
        print(f"  tick={loc.tick_offset:4d} {gate.gate if gate else '-':14s} on {gq}  pauli: {desc or '-'}"
              f"{'  (flips meas rec ' + str(mflip) + ')' if mflip is not None else ''}"
              f"  dets={sorted(t.dem_target.val for t in e.dem_error_terms if t.dem_target.is_relative_detector_id())}")
    print()


def generic_orientation(d):
    """Per weight-4 check of the generic edge-colouring block: orientation of the
    hook pair (the two data qubits touched in the last two CNOT layers)."""
    from lightstim.ir.qec_system import QECSystem
    from lightstim.qec_code.generic_css.SE_block import GenericCSSColorationExtractionBlock
    sysm = QECSystem()
    sysm.add_patch(RotatedSurfaceCode(distance=d), name='p')
    blk = GenericCSSColorationExtractionBlock(sysm)
    qc = {q: tuple(int(v) for v in xy) for q, xy in sysm.qubit_coords.items()}
    for basis, layers in (('X', blk.x_layers), ('Z', blk.z_layers)):
        order = {}
        for li, layer in enumerate(layers):
            for syn, dat in layer:
                order.setdefault(syn, []).append(dat)
        cnt = {'vertical': 0, 'horizontal': 0, 'diagonal': 0}
        bad = []
        for syn, ds in order.items():
            if len(ds) != 4:
                continue
            a, b = (qc.get(x) for x in ds[2:])
            kind = 'vertical' if a[0] == b[0] else 'horizontal' if a[1] == b[1] else 'diagonal'
            cnt[kind] += 1
            danger = 'vertical' if basis == 'X' else 'horizontal'
            if kind == danger:
                bad.append(qc.get(syn))
        print(f"generic d={d} {basis}-checks (weight 4): hook orientation counts {cnt}; "
              f"checks with hook parallel to the logical they threaten: {sorted(bad)}")


if __name__ == '__main__':
    for d in (5, 7):
        generic_orientation(d)
    print()
    schedule_summary()
    cfgs = [(3, 'Z', 'swapped'), (3, 'X', 'swapped'), (3, 'X', 'parallel'),
            (5, 'Z', 'swapped'), (5, 'X', 'swapped'), (5, 'X', 'parallel'),
            (5, 'Z', 'generic_coloration'), (5, 'X', 'generic_coloration')]
    for cfg in cfgs:
        explain(*cfg)
