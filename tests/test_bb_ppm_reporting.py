"""Reports must preserve the compiler's signed implementation and all witnesses."""

import csv
import json
from dataclasses import asdict
from itertools import groupby
from pathlib import Path
from xml.etree import ElementTree

import pytest
import stim

from lightstim.qec_code.BB_code.ppm.lowering import (
    BBPPMCompiler, Measure, PauliFrameUpdate, RecordParity, Shift,
)
from lightstim.qec_code.BB_code.ppm.reporting import (
    export_plan_report, export_rotation_catalog, export_shift_and_native_catalogs,
    format_logical_pauli, format_record_parity, render_native_circuit_excerpt,
)
from lightstim.qec_code.BB_code.ppm.shift import (
    build_tour_de_gross_code, logical_shift_tableau, logical_shift_tableaus,
)


@pytest.fixture(scope="module")
def code():
    return build_tour_de_gross_code()


@pytest.fixture(scope="module")
def compiler(code):
    return BBPPMCompiler(logical_shift_tableaus(code))


def test_signed_human_labels_and_bit_parity():
    assert format_logical_pauli("IXIIIIZIIIIX") == "+X2 Z7 X12"
    assert format_logical_pauli("-IXIIIIZIIIIX") == "-X2 Z7 X12"
    assert format_logical_pauli("I" * 12) == "+I"
    assert format_logical_pauli(stim.PauliString("X_____Y_____")) == "+X1 Y7"
    assert format_record_parity(RecordParity(("s", "m", "r"), True)) == "m XOR r XOR s XOR 1"
    assert format_record_parity(RecordParity()) == "0"
    with pytest.raises(ValueError, match="Hermitian"):
        format_logical_pauli("iX")


def test_native_catalog_roundtrip_preserves_all_shift_actions(compiler, code, tmp_path):
    report = export_shift_and_native_catalogs(compiler, code, tmp_path)
    assert report["counts"] == {"logical_shifts": 36, "native_measurements": 540}
    assert all(Path(p).is_absolute() and Path(p).is_file() for p in report["paths"].values())
    shifts = json.loads(Path(report["paths"]["shifts_json"]).read_text(encoding="utf-8"))["rows"]
    native = json.loads(Path(report["paths"]["native_json"]).read_text(encoding="utf-8"))["rows"]
    assert shifts == report["rows"]["shifts"]
    assert native == report["rows"]["native_measurements"]
    for row in shifts:
        tableau = stim.Tableau.from_conjugated_generators(
            xs=[stim.PauliString(p) for p in row["logical_x_image_strings"]],
            zs=[stim.PauliString(p) for p in row["logical_z_image_strings"]],
        )
        assert tableau == compiler.shift_maps[row["x"], row["y"]]
        assert logical_shift_tableau(code, *row["physical_displacement"]) == tableau
    for row in native:
        image = compiler.shift_maps[row["shift_x"], row["shift_y"]](
            stim.PauliString(row["basic_pauli_string"]))
        assert image == stim.PauliString(row["conjugated_pauli_string"])
        assert image * (-1 if row["outcome_xor"] else 1) == stim.PauliString(row["pauli_string"])
        sequence = row["operations"]
        if row["shift_x"] or row["shift_y"]:
            assert sequence[0] == {"type": "Shift", "x": row["inverse_x"], "y": row["inverse_y"]}
            assert sequence[-1] == {"type": "Shift", "x": row["shift_x"], "y": row["shift_y"]}
        assert sum(op["type"] == "Measure" for op in sequence) == 1
    with Path(report["paths"]["native_csv"]).open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    assert len(csv_rows) == 540
    assert json.loads(csv_rows[0]["operations"]) == native[0]["operations"]


def test_rotation_catalog_keeps_all_253_witnesses_for_245_axes(compiler, tmp_path):
    report = export_rotation_catalog(compiler, tmp_path)
    axes = report["rows"]["axes"]
    implementations = report["rows"]["implementations"]
    assert report["counts"] == {"axes": 245, "implementations": 253}
    valid_native = {p for p in compiler.native_measurements if p[0] != "I" and set(p[1:]) != {"I"}}
    assert {r["pauli_string"][1:] for r in implementations} == valid_native
    for row in axes:
        axis = row["axis_string"][1:]
        assert axis[0] == "I" and set(axis) != {"I"}
        choices = row["implementations"]
        assert len(choices) == row["implementation_count"]
        selected = [r for r in choices if r["chosen"]]
        assert len(selected) == 1
        assert selected[0]["pauli_string"][1:] == compiler.native_rotations[axis].pauli
        for choice in choices:
            native = stim.PauliString(choice["pauli_string"])
            native[0] = "I"
            assert native == stim.PauliString(row["axis_string"])
            assert {choice["q"][0], choice["prepare"][0], choice["readout"][0]} == {"X", "Y", "Z"}
    x7 = next(r for r in axes if r["axis"] == "+X7")
    assert {r["q"] for r in x7["implementations"]} == {"X1", "Y1", "Z1"}
    saved = json.loads(Path(report["paths"]["axes_json"]).read_text(encoding="utf-8"))
    assert saved["rows"] == axes
    assert saved["implementation_count"] == 253


def _restore_operations(rows):
    result = []
    for row in rows:
        if row["operation"] == "Shift":
            result.append(Shift(row["x"], row["y"]))
        elif row["operation"] == "Measure":
            result.append(Measure(row["p1"], row["p7"], row["key"]))
        else:
            result.append(PauliFrameUpdate(row["pauli_string"][1:],
                          RecordParity(tuple(row["parity_keys"]), row["parity_constant"])))
    return tuple(result)


def test_default_plan_report_roundtrips_actual_13_operations(compiler, tmp_path):
    plan = compiler.compile(((2, "X"), (7, "Z"), (12, "X")), prefix="ppm0")
    assert len(plan.rotations) == 1
    report = export_plan_report(compiler, [plan], tmp_path)
    summary = report["rows"]["summary"][0]
    assert summary["target"] == "+X2 Z7 X12"
    assert summary["data_base"] == "+Y7"
    assert summary["central_measurement"] == "+Y7"
    assert summary["central_method"] == "direct"
    assert summary["native_joint"] is None
    assert (summary["operation_count"], summary["measurements"], summary["shifts"], summary["frame_updates"]) == (13, 7, 4, 2)
    assert summary["output"] == "ppm0.result"
    assert report["rows"]["path"][0]["after"] == "+Y7"
    rows = report["rows"]["operations"]
    assert [key for key, _ in groupby(r["group"] for r in rows)] == ["pre0", "central", "post0"]
    assert _restore_operations(rows) == plan.operations
    saved = json.loads(Path(report["paths"]["plans_json"]).read_text(encoding="utf-8"))["plans"][0]
    assert _restore_operations(saved["operations"]) == plan.operations
    assert saved["central_method"] == "direct"
    assert RecordParity(tuple(saved["output"]["keys"]), saved["output"]["constant"]) == plan.output
    assert saved["path"][0] == {"before": "+IXIIIIZIIIIX", "axis": "+IXIIIIXIIIIX",
                                "after": "+IIIIIIYIIIII", "direction": 1}


def test_negative_native_target_and_identity_are_not_given_fake_paths(compiler, tmp_path):
    plans = [compiler.compile("-IXIIIIIIIIII", prefix="negative"), compiler.compile("I" * 12)]
    report = export_plan_report(compiler, plans, tmp_path)
    assert report["rows"]["summary"][0]["target"] == "-X2"
    assert report["rows"]["summary"][1]["target"] == "+I"
    assert report["rows"]["summary"][1]["operation_count"] == 0
    assert report["rows"]["path"] == []
    assert all(row["group"] == "central" for row in report["rows"]["operations"])


def test_signed_three_rotation_endpoint_and_inverse_group_order(compiler, tmp_path):
    plan = compiler.compile(((2, "X"), (8, "X"), (9, "X")), prefix="long")
    report = export_plan_report(compiler, [plan], tmp_path)
    path = report["rows"]["path"]
    assert len(path) == 3
    assert path[-1]["after"] == "-X2 X3 X4"
    assert report["rows"]["summary"][0]["data_base"] == "+X2 X3 X4"
    assert report["rows"]["summary"][0]["central_measurement"] == "+X2 X3 X4"
    assert report["rows"]["summary"][0]["central_method"] == "direct"
    assert report["rows"]["summary"][0]["native_joint"] is None
    assert plan.output == RecordParity(("long.result",), True)
    rows = report["rows"]["operations"]
    assert [key for key, _ in groupby(r["group"] for r in rows)] == [
        "pre0", "pre1", "pre2", "central", "post2", "post1", "post0"]
    assert _restore_operations(rows) == plan.operations


def test_non_native_endpoint_report_keeps_prepared_pivot_joint(compiler, tmp_path):
    fresh = BBPPMCompiler(compiler.shift_maps)
    endpoint = "IIIXIXXXIXII"
    assert endpoint not in fresh.native_measurements
    plan = fresh.compile(endpoint, prefix="fallback")
    report = export_plan_report(fresh, [plan], tmp_path)
    summary = report["rows"]["summary"][0]
    assert summary["data_base"] == "+X4 X6 X7 X8 X10"
    assert summary["central_measurement"] == "+X1 X4 X6 X7 X8 X10"
    assert summary["native_joint"] == summary["central_measurement"]
    assert summary["central_method"] == "prepared_pivot"
    assert summary["output"] == "fallback.prepare XOR fallback.result"
    assert _restore_operations(report["rows"]["operations"]) == plan.operations


def test_search_progress_export_preserves_actual_trace_and_cache_status(compiler, tmp_path):
    fresh = BBPPMCompiler(compiler.shift_maps)
    first = fresh.compile("IXIIIIZIIIIX", prefix="first")
    repeated = fresh.compile("IXIIIIZIIIIX", prefix="repeat")
    report = export_plan_report(fresh, [first, repeated], tmp_path)
    rows = report["rows"]["search"]
    expected = []
    for step, plan in enumerate((first, repeated)):
        for progress in plan.search_trace:
            row = asdict(progress)
            if row["meeting_pauli"] is not None:
                row["meeting_pauli"] = format_logical_pauli(row["meeting_pauli"])
            expected.append({"step": step, "target": format_logical_pauli(plan.target),
                             "search_status": plan.search_status, **row})
    assert rows == expected
    assert {r["search_status"] for r in rows} == {"searched", "cached"}
    saved = json.loads(Path(report["paths"]["plans_json"]).read_text(encoding="utf-8"))["plans"]
    for plan, payload in zip((first, repeated), saved):
        assert payload["search_status"] == plan.search_status
        assert payload["search_trace"] == [asdict(row) for row in plan.search_trace]
    with Path(report["paths"]["search_csv"]).open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    assert len(csv_rows) == len(rows)
    for exported, row in zip(csv_rows, rows):
        for key, value in row.items():
            assert exported[key] == ("" if value is None else str(value))


def test_direct_measurement_reports_have_no_fabricated_search_rows(compiler, tmp_path):
    plans = [compiler.compile("XIIIIIIIIIII"), compiler.compile("I" * 12)]
    report = export_plan_report(compiler, plans, tmp_path)
    assert report["rows"]["search"] == []
    payloads = json.loads(Path(report["paths"]["plans_json"]).read_text(encoding="utf-8"))["plans"]
    assert [payload["search_status"] for payload in payloads] == ["native", "identity"]
    assert all(payload["search_trace"] == [] for payload in payloads)


@pytest.mark.parametrize("pauli,ticks,qubits,previous_layer", [
    ("Y", (13, 14), (342, 377), "Ancilla resets"),
    ("X", (14, 15), (342, 293), "Preceding gate layer"),
])
def test_native_excerpt_uses_actual_stim_layers(pauli, ticks, qubits, previous_layer):
    from lightstim.protocols.bb_ppm import BBPPMExperiment, BBPPMStep

    experiment = BBPPMExperiment(None, [BBPPMStep(((1, pauli),))], rounds=1)
    circuit = experiment.build()
    original = circuit.copy()
    lpu = circuit.get_final_qubit_coordinates().keys() - experiment.system.local_to_global_map["bb"].values()
    svg, caption = render_native_circuit_excerpt(circuit, lpu)
    assert circuit == original
    assert f"Tick {ticks[0]} · {previous_layer}" in svg
    assert f"Tick {ticks[1]} · CX q{qubits[0]} → q{qubits[1]}" in svg
    assert ("Bell pair" in caption) == (pauli == "Y")
    root = ElementTree.fromstring(svg)
    ids = [node.attrib["id"] for node in root.iter() if "id" in node.attrib]
    assert len(ids) == len(set(ids))
    panels = root.findall("{*}svg")
    assert len(panels) == 2
    for panel in panels:
        x, y, width, height = map(float, panel.attrib["viewBox"].split())
        labels = panel.findall("{*}text")
        assert [node.text for node in labels] == [f"q{q}" for q in qubits]
        for node in labels:
            assert x < float(node.attrib["x"]) < x + width
            assert y < float(node.attrib["y"]) < y + height
