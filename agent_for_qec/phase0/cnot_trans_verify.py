"""Phase 0 calibration: transversal CNOT between two rotated-surface-code
patches (lightstim/protocols/cnot_trans.py) under the full-distance
'perpendicular' SE schedule.

Checks (all via agent_for_qec/tools/verify_stack.py):
  * code distance of the patch (exact, z3)
  * noiseless sanity of the full experiment for several init/measure bases
  * signed logical flows of the "logical segment" = the experiment circuit with
    the initial data reset and final data readout removed (SE rounds + CX layer
    + SE rounds), using LightStim's own patch logical operators
  * exact circuit-level distance (HiGHS MILP) of every experiment
  * platform report

  PYTHONPATH=. python agent_for_qec/phase0/cnot_trans_verify.py [d ...] > agent_for_qec/phase0/cnot_trans_verify.out
"""
import sys
import functools
sys.path.insert(0, 'agent_for_qec/tools')
import stim
from verify_stack import (circuit_distance, check_flows, noiseless_sanity, platform_report,
                          code_distance_from_patch, logical_flow_string)
from lightstim.qec_code.surface_code.rotated.code_patch import RotatedSurfaceCode
from lightstim.qec_code.surface_code.rotated.SE_block import RotatedSurfaceCodeExtractionBlock
from lightstim.protocols.cnot_trans import CNOTTransExperiment
from lightstim.noise.config import NoiseConfig

p = 1e-3
nc = NoiseConfig(p_1q=p, p_2q=p, p_meas=p, p_reset=p, p_idle=p)
SE = functools.partial(RotatedSurfaceCodeExtractionBlock, scheduling='perpendicular')


def build(d, ic, it, mc, mt, rounds):
    exp = CNOTTransExperiment(code_patch_class=RotatedSurfaceCode, extraction_block_class=SE,
                              code_params_control={'distance': d}, offset_target=(2 * d + 2, 0),
                              initial_basis_control=ic, initial_basis_target=it,
                              measure_basis_control=mc, measure_basis_target=mt,
                              rounds_before=rounds, rounds_after=rounds,
                              noise_params=nc, noise_model='circuit_level')
    c = exp.build()
    return exp, c


def logical_segment(circuit, data):
    """Drop the initial data reset(s), the final data readout and all annotations
    that reference measurement records."""
    flat = circuit.without_noise().flattened()
    insts = list(flat)
    out = stim.Circuit()
    first_2q = next(i for i, x in enumerate(insts) if x.name in ('CX', 'CNOT'))
    last_2q = max(i for i, x in enumerate(insts) if x.name in ('CX', 'CNOT'))
    for i, inst in enumerate(insts):
        if inst.name in ('DETECTOR', 'OBSERVABLE_INCLUDE'):
            continue
        if (i < first_2q and inst.name in ('R', 'RX', 'RY', 'RZ')) or \
           (i > last_2q and inst.name in ('M', 'MX', 'MY', 'MZ')):
            # data resets/readouts may share an instruction with ancilla ones
            keep = [t for t in inst.targets_copy() if t.value not in data]
            if keep:
                out.append(stim.CircuitInstruction(inst.name, keep, inst.gate_args_copy()))
            continue
        out.append(inst)
    return out


def patch_logicals(system, name):
    patch = system.patches[name][0]
    l2g = system.local_to_global_map[name]
    res = {}
    for lg in patch.logical_ops:
        pauli = {l2g.get(q, q): p for q, p in lg['pauli'].items()}
        kind = set(pauli.values())
        assert len(kind) == 1
        res[kind.pop()] = pauli
    return res


def main(ds, combos=None):
    for d in ds:
        cd = code_distance_from_patch(RotatedSurfaceCode(distance=d))
        print(f"### d={d}: patch code distance n={cd.n} k={cd.k} d={cd.d} ({cd.status})", flush=True)
        exp, c = build(d, 'Z', 'Z', 'Z', 'Z', d)
        data = set(exp.system.data_indices)
        seg = logical_segment(c, data)
        lc = patch_logicals(exp.system, 'control')
        lt = patch_logicals(exp.system, 'target')
        assert set(lc[ 'X']) | set(lc['Z']) <= data and set(lt['X']) | set(lt['Z']) <= data
        flows = {
            'X_c -> X_c X_t': logical_flow_string(lc['X'], {**lc['X'], **lt['X']}),
            'Z_t -> Z_c Z_t': logical_flow_string(lt['Z'], {**lc['Z'], **lt['Z']}),
            'X_t -> X_t': logical_flow_string(lt['X'], lt['X']),
            'Z_c -> Z_c': logical_flow_string(lc['Z'], lc['Z']),
        }
        negative = {  # must FAIL: wrong action / wrong sign
            'NEG X_c -> X_c': logical_flow_string(lc['X'], lc['X']),
            'NEG Z_t -> Z_t': logical_flow_string(lt['Z'], lt['Z']),
            'NEG X_c -> -X_c X_t (sign)': logical_flow_string(lc['X'], {**lc['X'], **lt['X']}, sign=-1),
        }
        fr = check_flows(seg, list(flows.values()) + list(negative.values()))
        for k, f in {**flows, **negative}.items():
            print(f"  flow {k:30s} signed={fr.results[f]} unsigned={fr.unsigned_results[f]}", flush=True)
        ok_pos = all(fr.results[f] for f in flows.values())
        ok_neg = not any(fr.results[f] for f in negative.values())
        print(f"  flows: positive all hold={ok_pos}; negative controls all rejected={ok_neg}", flush=True)
        pr = platform_report(c)
        print(f"  platform: qubits={pr.n_qubits} 2q={pr.n_2q_gates} nonlocal={pr.n_nonlocal_2q} "
              f"max_range={pr.max_range:.1f} max_partners={pr.max_degree} 2q_layers={pr.two_qubit_layers} "
              f"verdict={pr.verdict}", flush=True)
        for ic, it, mc, mt in (combos or ('ZZZZ', 'XXXX', 'XZZZ', 'XZXX')):
            exp, c = build(d, ic, it, mc, mt, d)
            ok, nd, no = noiseless_sanity(c)
            r = circuit_distance(c, timeout_s=3000)
            print(f"  init={ic}{it} meas={mc}{mt} rounds={d}+{d}: obs={c.num_observables} sanity={ok}(dets={nd},obs={no}) "
                  f"search={r.search_weight} exact={r.exact_weight} lb={r.proven_lower_bound} status={r.status} "
                  f"mech={r.n_mechanisms} dets={r.n_detectors} maxdeg={r.max_hyperedge_degree} sec={r.seconds:.0f}",
                  flush=True)


if __name__ == '__main__':
    # usage: cnot_trans_verify.py [d ...] [--combos ZZZZ,XXXX]   (combo = init_c init_t meas_c meas_t)
    args = sys.argv[1:]
    combos = None
    if '--combos' in args:
        i = args.index('--combos')
        combos = args[i + 1].split(',')
        args = args[:i] + args[i + 2:]
    main([int(x) for x in args] or [3, 5], combos)
