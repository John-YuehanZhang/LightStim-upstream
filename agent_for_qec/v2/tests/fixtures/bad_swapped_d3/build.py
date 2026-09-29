"""Fixture: memory with the 'swapped' schedule, which loses distance (exact circuit distance 2 at d=3)."""
from lightstim.qec_code.surface_code.rotated.code_patch import RotatedSurfaceCode
from lightstim.qec_code.surface_code.rotated.SE_block import RotatedSurfaceCodeExtractionBlock
from lightstim.protocols.memory import MemoryExperiment
from lightstim.noise.config import NoiseConfig

def build():
    p = 1e-3
    exp = MemoryExperiment(qec_patch=RotatedSurfaceCode(distance=3), rounds=3, basis='Z',
                           noise_params=NoiseConfig(p_1q=p, p_2q=p, p_meas=p, p_reset=p, p_idle=p),
                           noise_model='circuit_level', extraction_block_class=RotatedSurfaceCodeExtractionBlock,
                           se_block_kwargs={'scheduling': 'swapped'})
    c = exp.build()
    return {"code": {"patch": RotatedSurfaceCode(distance=3)},
            "circuits": {"mem_Z": {"circuit": c, "data_qubits": sorted(exp.system.data_indices)}}}
