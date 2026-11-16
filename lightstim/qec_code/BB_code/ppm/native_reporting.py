"""Signed stabilizer definitions for the 15 basic gross-code measurements.

The definitions come from ``build_native_measurement``. These reports describe
the operators, not the circuits used to measure them.
"""
from __future__ import annotations

from itertools import combinations, product
import json
from pathlib import Path

import stim

from .native import NativeMeasurement, build_native_measurement


SCHEMA_VERSION = 2
PAPER_URL = "https://arxiv.org/abs/2506.03094v1"
VALIDATION_CHECKS = (
    "pairwise_check_commutation",
    "signed_vertex_product_equals_target",
)


def _site(site):
    return {"kind": site[0], "x": site[1], "y": site[2],
            "label": f"{site[0]}({site[1]},{site[2]})"}


def _pauli(pauli):
    phase = {1: "+1", -1: "-1", 1j: "+i", -1j: "-i"}[pauli.sign]
    return {"phase": phase, "weight": pauli.weight,
            "terms": [{"qubit": q, "pauli": "IXYZ"[pauli[q]]}
                      for q in pauli.pauli_indices()]}


def _pauli_rank(paulis):
    """Rank over GF(2) of the physical binary Pauli support vectors."""
    pivots = {}
    for pauli in paulis:
        row = 0
        for q in pauli.pauli_indices():
            if pauli[q] in (1, 2):
                row |= 1 << (2 * q)
            if pauli[q] in (2, 3):
                row |= 1 << (2 * q + 1)
        while row:
            column = row.bit_length() - 1
            if column in pivots:
                row ^= pivots[column]
            else:
                pivots[column] = row
                break
    return len(pivots)


def native_measurement_report(native: NativeMeasurement, *, validate: bool = True) -> dict:
    """Return stabilizer definitions with physical IDs, site labels and signs.

    ``num_qubits`` retains the native Pauli-string dimension; it is not the
    number of qubits in an operator's support. The qubit registry contains
    only BB data and the active edge qubits that occur in these operators.
    Site labels use the paper's toric indexing, not a physical chip layout.
    Validation checks commutation and the signed product of vertex checks.
    """
    construction = native.construction
    bb_name_to_site = {f"{key[0]}_{key[1]}_{key[2]}": key
                       for key in construction.original_bb_checks}
    original_x = [pauli for key, pauli in construction.original_bb_checks.items() if key[0] == "X"]
    original_z = [pauli for key, pauli in construction.original_bb_checks.items() if key[0] == "Z"]
    original_rank = _pauli_rank(construction.original_bb_checks.values())
    original_bb_code = {
        "data_qubits": len(construction.data_sites),
        "x_check_count": len(original_x), "z_check_count": len(original_z),
        "x_rank": _pauli_rank(original_x), "z_rank": _pauli_rank(original_z),
        "stabilizer_rank": original_rank,
        "logical_qubits": len(construction.data_sites) - original_rank,
    }
    qubits = [
        {"qubit": q, "role": "bb_data", "paper_site": _site(site),
         "label": _site(site)["label"]}
        for site, q in sorted(construction.data_sites.items(), key=lambda item: item[1])
    ]
    qubits.extend(
        {"qubit": edge.qubit, "role": "lpu_data", "paper_site": None,
         "label": f"edge_{edge.index}", "edge_index": edge.index}
        for edge in sorted(construction.edges, key=lambda edge: edge.qubit)
    )

    checks = []
    modified_bb = 0
    vertex_product = stim.PauliString(native.num_qubits)
    for check in native.checks:
        entry = {"name": check.name, "kind": check.kind, "pauli": _pauli(check.pauli)}
        if check.kind == "bb":
            key = bb_name_to_site[check.name]
            original = construction.original_bb_checks[key]
            changed = original != check.pauli
            modified_bb += int(changed)
            entry.update({"paper_check_site": _site(key), "modified": changed,
                          "original_pauli": _pauli(original),
                          "added_support": [term for term in entry["pauli"]["terms"]
                                            if original[term["qubit"]] == 0]})
        elif check.kind == "vertex":
            vertex_product *= check.pauli
        checks.append(entry)

    matches_target = vertex_product == native.target_pauli
    if validate:
        if any(not a.pauli.commutes(b.pauli) for a, b in combinations(native.checks, 2)):
            raise ValueError("Noncommuting stabilizer definitions")
        if not matches_target:
            raise ValueError("The vertex product is not the requested logical Pauli")

    bb_count = sum(check.kind == "bb" for check in native.checks)
    target = " ".join(f"{letter}{index}" for index, letter in ((1, native.p1), (7, native.p7))
                      if letter != "I")
    return {
        "schema_version": SCHEMA_VERSION,
        "paper": {"url": PAPER_URL, "construction": "Appendix A.3"},
        "code": "gross [[144,12,12]]", "target": target,
        "p1": native.p1, "p7": native.p7, "lpu_mode": construction.lpu_mode,
        "num_qubits": native.num_qubits,
        "coordinate_convention": "paper sites (L/R, x mod 12, y mod 6); not chip coordinates",
        "original_bb_code": original_bb_code,
        "counts": {"bb_checks": bb_count,
                   "modified_bb_checks": modified_bb, "unchanged_bb_checks": bb_count - modified_bb,
                   "vertex_checks": sum(check.kind == "vertex" for check in native.checks),
                   "cycle_checks": sum(check.kind == "cycle" for check in native.checks),
                   "edge_qubits": len(construction.edges),
                   "bridge_qubits": sum(edge.kind == "bridge" for edge in construction.edges)},
        "qubits": qubits,
        "target_pauli": _pauli(native.target_pauli),
        "vertex_product": _pauli(vertex_product),
        "vertex_product_matches_target": matches_target,
        "checks": checks,
        "validation": {"performed": validate, "passed": True if validate else None,
                       "checks": list(VALIDATION_CHECKS)},
    }


def _readable_report(report):
    labels = {entry["qubit"]: entry["label"] for entry in report["qubits"]}

    def pauli_text(pauli):
        terms = " ".join(f"{term['pauli']}[{labels[term['qubit']]}|q{term['qubit']}]"
                         for term in pauli["terms"])
        return pauli["phase"] + " " + (terms or "I")

    original = report["original_bb_code"]
    lines = [f"Gross-code basic measurement: {report['target']}",
             f"Paulis (logical 1, 7): ({report['p1']}, {report['p7']})",
             f"Active auxiliary graph: {report['lpu_mode']}",
             "Counts: " + json.dumps(report["counts"], sort_keys=True),
             f"Original BB code (before measurement): {original['data_qubits']} physical data qubits; "
             f"{original['x_check_count'] + original['z_check_count']} listed checks "
             f"({original['x_check_count']} X, {original['z_check_count']} Z); "
             f"independent rank {original['stabilizer_rank']} "
             f"(X: {original['x_rank']}, Z: {original['z_rank']}); "
             f"encodes {original['logical_qubits']} logical qubits.",
             "Coordinates: " + report["coordinate_convention"],
             "Notation: Z[L(x,y)|qN] acts on BB data qubit qN at paper site L(x,y).",
             "          X[edge_e|qN] acts on edge auxiliary qubit qN, with edge index e.",
             "          Each line is a signed product; omitted qubits carry I.",
             "          +1/-1 is the operator sign, not a measured outcome.",
             "Validation: " + ("passed check commutation and signed vertex-product equality"
                               if report["validation"]["passed"] else "not run"),
             "", "TARGET", pauli_text(report["target_pauli"]),
             "Product of all vertex checks equals target: " + str(report["vertex_product_matches_target"]),
             "", "ORIGINAL AND DEFORMED BB CHECKS (ALL 144)"]
    for check in report["checks"]:
        if check["kind"] != "bb":
            continue
        lines.append(f"{check['name']}: {'modified' if check['modified'] else 'unchanged'}")
        if check["modified"]:
            lines.extend(["  original: " + pauli_text(check["original_pauli"]),
                          "  deformed: " + pauli_text(check["pauli"])])
        else:
            lines.append("  original = deformed: " + pauli_text(check["pauli"]))
    for kind, heading in (("vertex", "VERTEX CHECKS"), ("cycle", "CYCLE CHECKS")):
        lines.extend(["", heading])
        for check in report["checks"]:
            if check["kind"] == kind:
                lines.append(f"{check['name']}: " + pauli_text(check["pauli"]))
    return "\n".join(lines) + "\n"


def _remove_previous_circuit_exports(output_dir):
    """Remove only legacy raw components explicitly owned by the old manifest."""
    manifest_path = output_dir / "manifest.json"
    if manifest_path.is_symlink() or manifest_path.resolve().parent != output_dir:
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(manifest, dict) or not isinstance(manifest.get("measurements"), list):
        return
    owned_names = {
        f"measurement_{a}{b}_{component}.stim"
        for a, b in product("IXYZ", repeat=2) if a + b != "II"
        for component in ("prepare_edges", "prepare_bell", "syndrome", "round", "gaugeout")
    }
    for row in manifest["measurements"]:
        if not isinstance(row, dict) or not isinstance(row.get("stim_paths"), dict):
            continue
        for filename in row["stim_paths"].values():
            if not isinstance(filename, str) or filename not in owned_names:
                continue
            # No path from the manifest may traverse directories or redirect
            # cleanup through a symlink, even if it has a recognized basename.
            if Path(filename).name != filename or "/" in filename or "\\" in filename:
                continue
            path = output_dir / filename
            if path.is_symlink() or path.resolve().parent != output_dir:
                continue
            if path.is_file():
                path.unlink()


def export_basic_measurements(output_dir, *, validate: bool = True) -> list[dict]:
    """Export 15 stabilizer-only JSON/TXT reports and a manifest.

    Existing named reports are replaced. Raw Stim components are no longer
    exported; only recognized component files listed in the previous manifest
    are removed. All unrelated files and directories are left alone.
    """
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for p1, p7 in product("IXYZ", repeat=2):
        if p1 == p7 == "I":
            continue
        native = build_native_measurement(p1, p7, validate=False)
        report = native_measurement_report(native, validate=validate)
        stem = f"measurement_{p1}{p7}"
        json_path = output_dir / f"{stem}.json"
        txt_path = output_dir / f"{stem}.txt"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        txt_path.write_text(_readable_report(report), encoding="utf-8")
        summaries.append({"target": report["target"], "p1": p1, "p7": p7,
                          "lpu_mode": report["lpu_mode"], **report["counts"],
                          "validation_status": "passed" if validate else "not passed",
                          "json_path": str(json_path), "txt_path": str(txt_path)})
    _remove_previous_circuit_exports(output_dir)
    manifest = {"schema_version": SCHEMA_VERSION, "paper_url": PAPER_URL,
                "description": "Stabilizer definitions for the 15 nonidentity basic measurements on logical qubits 1 and 7",
                "validation_scope": list(VALIDATION_CHECKS),
                "measurements": [{**row, "json_path": Path(row["json_path"]).name,
                                  "txt_path": Path(row["txt_path"]).name}
                                 for row in summaries]}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
    return summaries
