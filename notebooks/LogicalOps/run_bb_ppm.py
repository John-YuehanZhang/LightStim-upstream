"""Execute the BB PPM notebook on a server using the current Python environment.

All experiment, sampling, checkpointing, and plotting logic stays in the
notebook and LightStim. This file only launches a kernel and saves its outputs.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

import nbformat
from nbclient import NotebookClient


LER_TAG = "bb-ppm-ler"


class StreamingNotebookClient(NotebookClient):
    """Retain notebook outputs while forwarding printed progress to the terminal."""

    def process_message(self, msg, cell, cell_index):
        if msg.get("msg_type") == "stream":
            stream = sys.stderr if msg["content"].get("name") == "stderr" else sys.stdout
            print(msg["content"].get("text", ""), end="", file=stream, flush=True)
        return super().process_message(msg, cell, cell_index)


def save_notebook(notebook, destination: Path) -> None:
    """Replace only the generated notebook, avoiding a half-written checkpoint."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".ipynb", prefix=".bb-ppm-",
        dir=destination.parent, delete=False,
    ) as handle:
        temporary = Path(handle.name)
        nbformat.write(notebook, handle)
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def kernel_environment(output_dir: Path, threads: int | None):
    """Use this interpreter without installing a persistent Jupyter kernelspec."""
    with tempfile.TemporaryDirectory(prefix="bb-ppm-kernel-") as temporary:
        kernel_dir = Path(temporary) / "kernels" / "bb-ppm-current"
        kernel_dir.mkdir(parents=True)
        (kernel_dir / "kernel.json").write_text(json.dumps({
            "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
            "display_name": "BB PPM (current Python)", "language": "python",
        }), encoding="utf-8")
        updates = {
            "JUPYTER_PATH": temporary + (os.pathsep + os.environ["JUPYTER_PATH"]
                                         if os.environ.get("JUPYTER_PATH") else ""),
            "BB_PPM_OUTPUT_DIR": str(output_dir),
            "PYTHONUNBUFFERED": "1",
            "MPLBACKEND": "module://matplotlib_inline.backend_inline",
        }
        if threads is not None:
            updates["RAYON_NUM_THREADS"] = str(threads)
        original = {key: os.environ.get(key) for key in updates}
        os.environ.update(updates)
        try:
            yield
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def run_notebook(
    source: Path, output_dir: Path, output_notebook: Path, *,
    reports_only: bool = False, threads: int | None = None,
) -> None:
    source = source.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_notebook = output_notebook.expanduser().resolve()
    if source == output_notebook:
        raise ValueError("The executed notebook must not overwrite bb_ppm.ipynb.")
    if threads is not None and threads < 1:
        raise ValueError("threads must be a positive integer")
    notebook = nbformat.read(source, as_version=4)
    code_indices = [i for i, cell in enumerate(notebook.cells) if cell.cell_type == "code"]
    ler_indices = [i for i in code_indices if LER_TAG in notebook.cells[i].metadata.get("tags", [])]
    # Fail closed if the teaching notebook changes, rather than unexpectedly
    # sampling in reports-only mode or silently skipping the wrong cell.
    if len(ler_indices) != 1 or ler_indices[0] != code_indices[-1]:
        raise ValueError("Expected exactly one final code cell tagged 'bb-ppm-ler'.")
    repo = next((p for p in source.parents if (p / "lightstim").is_dir()), None)
    if repo is None:
        raise ValueError("Run a notebook located inside the LightStim repository.")
    for cell in notebook.cells:
        if cell.cell_type == "code":
            cell.outputs = []
            cell.execution_count = None
            cell.metadata.pop("execution", None)
    notebook.metadata["bb_ppm_run"] = {
        "mode": "reports-only" if reports_only else "full",
        "state": "running", "started_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.executable, "output_dir": str(output_dir),
        "rayon_threads": str(threads) if threads is not None else os.environ.get("RAYON_NUM_THREADS"),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Python: {sys.executable}", flush=True)
    print(f"Mode: {'reports only; no physical LER run' if reports_only else 'full notebook including LER'}", flush=True)
    print(f"Artifacts: {output_dir}", flush=True)
    print(f"Executed notebook: {output_notebook}", flush=True)
    save_notebook(notebook, output_notebook)

    def cell_start(cell, cell_index, **_):
        if cell.cell_type == "code":
            skipped = reports_only and LER_TAG in cell.metadata.get("tags", [])
            print(f"Notebook cell {cell_index}: {'skip LER' if skipped else 'running'}", flush=True)

    def cell_finished(**_):
        save_notebook(notebook, output_notebook)

    try:
        with kernel_environment(output_dir, threads):
            client = StreamingNotebookClient(
                notebook, kernel_name="bb-ppm-current", timeout=None,
                resources={"metadata": {"path": str(repo)}},
                allow_errors=False,
                skip_cells_with_tag=LER_TAG if reports_only else "skip-execution",
                on_cell_start=cell_start, on_cell_executed=cell_finished,
            )
            client.execute()
    except BaseException as error:
        notebook.metadata["bb_ppm_run"]["state"] = (
            "interrupted" if isinstance(error, KeyboardInterrupt) else "failed")
        notebook.metadata["bb_ppm_run"]["error"] = str(error)
        raise
    else:
        notebook.metadata["bb_ppm_run"]["state"] = "complete"
    finally:
        notebook.metadata["bb_ppm_run"]["finished_utc"] = datetime.now(timezone.utc).isoformat()
        save_notebook(notebook, output_notebook)
        print(f"Saved notebook: {output_notebook}", flush=True)


def positive_int(value: str) -> int:
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-only", action="store_true",
                        help="Run setup and Cells 1-4; skip physical construction and LER sampling.")
    parser.add_argument("--threads", type=positive_int,
                        help="Relay-BP Rayon threads; otherwise inherit RAYON_NUM_THREADS/backend default.")
    parser.add_argument("--output-dir", type=Path,
                        help="Artifacts directory (default: assets/bb_ppm beside the source notebook).")
    parser.add_argument("--output-notebook", type=Path,
                        help="Executed notebook filename; the source notebook is never overwritten.")
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().with_name("bb_ppm.ipynb")
    output_dir = args.output_dir or source.parent / "assets" / "bb_ppm"
    suffix = "reports" if args.reports_only else "executed"
    output_notebook = args.output_notebook or output_dir / f"bb_ppm.{suffix}.ipynb"
    try:
        run_notebook(source, output_dir, output_notebook,
                     reports_only=args.reports_only, threads=args.threads)
    except KeyboardInterrupt:
        print("Interrupted. Completed point checkpoints are retained.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Notebook execution failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
