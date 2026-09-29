"""Phase 1a step 3 input: memory experiments of the three candidate codes with
LightStim's native SE blocks -- code distance, DEM size, and whether the fast
exact route (tools/circuit_distance_fast.py) closes the circuit distance.

  PYTHONPATH=. python agent_for_qec/phase1/candidate_probe.py [name ...] > agent_for_qec/phase1/candidate_probe.out
"""
import sys
sys.path.insert(0, 'agent_for_qec/tools')
from verify_stack import code_distance_from_patch, noiseless_sanity
from circuit_distance_fast import fast_circuit_distance
from lightstim.ir.qec_system import QECSystem
from lightstim.protocols.memory import MemoryExperiment
from lightstim.noise.config import NoiseConfig

p = 1e-3
nc = NoiseConfig(p_1q=p, p_2q=p, p_meas=p, p_reset=p, p_idle=p)


def cands():
    from lightstim.qec_code.BB_code import BBCode, BBCodeExtractionBlock
    from lightstim.qec_code.four_d_geo_code import FourDGeoCode, FourDGeoCodeExtractionBlock
    from lightstim.qec_code.four_d_geo_code.configs import FOUR_D_CONFIGS
    from lightstim.qec_code.HGP.instances import hgp_13_1_3, hgp_18_2_3
    from lightstim.qec_code.HGP.SE_block import HGPProductColorationExtractionBlock
    return {
        'A:4d_det3': (lambda: FourDGeoCode(L=FOUR_D_CONFIGS['det3']['L'], d=3), FourDGeoCodeExtractionBlock, 3),
        'A:4d_det5': (lambda: FourDGeoCode(L=FOUR_D_CONFIGS['det5']['L'], d=4), FourDGeoCodeExtractionBlock, 4),
        'B:hgp_13_1_3': (hgp_13_1_3, HGPProductColorationExtractionBlock, 3),
        'B:hgp_18_2_3': (hgp_18_2_3, HGPProductColorationExtractionBlock, 3),
        'C:bb_72_12_6': (lambda: BBCode(l=6, m=6, A=[[3, 0], [0, 1], [0, 2]], B=[[0, 3], [1, 0], [2, 0]]),
                         BBCodeExtractionBlock, 6),
    }


def main(names):
    table = cands()
    for name in names or list(table):
        mk, block, rounds = table[name]
        cd = code_distance_from_patch(mk(), timeout_s=1200)
        print(f"### {name}: code n={cd.n} k={cd.k} d={cd.d} ({cd.status}, {cd.detail['seconds']:.1f}s)", flush=True)
        for basis in ('Z', 'X'):
            system = QECSystem()
            system.add_patch(mk(), name='p')
            exp = MemoryExperiment(qec_system=system, extraction_block_class=block, rounds=rounds,
                                   noise_params=nc, noise_model='circuit_level', basis=basis)
            c = exp.build()
            ok, _, _ = noiseless_sanity(c)
            data = [system.index_map[x] for x in system.data_coords]
            r = fast_circuit_distance(c, data_qubits=data, milp_time_s=1800)
            rel = {k[6:]: (v['lb'], v['method'], v['cols'], v['maxdeg'], v['sec'], v['lift_ok'])
                   for k, v in r.detail.items() if k.startswith('relax:')}
            print(f"  {name} memory basis={basis} rounds={rounds}: obs={c.num_observables} sanity={ok} "
                  f"lb={r.lb} ub={r.ub} status={r.status} lb_by={r.lb_method} ub_by={r.ub_method} "
                  f"mech={r.n_mechanisms} dets={r.n_detectors} sec={r.seconds:.0f} feas={r.detail.get('feas')} "
                  f"relax={rel}", flush=True)


if __name__ == '__main__':
    main(sys.argv[1:])
