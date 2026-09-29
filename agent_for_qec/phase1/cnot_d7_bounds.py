"""Phase 1a: transversal CNOT at d=7 with the fast route (per-patch graphlike
relaxations + projected MILP capped at 500 s per call; stim search off). Reports the proven
interval [lb, ub]; exact only when they meet.

  PYTHONPATH=. timeout 3600 python agent_for_qec/phase1/cnot_d7_bounds.py ZZZZ > agent_for_qec/phase1/cnot_d7_ZZZZ.out
"""
import sys
sys.path.insert(0, 'agent_for_qec/tools')
sys.path.insert(0, 'agent_for_qec/phase0')
from cnot_trans_verify import build
from circuit_distance_fast import fast_circuit_distance, sensitive_detectors

d = 7
combo = sys.argv[1]
exp, c = build(d, *combo, d)
data = list(exp.system.data_indices)
coords = c.get_detector_coordinates()
extra = []
for pa in ('X', 'Z'):
    sens = sensitive_detectors(c, data, pa)
    extra.append([k for k in sens if coords[k][0] <= 2 * d])
    extra.append([k for k in sens if coords[k][0] > 2 * d])
r = fast_circuit_distance(c, data_qubits=data, extra_subsets=extra, milp_time_s=500, verbose=True,
                          use_stim_search=False)  # stim search does not finish at d=7
rel = {k[6:]: (v['lb'], v['method'], v['cols'], v['maxdeg'], v['sec'], v.get('milp_status'))
       for k, v in r.detail.items() if k.startswith('relax:')}
print(f"E d=7 init={combo[:2]} meas={combo[2:]}: lb={r.lb} ub={r.ub} status={r.status} lb_by={r.lb_method} "
      f"ub_by={r.ub_method} mech={r.n_mechanisms} dets={r.n_detectors} sec={r.seconds:.0f} "
      f"feas={r.detail.get('feas')} row_lb={r.detail.get('row_lb')} relax={rel}", flush=True)
