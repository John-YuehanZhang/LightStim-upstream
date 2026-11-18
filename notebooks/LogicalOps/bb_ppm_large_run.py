"""Attach to the frozen BB PPM campaign without sampling or decoding in Python.

``read_run_snapshot`` is read-only. ``attach_or_resume`` is read-only by default;
only an explicit ``resume=True`` can start the already frozen external driver.
No Stim, Relay, LightStim, or sampler module is imported here. Completed results
are taken from the driver's published root summary after independent metadata,
saved-row/count, parameter, and input-hash checks. Live partial rows never become
LER estimates. The original two-point campaign and the additional midpoint
campaign have separate immutable plans and independent samples.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

TUNING_ROOT = Path('/home/yuehan/bb_ppm_results/full_circuit_tuning')
EXPECTED_RUN = TUNING_ROOT / 'large_sample_runs/20261117_frozen_192'
EXPECTED_PLAN_SHA256 = '755d91b0d8e3b41235e0f88139be5037ab3c1de263e629d7c83a011e13377515'
EXPECTED_MIDPOINT_RUN = TUNING_ROOT / 'large_sample_runs/20261118_p0005_192'
EXPECTED_MIDPOINT_PLAN_SHA256 = 'b455e9d1810fa6251d02aacd90edeafd47e7124e990b609e7faa006838cc04f1'
EXPECTED_SELECTION_SHA256 = '508392498c37d4a018422c9e0ca57824503a59118cd21f8748c792541977b654'
PYTHON = Path('/home/yuehan/bb_code/venv/bin/python')
RESOURCE_PATH = Path('/home/yuehan/bb_ppm_results/ler/57d014f2cc436091/resources.json')
INPUT_FILES = ('check_matrix.npz', 'observable_matrix.npz', 'error_priors.npy', 'samples.npz')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _fingerprint(value):
    text = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _verified_plan(run):
    run = Path(run).expanduser().resolve()
    expected_sha = expected_plan_sha(run)
    _require(_sha(run / 'plan.json') == expected_sha, 'Frozen campaign plan changed')
    plan = _read(run / 'plan.json')
    _require(plan['workers'] == 192 and plan['threads_per_worker'] == 1, 'Unexpected worker policy')
    for relative, expected in plan['frozen_sources'].items():
        path = (TUNING_ROOT / relative).resolve()
        _require(TUNING_ROOT.resolve() in path.parents, 'Frozen source escapes the campaign root')
        _require(_sha(path) == expected, f'Frozen source changed: {relative}')
    _require(plan['frozen_sources']['original_lightstim/selection.json'] == EXPECTED_SELECTION_SHA256,
             'Unexpected selection identity')
    selection = _read(TUNING_ROOT / 'original_lightstim/selection.json')
    _require(selection['status'] == 'frozen' and selection['validation_results_used_for_selection'] is False,
             'Selection was not frozen before validation')
    _require(selection['selected_parameters'] == plan['selected_parameters'], 'Parameter identities disagree')
    for label, p, total in plan['cases']:
        path = run / label / 'campaign.json'
        expected = plan['prepared_inputs'][label]
        _require(_sha(path) == expected['campaign_sha256'], f'Prepared campaign changed: {label}')
        campaign = _read(path)
        _require(_fingerprint(campaign['plan']) == campaign['plan_sha256'] == expected['plan_sha256'],
                 'Prepared campaign plan fingerprint changed')
        _require(campaign['plan']['p'] == p and campaign['plan']['total_shots'] == total,
                 'Prepared p or fixed sample count changed')
        _require(campaign['plan']['selection_sha256'] == EXPECTED_SELECTION_SHA256,
                 'Prepared selection changed')
    return run, plan, selection


def expected_plan_sha(run):
    run = Path(run).expanduser().resolve()
    if run == EXPECTED_RUN.resolve():
        return EXPECTED_PLAN_SHA256
    _require(run == EXPECTED_MIDPOINT_RUN.resolve(), 'Unsupported frozen campaign directory')
    _require(EXPECTED_MIDPOINT_PLAN_SHA256 is not None, 'Midpoint plan has not been frozen')
    return EXPECTED_MIDPOINT_PLAN_SHA256


def _locked(path):
    """Probe an existing lock without creating/truncating it; never trust PIDs."""
    try:
        stream = Path(path).open('rb')
    except FileNotFoundError:
        return False
    with stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return False


def _locks(run):
    return {'orchestrator': _locked(run / '.orchestrator.lock'),
            'executor': _locked(TUNING_ROOT / 'decoder/.executor64.lock')}


def _resources():
    original = _read(RESOURCE_PATH)
    result = {key: original[key] for key in
              ('sequence', 'physical_qubits', 'ticks', 'detectors', 'observables',
               'p0_silent', 'plan_sha256', 'circuit_sha256') if key in original}
    result.update(source='Saved original clean-circuit resource report; not current runtime or worker settings.',
                  source_sha256=_sha(RESOURCE_PATH))
    return result


def _verify_intervals(row, failures, total):
    from scipy.stats import beta
    expected = {
        'ler_95_exact_two_sided_low': 0. if failures == 0 else float(beta.ppf(.025, failures, total-failures+1)),
        'ler_95_exact_two_sided_high': 1. if failures == total else float(beta.ppf(.975, failures+1, total-failures)),
        'ler_95_one_sided_upper': 1. if failures == total else float(beta.ppf(.95, failures+1, total-failures)),
    }
    for key, value in expected.items():
        actual = row[key]
        _require(type(actual) in (int, float) and math.isfinite(actual) and
                 0 <= actual <= 1 and math.isclose(actual, value, rel_tol=1e-12, abs_tol=1e-15),
                 f'Published exact binomial interval disagrees: {key}')


def _validate_completed_row(run, plan, row):
    matches = [(label, p, n) for label, p, n in plan['cases'] if p == row['p']]
    _require(len(matches) == 1, 'Published result has an unexpected noise point')
    label, p, total = matches[0]
    directory = run / label / 'decoding'
    source = run / label / 'merged'
    manifest_path = directory / 'manifest.json'
    _require(_sha(manifest_path) == row['decoder_manifest_sha256'], 'Published decoder manifest changed')
    manifest = _read(manifest_path)
    _require(manifest['status'] == 'complete' and
             manifest['completed_tasks'] == manifest['total_tasks'] == total,
             'A published case has not completed its fixed sample count')
    _require(manifest['source_sample_batch_size'] == row['shots'] == total and
             manifest['source_split'] == 'large_sample_validation', 'Wrong cohort or sample count')
    _require(Path(manifest['source_run']).resolve() == source.resolve(), 'Wrong decoder source directory')
    _require(manifest['decoder_parameters_by_candidate'] == {'selected': plan['selected_parameters']},
             'Published decoder parameters differ from frozen selection')
    expected_executor = plan['frozen_sources']['decoder/search_executor_large.py']
    _require(all(a['workers'] == 192 and a['rayon_threads_per_worker'] == 1 and
                 a['executor_sha256'] == expected_executor for a in manifest['attempts']),
             'Unexpected worker configuration or executor version')
    _require(_sha(source / 'manifest.json') == manifest['source_manifest_sha256'] == row['source_manifest_sha256'],
             'Published source manifest changed')
    merged = _read(source / 'manifest.json')
    _require(merged['p'] == p and merged['sample_batch_size'] == total and
             merged['selection_sha256'] == EXPECTED_SELECTION_SHA256, 'Wrong merged source identity')
    identity = manifest['config_identity']
    _require(_fingerprint(identity) == manifest['config_fingerprint'] and
             identity['shot_indices'] == list(range(total)), 'Wrong decoder task identity or coverage')
    for name in INPUT_FILES:
        _require(_sha(source / name) == merged['source_hashes'][name] == identity['source_inputs'][name],
                 f'Completed input changed: {name}')
    values = _read(directory / 'candidates/selected/summary.json')
    rows = _read(directory / 'candidates/selected/shots.json')
    _require(values == manifest['summaries']['selected'], 'Saved decoder summaries disagree')
    _require(values['complete'] is True and values['shots_decoded'] == len(rows) == total,
             'Saved rows are incomplete')
    _require(sorted(r['shot_index'] for r in rows) == list(range(total)), 'Saved rows do not cover the cohort exactly')
    _require(values['all_returned_Hc_Oc_and_convergence_independently_verified'] is True,
             'Decoder did not record Hc/Oc verification')
    for r in rows:
        _require(r['candidate'] == 'selected' and type(r['logical_failure']) is bool and
                 type(r['converged']) is bool, 'Malformed saved result row')
        _require(r['logical_failure'] == (r['wrong_observable_count'] > 0) and
                 r['converged'] == r['physical_success'] == (r['syndrome_residual_weight'] == 0),
                 'Saved logical/convergence flags disagree')
    counts = {
        'logical_failures': sum(r['logical_failure'] for r in rows),
        'nonconverged': sum(not r['converged'] for r in rows),
        'converged_correct': sum(r['converged'] and not r['logical_failure'] for r in rows),
        'converged_logical_failure': sum(r['converged'] and r['logical_failure'] for r in rows),
        'unconverged_correct_observables': sum(not r['converged'] and not r['logical_failure'] for r in rows),
        'unconverged_logical_failure': sum(not r['converged'] and r['logical_failure'] for r in rows),
    }
    for key, count in counts.items():
        _require(row[key] == values[key] == count, f'Published {key} disagrees with saved rows')
    _require(row['ler'] == counts['logical_failures'] / total and
             row['nonconvergence_rate'] == counts['nonconverged'] / total, 'Published rates disagree')
    _verify_intervals(row, counts['logical_failures'], total)
    cpu = math.fsum(r['decode_cpu_seconds'] for r in rows)
    _require(math.isfinite(cpu) and cpu >= 0 and
             math.isclose(cpu, row['sum_decode_cpu_seconds'], rel_tol=1e-12, abs_tol=1e-8) and
             math.isclose(cpu, values['sum_decode_cpu_seconds'], rel_tol=1e-12, abs_tol=1e-8),
             'Published CPU time disagrees')


def _read_single_run_snapshot(run: Path) -> dict:
    """Return validated completed results and completion-only live progress.

    Keys: selection (full frozen document), parameters (selected settings),
    workers, threads_per_worker, progress (p/requested/completed/active/status),
    results (unchanged completed root rows), resources (historical clean-circuit
    fields), updated_utc (snapshot time), plan_sha256, run_state, live (bool),
    locks ({orchestrator, executor}). No result is inferred from partial rows.
    """
    run, plan, selection = _verified_plan(run)
    plan_sha = expected_plan_sha(run)
    status_path = run / 'status.json'
    state = _read(status_path) if status_path.exists() else {}
    if state.get('plan_sha256') is not None:
        _require(state['plan_sha256'] == plan_sha, 'Runtime state belongs to a different plan')
    locks = _locks(run)
    results = []
    summary_path = run / 'summary.json'
    if summary_path.exists():
        summary = _read(summary_path)
        _require(summary['plan_sha256'] == plan_sha, 'Published summary belongs to another plan')
        results = summary['rows']
        _require(len(results) <= len(plan['cases']) and
                 len({r['p'] for r in results}) == len(results), 'Duplicate or excess published cases')
        for row in results:
            _validate_completed_row(run, plan, row)
        if summary.get('status') == 'complete':
            _require(len(results) == len(plan['cases']), 'Completed summary is missing a noise point')
    if state.get('status') == 'complete':
        _require(len(results) == len(plan['cases']), 'Completed campaign is missing its full results')
    progress = []
    for label, p, total in plan['cases']:
        directory = run / label / 'decoding'
        manifest_path = directory / 'manifest.json'
        manifest = _read(manifest_path) if manifest_path.exists() else {}
        progress_path = directory / 'progress.json'
        current = _read(progress_path) if progress_path.exists() else {}
        complete = manifest.get('status') == 'complete'
        done = manifest.get('completed_tasks', 0) if complete else current.get('completed', 0)
        active = 0 if complete else current.get('active', 0)
        _require(type(done) is int and 0 <= done <= total and
                 type(active) is int and 0 <= active <= 192, 'Invalid progress counters')
        if current:
            _require(current['total'] == total, 'Progress total differs from fixed N')
        stage = manifest.get('status', 'pending')
        if not complete and not any(locks.values()) and stage == 'decoding':
            stage = 'interrupted'
            active = 0
        elif current.get('runtime_errors'):
            stage = 'runtime_errors'
        progress.append(dict(p=p, shots_requested=total, shots_completed=done,
                             active=active, status=stage))
    return dict(selection=selection, parameters=plan['selected_parameters'],
                workers=plan['workers'], threads_per_worker=plan['threads_per_worker'],
                progress=progress, results=results, resources=_resources(),
                updated_utc=datetime.now(timezone.utc).isoformat(), plan_sha256=plan_sha,
                run_state=state.get('status', 'unknown'), live=any(locks.values()), locks=locks)


def read_run_snapshot(run: Path, *, midpoint_run=None) -> dict:
    """Validate each fixed-N cohort separately, then combine completed noise points."""
    snapshot = _read_single_run_snapshot(run)
    snapshot['campaign_plan_sha256s'] = [snapshot['plan_sha256']]
    if midpoint_run is None:
        return snapshot
    _require(Path(run).resolve() == EXPECTED_RUN.resolve(), 'The base must be the original campaign')
    _require(Path(midpoint_run).resolve() == EXPECTED_MIDPOINT_RUN.resolve(), 'Unexpected midpoint campaign')
    extra = _read_single_run_snapshot(midpoint_run)
    for key in ('parameters', 'workers', 'threads_per_worker', 'resources'):
        _require(snapshot[key] == extra[key], f'Campaigns disagree on {key}')
    _require({r['p']: r['shots_requested'] for r in extra['progress']} == {0.0005: 10000},
             'Midpoint budget differs from the approved 10000 shots')
    old_points = {r['p'] for r in snapshot['progress']}
    _require(not old_points.intersection(r['p'] for r in extra['progress']), 'Overlapping noise-point cohorts')
    snapshot['progress'] = sorted(snapshot['progress'] + extra['progress'], key=lambda r: r['p'])
    snapshot['results'] = sorted(snapshot['results'] + extra['results'], key=lambda r: r['p'])
    snapshot['campaign_plan_sha256s'].append(extra['plan_sha256'])
    snapshot['locks'] = {'original': snapshot['locks'], 'midpoint': extra['locks']}
    snapshot['live'] = snapshot['live'] or extra['live']
    snapshot['run_state'] = ('complete' if len(snapshot['results']) == len(snapshot['progress'])
                             else 'incomplete' if extra['run_state'] == 'complete'
                             else extra['run_state'])
    snapshot['updated_utc'] = extra['updated_utc']
    return snapshot


def attach_or_resume(run: Path, *, resume: bool = False) -> str:
    """Attach by default; explicitly resume only when both production locks are free.

    Returns one of complete, attached, executor_active, idle, resume_started,
    or resume_raced. The original driver owns the authoritative lock; a competing
    launch loses that lock without changing runtime state. No sampler is invoked.
    """
    run, plan, _ = _verified_plan(run)
    locks = _locks(run)
    if locks['orchestrator']:
        return 'attached'
    if locks['executor']:
        return 'executor_active'
    status = _read(run / 'status.json') if (run / 'status.json').exists() else {}
    if status.get('status') == 'complete':
        return 'complete'
    if not resume:
        return 'idle'
    _require(type(resume) is bool, 'resume must be an explicit boolean')
    # Log outside immutable plan/source/result folders. The original driver's
    # own nonblocking flock closes the race between this probe and its startup.
    logs = TUNING_ROOT / 'notebook_integration'
    logs.mkdir(exist_ok=True)
    if run == EXPECTED_MIDPOINT_RUN.resolve():
        command = [str(PYTHON), str(TUNING_ROOT / 'large_sample_tools/run_p0005_campaign.py'),
                   '--preparer-sha256', plan['frozen_sources']['large_sample_tools/prepare_midpoint_samples.py']]
    else:
        command = [str(PYTHON), str(TUNING_ROOT / 'large_sample_tools/run_large_campaign.py'),
                   '--merge-sha256', plan['frozen_sources']['large_sample_tools/merge_large_samples.py']]
    env = dict(os.environ)
    for key in ('RAYON_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
        env[key] = '1'
    with (logs / 'resume.log').open('ab') as log:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True, close_fds=True,
                                 cwd=str(TUNING_ROOT), env=env)
    code = child.poll()
    if code is None:
        return 'resume_started'
    if _locked(run / '.orchestrator.lock'):
        return 'resume_raced'
    raise RuntimeError(f'Frozen driver exited {code}; see notebook_integration/resume.log')
