"""Notebook presentation for the existing frozen full-circuit experiment."""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import time

for key in ('RAYON_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'

from bb_ppm_large_run import read_run_snapshot, attach_or_resume, EXPECTED_PLAN_SHA256

COMPILED_PLAN_SHA = 'f028b3134db48c3f0f7a6c5b9ae97ddcd7c95cffcb59f8cfc0afc28ad1679a37'
LER_TAG = 'bb-ppm-ler'


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def json_write(path, value):
    temp = path.with_name(path.name + f'.tmp-{os.getpid()}')
    with temp.open('w') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def table_output(frame):
    import nbformat
    return nbformat.v4.new_output('display_data', data={
        'text/html': frame.to_html(index=False, border=0, escape=True),
        'text/plain': frame.to_string(index=False),
    })


def format_ler(row):
    rate = row['ler']
    lower, upper = row['ler_95_exact_two_sided_low'], row['ler_95_exact_two_sided_high']
    return f'{rate:.4g} (+{upper-rate:.3g} / -{rate-lower:.3g})'


def render_outputs(snapshot):
    """Render actual saved data; unfinished cases never receive an LER estimate."""
    import nbformat
    import pandas as pd
    outputs = []
    resources = snapshot['resources']
    if resources['plan_sha256'] != COMPILED_PLAN_SHA:
        raise ValueError('Saved circuit resources do not match the compiled plan.')
    resource_rows = [
        ('sequence', resources['sequence']), ('circuit', '7 measurements + 4 shifts'),
        *[(key, resources[key]) for key in ('physical_qubits', 'ticks', 'detectors', 'observables')],
        ('noise_model', 'Original LightStim circuit_level_with_idling'),
        ('worker_processes', snapshot['workers']), ('threads_per_worker', snapshot['threads_per_worker']),
        ('compiled_plan_sha256', COMPILED_PLAN_SHA), ('circuit_sha256', resources['circuit_sha256']),
        ('campaign_plan_sha256', snapshot['plan_sha256']),
    ]
    outputs.append(table_output(pd.DataFrame(resource_rows, columns=['property', 'value'])))
    outputs.append(nbformat.v4.new_output('display_data', data={'text/html':'<hr>', 'text/plain':'─'*72}))
    outputs.append(table_output(pd.DataFrame([
        {'parameter': key, 'value': str(value)} for key, value in snapshot['parameters'].items()
    ])))
    outputs.append(table_output(pd.DataFrame(snapshot['progress'])[[
        'p', 'shots_requested', 'shots_completed', 'active', 'status'
    ]]))
    outputs.append(nbformat.v4.new_output('stream', name='stdout', text=(
        f"Updated: {snapshot['updated_utc']}\n"
        f"Campaign status: {snapshot['run_state']}; background process active: {snapshot['live']}\n"
        'LER counts any mismatch among the 10 declared LightStim Observables. Nonconvergence is reported separately.\n'
    )))
    rows = sorted(snapshot['results'], key=lambda row: row['p'])
    if not rows:
        outputs.append(nbformat.v4.new_output('stream', name='stdout', text=(
            'No noise point has completed its fixed sample count. Final LER results and the figure appear after completion.\n')))
        return outputs, None
    table = pd.DataFrame([dict(
        p=row['p'], shots=row['shots'], logical_failures=row['logical_failures'],
        **{'logical_error_rate (95% CI)':format_ler(row)},
        converged=row['shots']-row['nonconverged'], nonconverged=row['nonconverged'],
        converged_correct=row['converged_correct'],
        **{'95% one-sided LER upper bound':f"{row['ler_95_one_sided_upper']:.4g}"}
    ) for row in rows])
    outputs.append(table_output(table))
    from matplotlib.figure import Figure
    figure = Figure(figsize=(8, 5.4), layout='constrained')
    ax = figure.subplots()
    rates = [row['ler'] for row in rows]
    ax.errorbar([row['p'] for row in rows], rates,
        yerr=[[row['ler']-row['ler_95_exact_two_sided_low'] for row in rows],
              [row['ler_95_exact_two_sided_high']-row['ler'] for row in rows]],
        fmt='o', markersize=7, color='#0072B2', capsize=5, linestyle='none')
    upper = max(row['ler_95_exact_two_sided_high'] for row in rows)
    ax.set_ylim(-.08*upper, min(1.05, 1.25*upper))
    ax.set_xscale('log')
    ax.set_xlim(6e-5, 1.7e-3)
    ax.set_xticks([1e-4, 1e-3], labels=['0.0001', '0.001'])
    ax.set_xlabel('Physical error probability p')
    ax.set_ylabel('Logical error rate of the full circuit')
    ax.ticklabel_format(axis='y', style='sci', scilimits=(-2, 2))
    ax.set_title('Full-circuit PPM: X2 Z7 X12\nFrozen Relay-BP parameters; exact 95% binomial intervals')
    ax.grid(alpha=.2)
    for row in rows:
        ax.annotate(f"{row['logical_failures']:,}/{row['shots']:,}",
                    (row['p'], row['ler']), xytext=(8, 9), textcoords='offset points')
    buffer = io.BytesIO()
    figure.savefig(buffer, format='png', dpi=160)
    png = buffer.getvalue()
    figure.clear()
    outputs.append(nbformat.v4.new_output('display_data', data={
        'image/png':base64.b64encode(png).decode('ascii'),
        'text/plain':'Full-circuit LER with exact 95% binomial confidence intervals.'
    }, metadata={'image/png':{'width':800, 'height':540}}))
    if any(row['logical_failures'] == 0 for row in rows):
        outputs.append(nbformat.v4.new_output('stream', name='stdout', text=(
            'Zero observed failures are plotted at 0 with a nonzero confidence bound. They do not establish a zero true LER.\n')))
    return outputs, png


def display_campaign(run, *, expected_plan_sha256, decoder_params, shots_by_p,
                     workers, resume=False):
    """Notebook entry: attach, optionally resume, then display a verified snapshot."""
    run = Path(run).expanduser().resolve()
    if expected_plan_sha256 != COMPILED_PLAN_SHA:
        raise ValueError('This saved campaign does not match the Cell 4 compiled plan. Create a separate experiment for another target.')
    snapshot = read_run_snapshot(run)
    canonical = lambda value: json.dumps(value, sort_keys=True)
    if canonical(decoder_params) != canonical(snapshot['parameters']):
        raise ValueError('Notebook decoder parameters differ from the frozen campaign.')
    requested = {row['p']:row['shots_requested'] for row in snapshot['progress']}
    if dict(shots_by_p) != requested or workers != snapshot['workers']:
        raise ValueError('Notebook shot counts or worker count differ from the approved campaign.')
    attach_or_resume(run, resume=resume)
    snapshot = read_run_snapshot(run)
    outputs, _ = render_outputs(snapshot)
    from IPython.display import display
    for output in outputs:
        if output.output_type == 'stream':
            print(output.text, end='')
        else:
            display(output.data, raw=True, metadata=output.get('metadata', {}))
    return snapshot


def notebook_guard(notebook):
    """Stop automatic updates if the user changes the experiment or run entry."""
    return digest(json.dumps([(cell.get('id'), cell.cell_type, cell.source)
        for cell in notebook.cells], ensure_ascii=False).encode())


def save_notebook_outputs(path, outputs, snapshot, expected_guard):
    import nbformat
    original = path.read_bytes()
    notebook = nbformat.reads(original.decode(), as_version=4)
    if notebook_guard(notebook) != expected_guard:
        raise RuntimeError(f'Notebook source changed; automatic output refresh stopped for {path.name}.')
    matches = [cell for cell in notebook.cells if LER_TAG in cell.metadata.get('tags', [])]
    if len(matches) != 1:
        raise ValueError('Expected one tagged LER cell.')
    cell = matches[0]
    cell.outputs = outputs
    cell.execution_count = None
    cell.metadata.pop('execution', None)
    cell.metadata['large_sample_snapshot'] = dict(
        updated_utc=snapshot['updated_utc'], campaign_plan_sha256=snapshot['plan_sha256'],
        source='Verified saved campaign results; no sampling or decoding during output refresh.',
        completed_noise_points=[row['p'] for row in snapshot['results']])
    if notebook.metadata.get('bb_ppm_run', {}).get('mode') != 'frozen_campaign_snapshot':
        if 'bb_ppm_run' in notebook.metadata:
            notebook.metadata['bb_ppm_historical_run'] = notebook.metadata['bb_ppm_run']
        if 'bb_ppm_provenance' in notebook.metadata:
            notebook.metadata['bb_ppm_historical_provenance'] = notebook.metadata['bb_ppm_provenance']
    notebook.metadata['bb_ppm_run'] = dict(
        mode='frozen_campaign_snapshot', state='complete' if len(snapshot['results']) == 2 else snapshot['run_state'],
        updated_utc=snapshot['updated_utc'], worker_processes=snapshot['workers'],
        threads_per_worker=snapshot['threads_per_worker'],
        note='Saved output refresh does not execute sampling or decoding; the frozen background campaign performs the computation.')
    notebook.metadata['bb_ppm_provenance'] = dict(
        kind='frozen_full_circuit_large_sample', campaign_plan_sha256=snapshot['plan_sha256'],
        compiled_plan_sha256=COMPILED_PLAN_SHA, circuit_sha256=snapshot['resources']['circuit_sha256'],
        decoder_parameters=snapshot['parameters'], progress=snapshot['progress'], completed_results=snapshot['results'],
        metric='Any declared Observable mismatch; nonconvergence counted separately.',
        cohort='Fresh fixed-N samples only; historical 50-shot runs are not pooled.',
        noise_model='Original unmodified LightStim circuit_level_with_idling')
    nbformat.validate(notebook)
    temp = path.with_name(path.name + f'.tmp-{os.getpid()}')
    with temp.open('w', encoding='utf-8') as stream:
        nbformat.write(notebook, stream)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != original:
        temp.unlink()
        raise RuntimeError(f'Concurrent edit detected; output refresh stopped for {path.name}.')
    os.replace(temp, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--notebooks', type=Path, nargs='+', required=True)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--refresh-seconds', type=float, default=60)
    args = parser.parse_args()
    if not math.isfinite(args.refresh_seconds) or args.refresh_seconds < 10:
        parser.error('--refresh-seconds must be at least 10')
    import fcntl
    import nbformat
    run = args.run_dir.resolve()
    output = run / 'notebook_integration'
    output.mkdir(exist_ok=True)
    with (output / '.watcher.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        paths = [path.resolve() for path in args.notebooks]
        guards = {str(path):notebook_guard(nbformat.read(path, as_version=4)) for path in paths}
        identity_path = output / 'watcher_identity.json'
        identity = dict(notebook_source_guards=guards, renderer_sha256=digest(Path(__file__).read_bytes()),
                        reader_sha256=digest(Path(__file__).with_name('bb_ppm_large_run.py').read_bytes()),
                        campaign_plan_sha256=EXPECTED_PLAN_SHA256)
        if identity_path.exists():
            if json.loads(identity_path.read_text()) != identity:
                raise RuntimeError('Notebook or renderer changed; review the new version before replacing watcher identity.')
        else:
            json_write(identity_path, identity)
        try:
            while True:
                snapshot = read_run_snapshot(run)
                outputs, png = render_outputs(snapshot)
                for path in paths:
                    save_notebook_outputs(path, outputs, snapshot, guards[str(path)])
                json_write(output / 'snapshot.json', snapshot)
                if png is not None:
                    temp = output / 'ler.png.tmp'
                    temp.write_bytes(png)
                    os.replace(temp, output / 'ler.png')
                complete = len(snapshot['results']) == 2
                json_write(output / 'watcher_status.json', dict(
                    status='complete' if complete else 'watching' if args.watch else 'snapshot_saved',
                    updated_utc=utc(), pid=os.getpid(), completed_noise_points=len(snapshot['results']),
                    notebooks=[path.name for path in paths], no_sampling_or_decoding=True))
                if complete or not args.watch:
                    break
                time.sleep(args.refresh_seconds)
        except Exception as exc:
            json_write(output / 'watcher_status.json', dict(status='failed', updated_utc=utc(), error=str(exc),
                no_sampling_or_decoding=True))
            raise


if __name__ == '__main__':
    main()
