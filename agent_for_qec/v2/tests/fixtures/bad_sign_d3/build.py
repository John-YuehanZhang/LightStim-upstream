"""Calibration fixture: transversal CNOT between two d=3 rotated surface codes (full-distance SE)."""
import functools, sys
sys.path.insert(0, "agent_for_qec/phase0")
from cnot_trans_verify import build as build_exp, logical_segment, patch_logicals
sys.path.insert(0, "agent_for_qec/tools")
from verify_stack import logical_flow_string
from lightstim.qec_code.surface_code.rotated.code_patch import RotatedSurfaceCode

D = 3
SIGN = -1

def build():
    exp, c = build_exp(D, 'Z', 'Z', 'Z', 'Z', D)
    data = sorted(exp.system.data_indices)
    seg = logical_segment(c, set(data))
    lc, lt = patch_logicals(exp.system, 'control'), patch_logicals(exp.system, 'target')
    flows = [logical_flow_string(lc['X'], {**lc['X'], **lt['X']}, sign=SIGN),
             logical_flow_string(lt['Z'], {**lc['Z'], **lt['Z']}),
             logical_flow_string(lt['X'], lt['X']),
             logical_flow_string(lc['Z'], lc['Z'])]
    return {"code": {"patch": RotatedSurfaceCode(distance=D)},
            "circuits": {"cnot_ZZ": {"circuit": c, "data_qubits": data, "flow_circuit": seg, "flows": flows}}}
