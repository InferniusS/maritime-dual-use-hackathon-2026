"""Transport failures, source identity and independently checked CPU execution."""
import copy
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.edge_remote import (ComputeRequest, ModelDelta, PROTOCOL, _client_models,
    apply_delta, execute_remote_or_local, model_digest, probe_remote, verify_remote_result,
    validate_options)
from backend.edge_worker import app
from backend.models import Obstacle
from backend.scenarios import get_scenario
from backend.validation import analyze
from backend.advanced import run_optimization


@pytest.fixture(scope='module')
def real_worker():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    address = f'http://127.0.0.1:{port}'
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.edge_worker:app', '--host', '127.0.0.1', '--port', str(port)], cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail('Independent worker exited before becoming healthy')
            if probe_remote(address, .2)['available']:
                break
            time.sleep(.05)
        else:
            pytest.fail('Independent worker did not become healthy')
        yield address
    finally:
        process.terminate()
        process.wait(timeout=10)
        _client_models.pop(address, None)


def test_worker_real_http_analysis_optimization_and_delta(real_worker):
    model = get_scenario('inefficient')
    config = dict(enabled=True, endpoint=real_worker, timeout_s=10, source_revision='drawing:17')
    health = probe_remote(real_worker)
    assert health['available'] and health['processor'] == 'CPU'
    assert not health['gpu'] and not health['npu']
    _client_models.pop(real_worker, None)
    result = execute_remote_or_local('analyze', model, {}, config)
    execution = result['execution']
    assert execution['location'] == 'remote', execution
    assert result['result'] == analyze(model)
    assert execution['payload_mode'] == 'full'
    assert execution['request_bytes'] > 0 and execution['response_bytes'] > 0
    assert execution['total_ms'] >= execution['roundtrip_ms']
    assert execution['network_and_overhead_ms'] == max(0., execution['roundtrip_ms'] - execution['remote_compute_ms'])
    # A changed pipe still retains every unchanged surrounding obstacle and pipe.
    changed = model.model_copy(deep=True)
    changed.pipes[0].points[1] = (1., 5., 1.)
    second = execute_remote_or_local('analyze', changed, {}, dict(config, source_revision='drawing:18'))
    assert second['execution']['location'] == 'remote', second['execution']
    assert second['execution']['payload_mode'] == 'delta'
    assert second['result'] == analyze(changed)
    optimized = execute_remote_or_local('optimize', changed, {}, config)
    assert optimized['execution']['location'] == 'remote', optimized['execution']
    assert optimized['result']['candidates'][0]['status'] == 'accepted'
    assert optimized['result']['candidates'][0]['after']['length'] == pytest.approx(9)
    assert optimized['result']['network_after']['length'] < optimized['result']['network_before']['length']
    assert optimized['execution']['payload_mode'] == 'delta'


def test_worker_unknown_digest_retries_full_snapshot_once(real_worker):
    model = get_scenario('efficient')
    # Simulate a worker restart while the laptop retains a known source snapshot.
    _client_models[real_worker] = ('a' * 64, model.model_copy(deep=True))
    response = execute_remote_or_local('analyze', model, {}, dict(enabled=True, endpoint=real_worker))
    assert response['execution']['location'] == 'remote', response['execution']
    assert response['execution']['payload_mode'] == 'delta_then_full'
    assert response['result'] == analyze(model)


def test_real_http_timeout_falls_back(real_worker):
    result = execute_remote_or_local('optimize', get_scenario('congested'), dict(resolution=1.), dict(enabled=True, endpoint=real_worker, timeout_s=.01))
    assert result['execution']['location'] == 'local'
    assert 'timed out' in result['execution']['local_fallback_reason']
    assert result['result']['candidates'][0]['status'] == 'accepted'
    assert result['execution']['remote_cancel_supported'] is False


def test_local_disabled_and_explicit_clearance():
    model = get_scenario('efficient')
    model.obstacles = [Obstacle(id='nearby', min=(2, 1.3, .5), max=(3, 2, 2))]
    result = execute_remote_or_local('analyze', model, dict(clearance=.5), dict(enabled=False))
    assert result['result'] == analyze(model, .5)
    assert result['execution']['location'] == 'local'
    assert result['execution']['local_compute_ms'] >= 0
    assert result['execution']['remote_compute_ms'] is None
    assert result['result']['issues']


def test_unreachable_worker_falls_back_and_health_fails():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        endpoint = f'http://127.0.0.1:{sock.getsockname()[1]}'
    result = execute_remote_or_local('analyze', get_scenario('efficient'), {}, dict(enabled=True, endpoint=endpoint, timeout_s=.1))
    assert result['execution']['location'] == 'local'
    assert 'unreachable' in result['execution']['local_fallback_reason']
    assert probe_remote(endpoint, .1)['available'] is False


@pytest.mark.parametrize('endpoint', ['device:8010', 'ftp://device', 'http://user:secret@device', 'http://device?token=secret'])
def test_endpoint_rejects_unsupported_addresses(endpoint):
    assert not probe_remote(endpoint)['available']
    response = execute_remote_or_local('analyze', get_scenario('efficient'), {}, dict(enabled=True, endpoint=endpoint))
    assert response['execution']['location'] == 'local'


def test_timeout_falls_back_without_claiming_remote_cancellation(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout('slow')
    monkeypatch.setattr(httpx.Client, 'send', timeout)
    result = execute_remote_or_local('analyze', get_scenario('efficient'), {}, dict(enabled=True, endpoint='http://device:8010', timeout_s=.01))
    assert result['execution']['location'] == 'local'
    assert 'may continue' in result['execution']['local_fallback_reason']
    assert result['execution']['remote_cancel_supported'] is False


@pytest.mark.parametrize('bad', ['request', 'revision', 'digest', 'protocol', 'measurement', 'model'])
def test_invalid_or_stale_remote_response_falls_back(monkeypatch, bad):
    model = get_scenario('efficient')
    def post(self, outgoing, **kwargs):
        endpoint = str(outgoing.url)
        request = json.loads(outgoing.content)
        result = analyze(model)
        envelope = dict(protocol=PROTOCOL, request_id=request['request_id'], source_revision=request['source_revision'], source_digest=request['resulting_digest'], architecture='aarch64', result=result, compute_ms=.25)
        if bad == 'request': envelope['request_id'] = 'other-request'
        elif bad == 'revision': envelope['source_revision'] = 'older-drawing'
        elif bad == 'digest': envelope['source_digest'] = '0' * 64
        elif bad == 'protocol': envelope['protocol'] = 'unsupported'
        elif bad == 'measurement': result['metrics'][model.pipes[0].id]['length'] = 999
        elif bad == 'model': envelope['result'] = {'issues': 'invalid', 'metrics': {}}
        return httpx.Response(200, json=envelope, request=httpx.Request('POST', endpoint))
    monkeypatch.setattr(httpx.Client, 'send', post)
    result = execute_remote_or_local('analyze', model, {}, dict(enabled=True, endpoint='http://device:8010', source_revision='drawing:5'))
    assert result['execution']['location'] == 'local'
    assert result['execution']['local_fallback_reason']
    assert result['result'] == analyze(model)


@pytest.mark.parametrize('change', ['endpoint', 'diameter', 'fixed', 'collision', 'slope', 'unselected'])
def test_remote_geometry_cannot_bypass_local_independent_checks(change):
    model = get_scenario('inefficient')
    options = validate_options('optimize', model, {})
    result = run_optimization(model)
    pipe = result['model']['pipes'][0]
    if change == 'endpoint': pipe['points'][-1] = (8, 1, 1)
    elif change == 'diameter': pipe['outer_diameter'] = .5
    elif change == 'fixed': model.pipes[0].editable = False
    elif change == 'collision': pipe['points'] = [(1, 1, 1), (5, 1, 1), (5, 7.9, 1), (10, 7.9, 1)]
    elif change == 'slope':
        model = get_scenario('gravity-detour')
        result = run_optimization(model)
        result['model']['pipes'][0]['points'][1] = (3, 1, 3.5)
    elif change == 'unselected': options['pipe_ids'] = []
    with pytest.raises(ValueError):
        verify_remote_result('optimize', model, result, options)


def test_multi_system_source_preservation_and_final_clashes_checked():
    model = get_scenario('multi-system')
    options = validate_options('optimize', model, dict(try_orders=True, resolution=1.))
    result = run_optimization(model, **options)
    verified = verify_remote_result('optimize', model, result, options)
    assert not verified['issues']
    assert all(c['assurance']['implemented_constraints_valid'] for c in verified['candidates']), [(c['pipe_id'], c['status'], [row['requirement'] for row in c['validation'] if row['status'] == 'FAIL']) for c in verified['candidates']]
    damaged = copy.deepcopy(result)
    damaged['model']['pipes'][0]['points'][0] = (0, 0, 0)
    with pytest.raises(ValueError):
        verify_remote_result('optimize', model, damaged, options)
    # Restore both crossing source lines while leaving the remote accepted claim.
    crossing = copy.deepcopy(result)
    crossing['model'] = model.model_dump()
    with pytest.raises(ValueError, match='independent local route validation'):
        verify_remote_result('optimize', model, crossing, options)


def test_worker_request_validation_and_model_digest():
    client = TestClient(app, client=('127.0.0.1', 50000))
    model = get_scenario('efficient')
    request = dict(request_id='test-1', source_revision='4', operation='analyze', model=model.model_dump(), options={})
    response = client.post('/api/edge/compute', json=request)
    assert response.status_code == 200
    assert response.json()['source_digest'] == model_digest(model)
    assert response.json()['source_revision'] == '4'
    for options in [dict(clearance=-1), dict(fake_option=True), dict(pipe_ids=['unknown'])]:
        invalid = dict(request, options=options)
        assert client.post('/api/edge/compute', json=invalid).status_code == 422
    invalid = dict(request, operation='optimize', options=dict(pipe_ids=['unknown']))
    assert client.post('/api/edge/compute', json=invalid).status_code == 422
    invalid = dict(request, resulting_digest='a' * 64)
    assert client.post('/api/edge/compute', json=invalid).status_code == 422


def test_delta_keeps_complete_context_and_rejects_tampering():
    model = get_scenario('multi-system')
    pipe = model.pipes[0].model_copy(deep=True)
    pipe.points[1] = (2, 2, 1)
    merged = apply_delta(model, ModelDelta(pipes=[pipe]))
    assert merged.pipes[1:] == model.pipes[1:]
    assert merged.obstacles == model.obstacles
    assert model_digest(merged) != model_digest(model)
    with pytest.raises(ValueError): apply_delta(model, ModelDelta(pipes=[pipe, pipe]))
    with pytest.raises(ValueError): apply_delta(model, ModelDelta(remove_pipe_ids=['unknown']))


def test_cancelled_request_does_not_dispatch_to_worker(monkeypatch):
    def fail(*args, **kwargs): pytest.fail('Cancelled request contacted remote worker')
    monkeypatch.setattr(httpx.Client, 'send', fail)
    event = threading.Event(); event.set()
    response = execute_remote_or_local('optimize', get_scenario('efficient'), {}, dict(enabled=True, endpoint='http://device:8010'), event)
    assert response['execution']['location'] == 'local'
    assert 'cancellation' in response['execution']['local_fallback_reason']
    assert response['result']['analysis_performed'] is False
    assert response['result']['assurance']['implemented_constraints_valid'] is False


def test_worker_import_does_not_load_browser_or_cad_subsystems():
    code = "import sys; import backend.edge_worker; assert not any(name in sys.modules for name in ['backend.app', 'backend.exports', 'backend.step_adapter', 'cadquery', 'ezdxf'])"
    result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_worker_restricts_external_clients_and_ignores_spoofed_forwarding(monkeypatch):
    monkeypatch.delenv('PIPEFORGE_EDGE_TOKEN', raising=False)
    monkeypatch.delenv('PIPEFORGE_EDGE_ALLOWLIST', raising=False)
    with TestClient(app, client=('192.0.2.10', 50000)) as client:
        assert client.get('/api/edge/health').status_code == 403
        assert client.get('/api/edge/health', headers={'X-Forwarded-For': '127.0.0.1'}).status_code == 403
    monkeypatch.setenv('PIPEFORGE_EDGE_ALLOWLIST', '192.0.2.0/24')
    with TestClient(app, client=('192.0.2.10', 50000)) as client:
        assert client.get('/api/edge/health').status_code == 200
    with TestClient(app, client=('198.51.100.10', 50000)) as client:
        assert client.get('/api/edge/health', headers={'X-Forwarded-For': '192.0.2.10'}).status_code == 403


def test_worker_token_authentication_and_logs_exclude_credentials(monkeypatch, caplog):
    import logging, secrets
    token = secrets.token_urlsafe(24)
    monkeypatch.setenv('PIPEFORGE_EDGE_TOKEN', token)
    monkeypatch.delenv('PIPEFORGE_EDGE_ALLOWLIST', raising=False)
    model = get_scenario('efficient')
    body = dict(request_id='authenticated-test', source_revision='drawing:1', operation='analyze', model=model.model_dump(), options={})
    with TestClient(app, client=('192.0.2.10', 50000)) as client:
        assert client.post('/api/edge/compute', json=body).status_code == 403
        assert client.get('/api/edge/health', headers={'Authorization': 'Bearer invalid-test-value'}).status_code == 403
        with caplog.at_level(logging.INFO, logger='uvicorn.error'):
            response = client.post('/api/edge/compute', json=body, headers={'Authorization': 'Bearer ' + token})
        assert response.status_code == 200
        assert token not in response.text
        assert token not in caplog.text
        assert 'authenticated-test' in caplog.text


def test_invalid_worker_access_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv('PIPEFORGE_EDGE_ALLOWLIST', 'invalid-network-configuration')
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        assert client.get('/api/edge/health').status_code == 503


def test_worker_rejects_preflight_and_output_budgets_explicitly():
    model = get_scenario('efficient')
    body = dict(request_id='budget-test', source_revision='r' * 256, operation='analyze', model=model.model_dump(), options={'limits': {'max_response_bytes': 1024}})
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        response = client.post('/api/edge/compute', json=body)
    assert response.status_code == 422
    assert response.json()['detail']['engineering_status'] == 'UNRESOLVED'


def test_access_token_is_only_transmitted_in_header(monkeypatch):
    import secrets
    token = secrets.token_urlsafe(24)
    model = get_scenario('efficient')
    def send(self, request, **kwargs):
        assert request.headers['authorization'] == 'Bearer ' + token
        assert token.encode() not in request.content
        outgoing = json.loads(request.content)
        return httpx.Response(200, json=dict(protocol=PROTOCOL, request_id=outgoing['request_id'], source_revision=outgoing['source_revision'], source_digest=outgoing['resulting_digest'], architecture='test-cpu', compute_ms=1., result=analyze(model)), request=request)
    monkeypatch.setattr(httpx.Client, 'send', send)
    response = execute_remote_or_local('analyze', model, {}, dict(enabled=True, endpoint='http://device:8010', access_token=token))
    assert response['execution']['location'] == 'remote'
    assert token not in json.dumps(response)


def test_oversized_remote_response_is_bounded_and_local_fallback_remains(monkeypatch):
    model = get_scenario('efficient')
    class CountingStream(httpx.SyncByteStream):
        def __init__(self): self.reads = 0
        def __iter__(self):
            for _ in range(100):
                self.reads += 1
                yield b'x' * 65536
    stream = CountingStream()
    def send(self, request, **kwargs):
        return httpx.Response(200, stream=stream, request=request)
    monkeypatch.setattr(httpx.Client, 'send', send)
    response = execute_remote_or_local('analyze', model, dict(limits=dict(max_response_bytes=100_000)), dict(enabled=True, endpoint='http://device:8010'))
    assert response['execution']['location'] == 'local'
    assert 'exceeds' in response['execution']['local_fallback_reason']
    assert stream.reads <= 2
    assert response['result'] == analyze(model)


@pytest.fixture
def authenticated_worker():
    import os, secrets
    token = secrets.token_urlsafe(24)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    endpoint = f'http://127.0.0.1:{port}'
    environment = dict(os.environ, PIPEFORGE_EDGE_TOKEN=token)
    environment.pop('PIPEFORGE_EDGE_ALLOWLIST', None)
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.edge_worker:app', '--host', '127.0.0.1', '--port', str(port), '--no-proxy-headers'], cwd=Path(__file__).resolve().parents[1], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if probe_remote(endpoint, .2, token)['available']:
                break
            if process.poll() is not None:
                pytest.fail('Authenticated worker exited before startup')
            time.sleep(.05)
        else:
            pytest.fail('Authenticated worker did not start')
        yield endpoint, token
    finally:
        process.terminate(); process.wait(timeout=10)
        _client_models.pop(endpoint, None)


def test_real_http_authenticated_processing_and_missing_token_fallback(authenticated_worker, monkeypatch):
    endpoint, token = authenticated_worker
    monkeypatch.delenv('PIPEFORGE_REMOTE_TOKEN', raising=False)
    model = get_scenario('efficient')
    assert not probe_remote(endpoint, .2)['available']
    assert probe_remote(endpoint, .2, token)['available']
    config = dict(enabled=True, endpoint=endpoint, timeout_s=5)
    rejected = execute_remote_or_local('analyze', model, {}, config)
    assert rejected['execution']['location'] == 'local'
    assert '403' in rejected['execution']['local_fallback_reason']
    accepted = execute_remote_or_local('analyze', model, {}, dict(config, access_token=token))
    assert accepted['execution']['location'] == 'remote', accepted['execution']
    assert token not in json.dumps(accepted)
    monkeypatch.setenv('PIPEFORGE_REMOTE_TOKEN', token)
    assert probe_remote(endpoint, .2)['available']
    inherited = execute_remote_or_local('analyze', model, {}, config)
    assert inherited['execution']['location'] == 'remote', inherited['execution']
