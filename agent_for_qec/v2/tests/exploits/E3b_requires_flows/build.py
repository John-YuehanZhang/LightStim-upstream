"""Z memory, rotated d=3, 3 rounds; SCHED selects the schedule."""
from lightstim.qec_code.surface_code.rotated.code_patch import RotatedSurfaceCode
from lightstim.qec_code.surface_code.rotated.SE_block import RotatedSurfaceCodeExtractionBlock
from lightstim.protocols.memory import MemoryExperiment
from lightstim.noise.config import NoiseConfig
from agent_for_qec.v2.helpers import patch_block, patch_logicals, flow

SCHED = 'perpendicular'

def build():
    p = 1e-3
    exp = MemoryExperiment(qec_patch=RotatedSurfaceCode(distance=3), rounds=3, basis='Z',
                           noise_params=NoiseConfig(p_1q=p, p_2q=p, p_meas=p, p_reset=p, p_idle=p),
                           noise_model='circuit_level', extraction_block_class=RotatedSurfaceCodeExtractionBlock,
                           se_block_kwargs={'scheduling': SCHED})
    c = exp.build()
    name = next(iter(exp.system.patches))
    L = patch_logicals(exp.system, name)
    return {"code": {"patch": RotatedSurfaceCode(distance=3)},
            "circuits": {"mem_Z": {"circuit": c, "blocks": [patch_block(exp.system, name)],
                                   "flows": [flow(L['X'], L['X']), flow(L['Z'], L['Z'])]}}}
