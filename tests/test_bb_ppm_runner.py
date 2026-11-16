"""Headless notebook execution controls; no physical sampling in these tests."""

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

nbformat = pytest.importorskip("nbformat")
pytest.importorskip("nbclient")


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "bb_ppm_runner", REPO / "notebooks/LogicalOps/run_bb_ppm.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def sample_notebook(tmp_path):
    (tmp_path / "lightstim").mkdir()
    source = tmp_path / "notebooks" / "bb_ppm.ipynb"
    source.parent.mkdir()
    nb = nbformat.v4.new_notebook(cells=[
        nbformat.v4.new_code_cell("print('report')"),
        nbformat.v4.new_code_cell("raise AssertionError('must not sample')",
                                 metadata={"tags": [runner.LER_TAG]}),
    ])
    nb.cells[1].outputs = [nbformat.v4.new_output("stream", name="stdout", text="old LER")]
    nbformat.write(nb, source)
    return source


def test_reports_only_skips_sampling_and_uses_current_environment(tmp_path, monkeypatch):
    source = sample_notebook(tmp_path)
    original = source.read_bytes()
    output = tmp_path / "results" / "reports.ipynb"
    monkeypatch.setenv("RAYON_NUM_THREADS", "2")

    class FakeClient:
        def __init__(self, notebook, **kwargs):
            self.nb, self.kw = notebook, kwargs

        def execute(self):
            assert self.kw["timeout"] is None
            assert self.kw["resources"]["metadata"]["path"] == str(tmp_path)
            assert os.environ["RAYON_NUM_THREADS"] == "3"
            assert os.environ["BB_PPM_OUTPUT_DIR"] == str(output.parent)
            kernel = Path(os.environ["JUPYTER_PATH"].split(os.pathsep)[0]) / "kernels/bb-ppm-current/kernel.json"
            assert json.loads(kernel.read_text())["argv"][0] == sys.executable
            for i, cell in enumerate(self.nb.cells):
                if self.kw["skip_cells_with_tag"] in cell.metadata.get("tags", []):
                    continue
                cell.outputs = [nbformat.v4.new_output("stream", name="stdout", text="new report")]
                self.kw["on_cell_executed"](cell=cell, cell_index=i, execute_reply={})

    monkeypatch.setattr(runner, "StreamingNotebookClient", FakeClient)
    runner.run_notebook(source, output.parent, output, reports_only=True, threads=3)
    saved = nbformat.read(output, as_version=4)
    assert saved.cells[0].outputs[0].text == "new report"
    assert saved.cells[1].outputs == []
    assert saved.metadata.bb_ppm_run.state == "complete"
    assert source.read_bytes() == original
    assert os.environ["RAYON_NUM_THREADS"] == "2"


@pytest.mark.parametrize("error", [RuntimeError("decoder failed"), KeyboardInterrupt()])
def test_failures_and_interrupts_save_partial_notebook(tmp_path, monkeypatch, error):
    source = sample_notebook(tmp_path)
    output = tmp_path / "results" / "executed.ipynb"

    class FailingClient:
        def __init__(self, notebook, **kwargs):
            self.nb = notebook

        def execute(self):
            self.nb.cells[0].outputs = [nbformat.v4.new_output("stream", name="stdout", text="finished report")]
            raise error

    monkeypatch.setattr(runner, "StreamingNotebookClient", FailingClient)
    with pytest.raises(type(error)):
        runner.run_notebook(source, output.parent, output)
    saved = nbformat.read(output, as_version=4)
    assert saved.cells[0].outputs[0].text == "finished report"
    assert saved.metadata.bb_ppm_run.state == (
        "interrupted" if isinstance(error, KeyboardInterrupt) else "failed")


def test_reports_only_fails_closed_if_ler_tag_is_missing(tmp_path):
    source = sample_notebook(tmp_path)
    nb = nbformat.read(source, as_version=4)
    nb.cells[-1].metadata.tags = []
    nbformat.write(nb, source)
    with pytest.raises(ValueError, match="tagged"):
        runner.run_notebook(source, tmp_path / "results", tmp_path / "result.ipynb", reports_only=True)


def test_source_notebook_cannot_be_overwritten(tmp_path):
    source = sample_notebook(tmp_path)
    with pytest.raises(ValueError, match="overwrite"):
        runner.run_notebook(source, tmp_path / "results", source)
