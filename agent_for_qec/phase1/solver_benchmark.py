"""Phase 1a: benchmark of the exact circuit-level distance routes.

  A. cross-check: fast route (tools/circuit_distance_fast.py) vs the Phase 0
     MILP numbers on every calibration config d=3,5 (4 schedules x 2 bases);
     the relaxation LB must never exceed the known exact value.
  B. rotated memory, perpendicular, d=5,7,9,11 (Z and X): fast route timing.
  C. d=7 Z: full-problem MILP feasibility "weight <= 6?" (route b) with a
     time limit, and the old optimisation MILP result from Phase 0 (1500 s timeout).
  D. rounds scan (route c, evidence only, not a proof): d=7 Z, rounds=1..3 and d.

  PYTHONPATH=. python agent_for_qec/phase1/solver_benchmark.py [A|B|C|D ...] > agent_for_qec/phase1/solver_benchmark.out
"""
import sys, time
sys.path.insert(0, 'agent_for_qec/tools')
from circuit_distance_fast import fast_circuit_distance, milp_feasibility, sensitive_detectors
from verify_stack import _dem_matrices
from lightstim.qec_code.surface_code.rotated.code_patch import RotatedSurfaceCode
from lightstim.qec_code.surface_code.rotated.SE_block import RotatedSurfaceCodeExtractionBlock
from lightstim.protocols.memory import MemoryExperiment
from lightstim.noise.config import NoiseConfig

p = 1e-3
nc = NoiseConfig(p_1q=p, p_2q=p, p_meas=p, p_reset=p, p_idle=p)

# Phase 0 exact values (phase0/schedule_calibration.out)
KNOWN = {(3, 'Z', 'perpendicular'): 3, (3, 'X', 'perpendicular'): 3, (3, 'Z', 'swapped'): 2,
         (3, 'X', 'swapped'): 2, (3, 'Z', 'parallel'): 3, (3, 'X', 'parallel'): 2,
         (3, 'Z', 'generic_coloration'): 3, (3, 'X', 'generic_coloration'): 3,
         (5, 'Z', 'perpendicular'): 5, (5, 'X', 'perpendicular'): 5, (5, 'Z', 'swapped'): 3,
         (5, 'X', 'swapped'): 3, (5, 'Z', 'parallel'): 5, (5, 'X', 'parallel'): 3,
         (5, 'Z', 'generic_coloration'): 3, (5, 'X', 'generic_coloration'): 3,
         (7, 'Z', 'generic_coloration'): 5, (7, 'X', 'generic_coloration'): 5}


def mem(d, basis, sched, rounds=None):
    kw = dict(qec_patch=RotatedSurfaceCode(distance=d), rounds=rounds or d, basis=basis,
              noise_params=nc, noise_model='circuit_level')
    if sched != 'generic_coloration':
        kw.update(extraction_block_class=RotatedSurfaceCodeExtractionBlock, se_block_kwargs={'scheduling': sched})
    exp = MemoryExperiment(**kw)
    c = exp.build()
    data = [exp.system.index_map[x] for x in exp.system.data_coords]
    return c, data


def line(tag, r):
    rel = {k[6:]: (v['lb'], v['method'], v['cols'], v['maxdeg'], v['sec']) for k, v in r.detail.items()
           if k.startswith('relax:')}
    print(f"{tag}: lb={r.lb} ub={r.ub} status={r.status} lb_by={r.lb_method} ub_by={r.ub_method} "
          f"mech={r.n_mechanisms} dets={r.n_detectors} sec={r.seconds:.1f} t_ub={r.detail.get('t_ub')} "
          f"feas={r.detail.get('feas')} relax={rel}", flush=True)


def part_A():
    print("## A. cross-check vs Phase 0 MILP", flush=True)
    bad = 0
    for (d, basis, sched), known in KNOWN.items():
        c, data = mem(d, basis, sched)
        r = fast_circuit_distance(c, data_qubits=data, milp_time_s=900)
        ok = (r.lb <= known) and (r.ub is None or r.ub >= known) and (r.status != 'exact' or r.lb == known)
        bad += not ok
        line(f"A d={d} {basis} {sched} known={known} agree={ok}", r)
    print(f"A: disagreements={bad}", flush=True)


def part_B():
    print("## B. perpendicular memory, fast route", flush=True)
    for d in (5, 7, 9, 11, 13, 15):
        for basis in ('Z', 'X'):
            c, data = mem(d, basis, 'perpendicular')
            r = fast_circuit_distance(c, data_qubits=data, milp_time_s=1800)
            line(f"B d={d} {basis} perpendicular", r)


def part_C():
    print("## C. d=7 Z perpendicular: full-problem MILP feasibility (weight <= 6?)", flush=True)
    c, _ = mem(7, 'Z', 'perpendicular')
    dem = c.detector_error_model(decompose_errors=False, flatten_loops=True)
    H, L, _, _ = _dem_matrices(dem)
    st, sol, sec = milp_feasibility(H, L, 6, time_limit_s=1800)
    print(f"C feasibility(<=6) full problem: {st} sec={sec:.0f} (Phase 0 optimisation MILP: timeout at 1500 s)",
          flush=True)


def part_D():
    print("## D. rounds scan (evidence only)", flush=True)
    for rounds in (1, 2, 3, 7):
        c, data = mem(7, 'Z', 'perpendicular', rounds=rounds)
        r = fast_circuit_distance(c, data_qubits=data, milp_time_s=900)
        line(f"D d=7 Z rounds={rounds}", r)


def part_E(ds=(3, 5, 7)):
    """Transversal CNOT (phase0/cnot_trans_verify.py construction), fast route."""
    print("## E. transversal CNOT, perpendicular, rounds d+d, fast route", flush=True)
    sys.path.insert(0, 'agent_for_qec/phase0')
    from cnot_trans_verify import build
    for d in ds:
        for combo in ('ZZZZ', 'XXXX', 'XZZZ', 'XZXX'):
            exp, c = build(d, *combo, d)
            data = list(exp.system.data_indices)
            # extra relaxations: each basis-sensitive set restricted to one patch
            # (control: x <= 2d, target: x >= 2d+2)
            coords = c.get_detector_coordinates()
            extra = []
            for pa in ('X', 'Z'):
                sens = sensitive_detectors(c, data, pa)
                extra.append([k for k in sens if coords[k][0] <= 2 * d])
                extra.append([k for k in sens if coords[k][0] > 2 * d])
            r = fast_circuit_distance(c, data_qubits=data, extra_subsets=extra, milp_time_s=1800)
            line(f"E d={d} init={combo[:2]} meas={combo[2:]}", r)


if __name__ == '__main__':
    parts = sys.argv[1:] or ['A', 'B', 'C', 'D', 'E']
    for p_ in parts:
        {'A': part_A, 'B': part_B, 'C': part_C, 'D': part_D, 'E': part_E,
         'E35': lambda: part_E((3, 5)), 'E7': lambda: part_E((7,))}[p_]()
