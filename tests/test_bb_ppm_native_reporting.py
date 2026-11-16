"""Exports preserve the signed stabilizers while omitting circuit details."""
import json
from itertools import combinations, product
from pathlib import Path

import pytest
import stim

from lightstim.qec_code.BB_code.ppm.native import build_native_measurement
from lightstim.qec_code.BB_code.ppm.native_reporting import (
    export_basic_measurements, native_measurement_report,
)


LABELS = [a + b for a, b in product("IXYZ", repeat=2) if a + b != "II"]


def _decode_pauli(record, n):
    pauli = stim.PauliString(n)
    for term in record["terms"]:
        pauli[term["qubit"]] = term["pauli"]
    return {"+1": 1, "-1": -1, "+i": 1j, "-i": -1j}[record["phase"]] * pauli


@pytest.fixture(scope="module")
def exports(tmp_path_factory):
    directory = tmp_path_factory.mktemp("bb_native_reports")
    rows = export_basic_measurements(directory)
    reports = {row["p1"] + row["p7"]: json.loads(Path(row["json_path"]).read_text(encoding="utf-8"))
               for row in rows}
    return directory, rows, reports


@pytest.mark.parametrize("label", LABELS)
def test_exported_checks_are_the_full_signed_native_stabilizers(exports, label):
    _, _, reports = exports
    report = reports[label]
    n = report["num_qubits"]
    native = build_native_measurement(*label, validate=False)
    checks_by_name = {check["name"]: _decode_pauli(check["pauli"], n) for check in report["checks"]}
    assert checks_by_name == {check.name: check.pauli for check in native.checks}
    assert all(a.commutes(b) for a, b in combinations(checks_by_name.values(), 2))
    target = stim.PauliString(n)
    for check in report["checks"]:
        if check["kind"] == "vertex":
            target *= checks_by_name[check["name"]]
    assert target == native.target_pauli == _decode_pauli(report["target_pauli"], n)
    assert target == _decode_pauli(report["vertex_product"], n)
    assert report["vertex_product_matches_target"]
    assert report["validation"]["passed"]

    registry = {entry["qubit"]: entry for entry in report["qubits"]}
    assert set(registry) == set(native.construction.data_sites.values()) | set(native.edge_qubits)
    bb = [check for check in report["checks"] if check["kind"] == "bb"]
    assert len(bb) == 144
    for check in bb:
        original = _decode_pauli(check["original_pauli"], n)
        site = check["paper_check_site"]
        assert original == native.construction.original_bb_checks[site["kind"], site["x"], site["y"]]
        deformed = checks_by_name[check["name"]]
        assert original.weight == 6
        assert {registry[q]["role"] for q in original.pauli_indices()} == {"bb_data"}
        extra = stim.PauliString(n)
        for term in check["added_support"]:
            assert term["pauli"] == "Z"
            assert registry[term["qubit"]]["role"] == "lpu_data"
            extra[term["qubit"]] = "Z"
        assert len(check["added_support"]) <= 1
        assert original * extra == deformed
        assert check["modified"] == bool(extra.weight)
    assert report["counts"]["modified_bb_checks"] == sum(check["modified"] for check in bb)


def test_fifteen_reports_contain_only_operator_definitions(exports):
    directory, rows, reports = exports
    assert set(reports) == set(LABELS)
    assert len(rows) == 15
    assert len(list(directory.glob("measurement_*.json"))) == 15
    assert len(list(directory.glob("measurement_*.txt"))) == 15
    assert not list(directory.glob("*.stim"))
    for label, report in reports.items():
        mode, vertices, cycles, edges = (
            ("left", 12, 5, 18) if label in {"XI", "IZ", "XZ"} else
            ("right", 12, 3, 18) if label in {"IX", "ZI", "ZX"} else
            ("full", 23, 19, 47))
        assert report["schema_version"] == 2
        assert report["lpu_mode"] == mode
        assert report["counts"]["vertex_checks"] == vertices
        assert report["counts"]["cycle_checks"] == cycles
        assert report["counts"]["edge_qubits"] == edges
        assert report["counts"]["bridge_qubits"] == (11 if mode == "full" else 0)
        assert not {"active_lpu_qubits", "allocated_lpu_qubits"} & report["counts"].keys()
        assert {entry["role"] for entry in report["qubits"]} == {"bb_data", "lpu_data"}
        assert len(report["qubits"]) == 144 + edges
        assert not {"graph", "readout", "gaugeout", "schedule", "circuit_files", "circuit_scope"} & report.keys()
        for check in report["checks"]:
            assert "parts" not in check
            assert all(not key.startswith("readout") for key in check)
        txt = (directory / f"measurement_{label}.txt").read_text(encoding="utf-8")
        assert "ORIGINAL AND DEFORMED BB CHECKS (ALL 144)" in txt
        assert "VERTEX CHECKS" in txt and "CYCLE CHECKS" in txt
        assert txt.count("original = deformed:") == report["counts"]["unchanged_bb_checks"]
        assert txt.count("  original:") == report["counts"]["modified_bb_checks"]
        assert txt.count("  deformed:") == report["counts"]["modified_bb_checks"]
        assert all(word not in txt.lower() for word in ("readout", "bell", "gauge-out", "controlled", ".stim"))
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert len(manifest["measurements"]) == 15
    assert all(row["validation_status"] == "passed" for row in rows)
    assert all(not Path(row["json_path"]).is_absolute() for row in manifest["measurements"])
    assert all("stim_paths" not in row for row in rows + manifest["measurements"])


def test_original_bb_rank_counts_dependent_checks(exports):
    directory, _, reports = exports
    assert reports["ZZ"]["original_bb_code"] == {
        "data_qubits": 144, "x_check_count": 72, "z_check_count": 72,
        "x_rank": 66, "z_rank": 66, "stabilizer_rank": 132, "logical_qubits": 12,
    }
    txt = (directory / "measurement_ZZ.txt").read_text(encoding="utf-8")
    assert "Original BB code (before measurement)" in txt
    assert "144 listed checks" in txt and "independent rank 132" in txt


def test_remapped_physical_ids_and_coordinates_are_preserved():
    data = {(side, x, y): 1000 + (0 if side == "L" else 72) + 6 * x + y
            for side in "LR" for x in range(12) for y in range(6)}
    bb = {(basis, x, y): 1200 + (0 if basis == "X" else 72) + 6 * x + y
          for basis in "XZ" for x in range(12) for y in range(6)}
    native = build_native_measurement("Y", "X", data_index=data, bb_check_indices=bb,
                                      ancilla_start=1500, validate=False)
    report = native_measurement_report(native, validate=False)
    assert report["validation"]["passed"] is None
    assert not report["validation"]["performed"]
    registry = {entry["qubit"]: entry for entry in report["qubits"]}
    for site, q in data.items():
        assert registry[q]["paper_site"] == {"kind": site[0], "x": site[1], "y": site[2],
                                             "label": f"{site[0]}({site[1]},{site[2]})"}
    assert not set(bb.values()) & registry.keys()
    for edge in native.construction.edges:
        assert registry[edge.qubit]["edge_index"] == edge.index
        assert registry[edge.qubit]["label"] == f"edge_{edge.index}"
    assert {check["name"]: _decode_pauli(check["pauli"], native.num_qubits) for check in report["checks"]} == {
        check.name: check.pauli for check in native.checks}
    assert report["original_bb_code"]["stabilizer_rank"] == 132


def test_reexport_is_deterministic_and_preserves_unrelated_files(exports):
    directory, _, _ = exports
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    unrelated = directory / "notes.txt"
    unrelated.write_text("user notes", encoding="utf-8")
    export_basic_measurements(directory)
    assert all((directory / name).read_bytes() == data for name, data in before.items())
    assert unrelated.read_text(encoding="utf-8") == "user notes"


def test_legacy_cleanup_requires_manifest_ownership_and_safe_basename(tmp_path):
    directory = tmp_path / "basic_measurements"
    directory.mkdir()
    owned = [f"measurement_ZZ_{component}.stim"
             for component in ("prepare_edges", "prepare_bell", "syndrome", "round", "gaugeout")]
    keep = ["unrelated.stim", "measurement_XI_round.stim", "measurement_ZZ_full.stim"]
    for name in owned + keep:
        (directory / name).write_text("retained unless owned", encoding="utf-8")
    outside = tmp_path / "measurement_YY_round.stim"
    outside.write_text("outside export directory", encoding="utf-8")
    nested = directory / "other" / "measurement_YX_round.stim"
    nested.parent.mkdir()
    nested.write_text("nested unrelated file", encoding="utf-8")
    ler = tmp_path / "ler" / "physical.stim"
    ler.parent.mkdir()
    ler.write_text("full LER circuit", encoding="utf-8")
    filenames = owned + ["unrelated.stim", "measurement_ZZ_full.stim",
                         "../measurement_YY_round.stim", str(outside.resolve()),
                         "other/measurement_YX_round.stim", "other\\measurement_YX_round.stim"]
    manifest = {"schema_version": 1, "measurements": [
        {"stim_paths": {str(i): name for i, name in enumerate(filenames)}}]}
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    rows = export_basic_measurements(directory, validate=False)
    assert all(not (directory / name).exists() for name in owned)
    assert all((directory / name).read_text(encoding="utf-8") == "retained unless owned" for name in keep)
    assert outside.read_text(encoding="utf-8") == "outside export directory"
    assert nested.read_text(encoding="utf-8") == "nested unrelated file"
    assert ler.read_text(encoding="utf-8") == "full LER circuit"
    assert all(row["validation_status"] == "not passed" for row in rows)
    assert all("stim_paths" not in row for row in rows)
