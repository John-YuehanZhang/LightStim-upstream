"""Validate recorded scientific statistics and portable BB PPM plotting."""
import copy
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from benchmarks.logical_ops import plot_bb_ppm as plot


@pytest.fixture
def portable_results(tmp_path):
    for name in ("results.csv", "manifest.json"):
        shutil.copyfile(plot.DEFAULT_RESULTS.parent / name, tmp_path / name)
    return tmp_path / "results.csv"


def rewrite(path, *, change_rows=None, change_manifest=None):
    manifest_path = path.with_name("manifest.json")
    manifest = json.loads(manifest_path.read_text())
    if change_rows is not None:
        change_rows(manifest["rows"])
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=plot.CSV_FIELDS)
            writer.writeheader()
            writer.writerows(manifest["rows"])
        manifest["results_csv"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if change_manifest is not None:
        change_manifest(manifest)
    manifest_path.write_text(json.dumps(manifest))


@pytest.fixture
def zero_first_point_results(portable_results):
    """Exercise zero-count plotting independently of the recorded sample draw."""
    def zero_first(rows):
        n = rows[0]["shots"]
        rows[0].update(logical_failures=0, nonconverged=0, converged_correct=n,
                       converged_logical_failure=0, unconverged_correct_observables=0,
                       unconverged_logical_failure=0, ler=0.0, nonconvergence_rate=0.0,
                       ler_95_exact_two_sided_low=0.0,
                       ler_95_exact_two_sided_high=1 - .025 ** (1 / n),
                       ler_95_one_sided_upper=1 - .05 ** (1 / n))
    rewrite(portable_results, change_rows=zero_first)
    return portable_results


def test_recorded_results_are_portable_and_exact(portable_results):
    data = plot.read_results(portable_results)
    assert [(r["p"], r["shots"]) for r in data["results"]] == [
        (.0001, 5), (.0005, 5), (.001, 5)]
    assert data["manifest"]["raw_shots_in_repository"] is False
    assert "/home/" not in json.dumps(data["manifest"])
    assert plot.read_results(portable_results.with_name("manifest.json")) == data


def test_published_notebook_identifies_its_displayed_results():
    root = Path(__file__).resolve().parents[1]
    notebook_path = root / "notebooks/LogicalOps/logical_PPM_BB.ipynb"
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    recorded = notebook["metadata"]["bb_ppm_results"]
    setup = next(cell for cell in notebook["cells"]
                 if "bb-ppm-setup" in cell["metadata"].get("tags", []))
    stdout = [line for output in setup["outputs"] if output.get("name") == "stdout"
              for line in "".join(output["text"]).splitlines()]
    assert f"Result table: {recorded['source']}" in stdout
    for key in ("source", "manifest"):
        digest = hashlib.sha256((root / recorded[key]).read_bytes()).hexdigest()
        assert digest == recorded[f"{key}_sha256"]
    plot.read_results(
        root / recorded["source"], manifest_path=root / recorded["manifest"],
        expected_experiment={"target": "Y1"},
        shots_by_p={.0005: 1_000_000, .001: 1_000_000, .002: 100_000},
    )


def test_csv_digest_and_manifest_rows_are_both_verified(zero_first_point_results):
    portable_results = zero_first_point_results
    portable_results.write_bytes(portable_results.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="SHA256"):
        plot.read_results(portable_results)
    rewrite(portable_results, change_manifest=lambda m: m["results_csv"].update(
        sha256=hashlib.sha256(portable_results.read_bytes()).hexdigest()))
    rewrite(portable_results, change_manifest=lambda m: m["rows"][0].update(nonconverged=1))
    with pytest.raises(ValueError, match="recorded statistics"):
        plot.read_results(portable_results)


@pytest.mark.parametrize("change,match", [
    ({"logical_failures": 1}, "counts"),
    ({"nonconverged": 1}, "counts"),
    ({"ler_95_exact_two_sided_high": 0}, "confidence interval"),
    ({"ler_95_one_sided_upper": .5}, "confidence interval"),
    ({"sum_decode_cpu_seconds": -1}, "CPU"),
    ({"ler": float("nan")}, "finite"),
    ({"source_manifest_sha256": "bad"}, "SHA256"),
])
def test_invalid_statistics_rejected_even_when_rehashed(zero_first_point_results, change, match):
    portable_results = zero_first_point_results
    rewrite(portable_results, change_rows=lambda rows: rows[0].update(change))
    with pytest.raises(ValueError, match=match):
        plot.read_results(portable_results)


@pytest.mark.parametrize("kwargs", [
    {"expected_plan_sha256": "0"*64},
    {"decoder_params": {"alpha": 1}},
    {"shots_by_p": {.0001: 50, .0005: 50, .001: 50}},
    {"expected_experiment": {"target": "X2 Z7 X12"}},
    {"expected_experiment": {"rounds": 9}},
    {"expected_experiment": {"noise_model": "different"}},
    {"expected_experiment": {"initial_state": "Z"}},
    {"expected_experiment": {"final_basis": "Z"}},
])
def test_incompatible_experiment_rejected(portable_results, kwargs):
    with pytest.raises(ValueError):
        plot.read_results(portable_results, **kwargs)


def test_matching_configuration_accepts_tuple_interval(portable_results):
    data = plot.read_results(portable_results)
    params = copy.deepcopy(data["parameters"])
    params["gamma_dist_interval"] = tuple(params["gamma_dist_interval"])
    result = plot.read_results(portable_results,
        expected_plan_sha256=data["experiment"]["compiled_plan_sha256"],
        decoder_params=params, shots_by_p=data["requested_shots_by_p"],
        expected_experiment={"target": "Y1", "rounds": 10,
                             "initial_state": "X", "final_basis": "X"})
    assert result == data


def test_partial_run_does_not_invent_missing_points(portable_results):
    rewrite(portable_results, change_rows=lambda rows: rows.pop(),
            change_manifest=lambda m: m.update(status="partial"))
    data = plot.read_results(portable_results,
                             shots_by_p={.0001: 5, .0005: 5, .001: 5})
    assert len(data["results"]) == 2
    rewrite(portable_results, change_manifest=lambda m: m.update(status="complete"))
    with pytest.raises(ValueError, match="missing"):
        plot.read_results(portable_results)


def test_csv_sidecar_manifest_supported(portable_results):
    portable_results.with_name("manifest.json").rename(portable_results.with_suffix(".manifest.json"))
    assert len(plot.read_results(portable_results)["results"]) == 3


def test_connected_figure_preserves_zero_and_style(zero_first_point_results):
    import matplotlib as mpl

    portable_results = zero_first_point_results
    data = plot.read_results(portable_results)
    before = dict(mpl.rcParams)
    figure = plot.create_figure(data)
    assert dict(mpl.rcParams) == before
    axes = figure.axes[0]
    points = axes.containers[0].lines[0]
    assert list(points.get_xdata()) == [.0001, .0005, .001]
    assert list(points.get_ydata()) == [row["ler"] for row in data["results"]]
    assert points.get_ydata()[0] == 0
    assert points.get_linestyle() == "-"
    assert axes.get_yscale() == "linear"
    intervals = axes.containers[0].lines[2][0].get_segments()
    assert intervals[0][0][1] == 0
    assert intervals[0][1][1] == data["results"][0]["ler_95_exact_two_sided_high"]


def test_notebook_display_keeps_resources_separator_and_native_png(portable_results, monkeypatch):
    display_module = pytest.importorskip("IPython.display")

    outputs = []
    monkeypatch.setattr(display_module, "display", outputs.append)
    data = plot.display_results(portable_results)
    resources = dict(outputs[0].itertuples(index=False, name=None))
    assert resources["Measurement instructions"] == 1
    assert resources["Shift instructions"] == 0
    assert resources["Noiseless circuit silent"] is True
    assert resources["Compiled plan SHA256"] == data["experiment"]["compiled_plan_sha256"]
    assert resources["Clean circuit SHA256"] == data["experiment"]["clean_circuit_sha256"]
    assert len([key for key in resources if key.startswith("Original campaign")]) == len(
        data["manifest"].get("campaigns", []))
    assert outputs[1].data == "<hr>"
    image = next(item for item in outputs if isinstance(item, display_module.Image))
    assert image.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.width == 800 and image.height == 540


def test_import_does_not_change_environment_or_import_science_backends():
    code = """
import os, sys
before = dict(os.environ)
from benchmarks.logical_ops import plot_bb_ppm
assert dict(os.environ) == before
assert not any(key in sys.modules for key in ('stim', 'relay_bp', 'matplotlib', 'IPython'))
"""
    subprocess.run([sys.executable, "-c", code], check=True,
                   cwd=Path(__file__).resolve().parents[1])
