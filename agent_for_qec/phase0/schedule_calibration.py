"""Calibration: circuit-level distance of rotated-surface-code memory circuits
for each SE block / schedule / basis. Exact via MILP. Output: CSV lines on stdout."""
import sys, time
sys.path.insert(0, 'agent_for_qec/tools')
from verify_stack import circuit_distance
from lightstim.qec_code.surface_code.rotated.code_patch import RotatedSurfaceCode
from lightstim.qec_code.surface_code.rotated.SE_block import RotatedSurfaceCodeExtractionBlock
from lightstim.protocols.memory import MemoryExperiment
from lightstim.noise.config import NoiseConfig
p = 1e-3
nc = NoiseConfig(p_1q=p, p_2q=p, p_meas=p, p_reset=p, p_idle=p)

def run(d, basis, sched, timeout):
    kw = dict(qec_patch=RotatedSurfaceCode(distance=d), rounds=d, basis=basis, noise_params=nc, noise_model='circuit_level')
    if sched != 'generic_coloration':
        kw.update(extraction_block_class=RotatedSurfaceCodeExtractionBlock, se_block_kwargs={'scheduling': sched})
    c = MemoryExperiment(**kw).build()
    r = circuit_distance(c, timeout_s=timeout)
    print(f"d={d},basis={basis},block={sched},search={r.search_weight},exact={r.exact_weight},status={r.status},"
          f"mech={r.n_mechanisms},dets={r.n_detectors},maxdeg={r.max_hyperedge_degree},sec={r.seconds:.0f}", flush=True)

print("config,search,exact,status,mech,dets,maxdeg,sec", flush=True)
for d, timeout in ((3, 300), (5, 600)):
    for sched in ('perpendicular', 'swapped', 'parallel', 'generic_coloration'):
        for basis in ('Z', 'X'):
            run(d, basis, sched, timeout)
for basis in ('Z', 'X'):
    run(7, basis, 'perpendicular', 1500)
    run(7, basis, 'generic_coloration', 1500)
