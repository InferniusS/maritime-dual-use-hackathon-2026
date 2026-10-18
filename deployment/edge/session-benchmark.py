"""Measure session analysis reuse and full versus delta HTTP transfer with equal outputs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.edge_remote import execute_remote_or_local, probe_remote, _client_models
from backend.edge_store import SessionStore
from backend.models import Bounds, Model, Pipe
from backend.validation import analyze


def timed(callback):
    started = time.perf_counter()
    result = callback()
    return result, (time.perf_counter() - started) * 1000


def fixture(count):
    # Six-metre spacing keeps all old/new route AABBs and fitting envelopes
    # disjoint at the session dependency detector's maximum one-metre clearance.
    pipes = []
    for index in range(count):
        y = 3. + 6 * index
        pipes.append(Pipe(id=f'P-{index:03}', system_id=f'water-{index:03}', editable=True,
                          points=[(1., y, 1.5), (1., y + 1., 1.5),
                                  (19., y + 1., 1.5), (19., y, 1.5)]))
    return Model(id='disjoint-session-benchmark', name=f'{count} disjoint pressure routes',
                 bounds=Bounds(min=(0., 0., 0.), max=(20., 6. * count + 6., 4.)), pipes=pipes)


def engineering(result):
    # SQLite and HTTP store points as JSON arrays; compare their JSON meaning.
    return json.loads(json.dumps({key: result[key] for key in ('issues', 'metrics')}, allow_nan=False))


def standalone_worker():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    endpoint = f'http://127.0.0.1:{port}'
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.edge_worker:app',
                                '--host', '127.0.0.1', '--port', str(port)],
                               cwd=Path(__file__).resolve().parents[2],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Independent HTTP worker exited before startup')
        if probe_remote(endpoint, .2)['available']:
            return process, endpoint
        time.sleep(.05)
    process.terminate()
    process.wait(timeout=10)
    raise RuntimeError('Independent HTTP worker did not become healthy')


def run(args, endpoint):
    health = probe_remote(endpoint)
    if not health['available']:
        raise RuntimeError('CPU worker is unavailable: ' + health.get('error', 'unknown'))
    source = fixture(args.pipes)
    target_id = source.pipes[args.pipes // 2].id
    rows = []
    with tempfile.TemporaryDirectory(prefix='pipeforge-session-benchmark-') as directory:
        for index in range(args.runs):
            store = SessionStore(Path(directory) / f'session-{index}.sqlite3')
            try:
                state = store.create('benchmark-drawing', source, source_adapter='procedural')
                capture = store.capture(state['id'])
                full, full_ms = timed(lambda: analyze(Model.model_validate(capture['model']), .1))
                full = engineering(full)
                (cold, cache), cold_ms = timed(lambda: store.incremental_analysis(capture, .1))
                assert engineering(cold) == full, 'Cold session output differs from full analysis'
                assert cold['cache']['recomputed'] == args.pipes and cold['cache']['reused'] == 0
                store.publish_analysis(state['id'], state['revision'], cold, cache)
                warm_capture = store.capture(state['id'])
                (warm, _), warm_ms = timed(lambda: store.incremental_analysis(warm_capture, .1))
                assert engineering(warm) == full, 'Warm session output differs from full analysis'
                assert warm['cache']['recomputed'] == 0 and warm['cache']['reused'] == args.pipes
                changed_pipe = next(pipe for pipe in source.pipes if pipe.id == target_id).model_copy(deep=True)
                changed_pipe.points[1] = (changed_pipe.points[1][0], changed_pipe.points[1][1] + .5, 1.5)
                changed_pipe.points[2] = (changed_pipe.points[2][0], changed_pipe.points[2][1] + .5, 1.5)
                patched, patch_ms = timed(lambda: store.patch(state['id'], state['revision'], upsert_pipes=[changed_pipe]))
                edited_capture = store.capture(state['id'])
                assert patched['affected_pipe_ids'] == [target_id]
                edited_full, edited_full_ms = timed(lambda: analyze(Model.model_validate(edited_capture['model']), .1))
                edited_full = engineering(edited_full)
                (edited, _), edited_ms = timed(lambda: store.incremental_analysis(edited_capture, .1))
                assert engineering(edited) == edited_full, 'Localized edit output differs from full analysis'
                assert edited['cache']['recomputed_pipe_ids'] == [target_id]
                assert edited['cache']['reused'] == args.pipes - 1
                _client_models.pop(endpoint.rstrip('/'), None)
                remote_full = execute_remote_or_local('analyze', source, {},
                    dict(enabled=True, endpoint=endpoint, timeout_s=60, source_revision='1'))
                assert remote_full['execution']['location'] == 'remote', remote_full['execution']
                assert remote_full['execution']['payload_mode'] == 'full'
                assert engineering(remote_full['result']) == full
                edited_model = Model.model_validate(edited_capture['model'])
                remote_delta = execute_remote_or_local('analyze', edited_model, {},
                    dict(enabled=True, endpoint=endpoint, timeout_s=60, source_revision='2'))
                assert remote_delta['execution']['location'] == 'remote', remote_delta['execution']
                assert remote_delta['execution']['payload_mode'] == 'delta'
                assert engineering(remote_delta['result']) == edited_full
                assert remote_delta['execution']['request_bytes'] < remote_full['execution']['request_bytes']
                rows.append(dict(full_analysis_ms=full_ms, cold_session_ms=cold_ms, warm_session_ms=warm_ms,
                                 localized_patch_ms=patch_ms, edited_full_analysis_ms=edited_full_ms,
                                 edited_incremental_ms=edited_ms, cold_cache=cold['cache'], warm_cache=warm['cache'],
                                 edit_cache=edited['cache'], issue_count_before=len(full['issues']),
                                 issue_count_after=len(edited_full['issues']), remote_full=remote_full['execution'],
                                 remote_delta=remote_delta['execution']))
            finally:
                store.db.close()
    local_keys = ['full_analysis_ms', 'cold_session_ms', 'warm_session_ms', 'localized_patch_ms',
                  'edited_full_analysis_ms', 'edited_incremental_ms']
    medians = {key: statistics.median(row[key] for row in rows) for key in local_keys}
    for mode in ['remote_full', 'remote_delta']:
        medians[mode] = {key: statistics.median(row[mode][key] for row in rows)
                         for key in ['request_bytes', 'response_bytes', 'serialization_ms', 'roundtrip_ms',
                                     'remote_compute_ms', 'decode_ms', 'local_validation_ms', 'total_ms']}
    return dict(recorded_utc=datetime.now(timezone.utc).isoformat(),
                scope='Procedural disjoint routes, full geometry retained. Session engine-call timings exclude SQLite capture/publish; patch timing is separate. Independent HTTP worker; no AutoCAD or IQ-9075 execution.',
                client=dict(architecture=platform.machine(), system=platform.system(), python=platform.python_version()),
                worker=health, independently_spawned_worker=args.endpoint is None,
                fixture=dict(pipe_count=args.pipes, segment_count=args.pipes * 3, obstacles=0,
                             spacing_m=6, clearance_m=.1, changed_pipe_id=target_id,
                             localized_edit='Raise the middle straight run by 0.5 m in Y; source endpoints retained'),
                runs=args.runs, medians=medians, samples=rows,
                assertions=['Cold session engineering outputs equal full analysis',
                            'Warm session engineering outputs equal full analysis; all pipes reused',
                            'Localized edit engineering outputs equal fresh full analysis; exactly one pipe recomputed',
                            'Actual HTTP full-source analysis equals local full analysis',
                            'Actual HTTP one-edit delta analysis equals local edited full analysis',
                            'Delta request transmits fewer bytes than the full request'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', help='Existing worker address; omit to start an independent loopback worker')
    parser.add_argument('--pipes', type=int, default=40)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 30 <= args.pipes <= 100:
        parser.error('--pipes must be between 30 and 100')
    if not 1 <= args.runs <= 10:
        parser.error('--runs must be between 1 and 10')
    process = None
    try:
        if args.endpoint:
            endpoint = args.endpoint
        else:
            process, endpoint = standalone_worker()
        result = run(args, endpoint)
        payload = json.dumps(result, indent=2) + '\n'
        if args.output:
            args.output.write_text(payload, encoding='utf-8')
        print(payload)
    finally:
        if process is not None:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()
