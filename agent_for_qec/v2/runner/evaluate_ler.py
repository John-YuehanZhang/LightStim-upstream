#!/usr/bin/env python3
"""Final evaluation of accepted facts: logical error rate under the gate's standard
noise, sampled with a fixed decoder. Run after a project has finished; results are
stored in the fact's `evaluation` field and shown in the ledger. This is reporting
only; acceptance never depends on it.

  evaluate_ler.py --project P [--facts ID ...] [--p 1e-3] [--shots 200000] [--max-errors 300]
                  [--decoder bposd|mwpf|pymatching] [--force]

Decoder: the same decoder is used for every fact of a project so that numbers
are comparable. Default BP-OSD (hyperedge-aware; transversal gates and surgery
produce hyperedges that PyMatching can only handle by lossy decomposition).
`mwpf` is the hypergraph matching alternative; `pymatching` decomposes errors
and is only meaningful for graphlike circuits. The decoder is recorded with
the result.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import stim

V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "lib"))
from store import Store  # noqa: E402
from gate import build_in_sandbox  # noqa: E402
from circuitops import standard_noise  # noqa: E402


def sample(circuit: stim.Circuit, decoder: str, shots: int, max_errors: int, batch: int = 20000) -> dict:
    dem = circuit.detector_error_model(decompose_errors=(decoder == "pymatching"), flatten_loops=True)
    if decoder == "pymatching":
        import pymatching
        dec = pymatching.Matching.from_detector_error_model(dem)
        decode = lambda d: dec.decode_batch(d)
    elif decoder == "bposd":
        from stimbposd import BPOSD
        dec = BPOSD(dem, max_bp_iters=30, osd_order=10, osd_method="osd_cs")
        decode = lambda d: dec.decode_batch(d)
    elif decoder == "mwpf":
        from mwpf import SinterMWPFDecoder
        comp = SinterMWPFDecoder(cluster_node_limit=50).compile_decoder_for_dem(dem=dem)
        nobs = dem.num_observables

        def decode(d):
            packed = comp.decode_shots_bit_packed(bit_packed_detection_event_data=np.packbits(d, axis=1, bitorder="little"))
            return np.unpackbits(packed, axis=1, bitorder="little")[:, :nobs]
    else:
        raise ValueError(decoder)
    sampler = circuit.compile_detector_sampler()
    n = errs = 0
    t0 = time.time()
    while n < shots and errs < max_errors:
        det, obs = sampler.sample(batch, separate_observables=True)
        pred = decode(det)
        pred = np.asarray(pred, dtype=bool).reshape(obs.shape)
        errs += int(np.any(pred != obs, axis=1).sum())
        n += batch
    ler = errs / n
    return {"ler": ler, "ler_err": (ler * (1 - ler) / n) ** 0.5, "shots": n, "errors": errs, "decoder": decoder,
            "seconds": round(time.time() - t0, 1)}


def evaluate_fact(st: Store, fact, p: float, shots: int, max_errors: int, decoder: str) -> dict:
    bundle = V2 / "results" / st.project / "facts" / fact["id"][:12] / "bundle"
    out = {"p": p, "circuits": {}, "evaluated": time.time()}
    with tempfile.TemporaryDirectory() as td:
        proc = build_in_sandbox(bundle, Path(td) / "w")
        if proc.returncode != 0:
            out["error"] = "rebuild failed: " + proc.stderr[-500:]
            return out
        for f in sorted((Path(td) / "w" / "out" / "circuits").glob("*.stim")):
            noisy = standard_noise(stim.Circuit(f.read_text()), p)
            try:
                out["circuits"][f.stem] = sample(noisy, decoder, shots, max_errors)
            except Exception as ex:
                out["circuits"][f.stem] = {"error": f"{type(ex).__name__}: {str(ex)[:300]}", "decoder": decoder}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--facts", nargs="*")
    ap.add_argument("--p", type=float)
    ap.add_argument("--shots", type=int, default=200000)
    ap.add_argument("--max-errors", type=int, default=300)
    ap.add_argument("--decoder", default="bposd", choices=["bposd", "mwpf", "pymatching"])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    st = Store(a.project)
    p = a.p if a.p is not None else float(st.get("noise_p", 1e-3))
    facts = [st.fact(x) for x in a.facts] if a.facts else st.facts()
    for f in facts:
        if f is None:
            continue
        if f["evaluation"] and not a.force:
            print(f"{f['id'][:12]} already evaluated; use --force")
            continue
        r = evaluate_fact(st, f, p, a.shots, a.max_errors, a.decoder)
        st.set_evaluation(f["id"], r)
        print(f"{f['id'][:12]} " + "; ".join(f"{k}: {v.get('ler', v.get('error'))} ({v.get('decoder')})"
                                              for k, v in r["circuits"].items()))


if __name__ == "__main__":
    main()
