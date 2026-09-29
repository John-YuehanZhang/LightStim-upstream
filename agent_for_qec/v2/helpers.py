"""Helpers for writing submissions (importable from build.py as
`from agent_for_qec.v2.helpers import ...`; the repository root is on PYTHONPATH).

The gate derives the logical segment and injects noise itself; `logical_segment`
and `standard_noise` here are the exact functions it uses, so you can check
your flows and distances the same way before submitting.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from circuitops import logical_segment, standard_noise  # noqa: E402,F401


def patch_block(system, name: str) -> List[int]:
    """Global indices of a LightStim patch's data qubits, in the patch's local order
    (the order used when the gate reads the code from {'patch': patch})."""
    patch = system.patches[name][0]
    l2g = system.local_to_global_map[name]
    return [l2g[q] for q in sorted(patch.data_indices)]


def patch_logicals(system, name: str) -> Dict[str, Dict[int, str]]:
    """The patch's X and Z logical representatives as {global qubit: Pauli}."""
    patch = system.patches[name][0]
    l2g = system.local_to_global_map[name]
    res = {}
    for lg in patch.logical_ops:
        res[lg["type"]] = {l2g.get(q, q): p for q, p in lg["pauli"].items()}
    return res


def flow(inp: Dict[int, str], out: Dict[int, str], recs: Sequence[int] = (), sign: int = +1) -> str:
    """A stim flow string 'P -> [-]Q [xor rec[..] ...]' from {qubit: Pauli} dicts."""
    def s(d):
        return "*".join(f"{p}{q}" for q, p in sorted(d.items())) if d else "1"
    rhs = ("-" if sign < 0 else "") + s(out)
    if recs:
        rhs += " xor " + " xor ".join(f"rec[{r}]" for r in recs)
    return f"{s(inp)} -> {rhs}"


def check_flows_like_gate(circuit, blocks: Iterable[Iterable[int]], flows: Iterable[str]) -> Dict[str, bool]:
    """Signed flow check on the same logical segment the gate uses."""
    import stim
    data = sorted({q for b in blocks for q in b})
    seg = logical_segment(circuit, data)
    return {f: seg.has_flow(stim.Flow(f)) for f in flows}
