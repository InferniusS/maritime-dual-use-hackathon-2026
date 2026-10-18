"""Targeted Edge 3.2 browser checks against a started app and CPU worker.

Uses isolated API-created fixtures and an ephemeral authenticated worker. It
does not reset an existing server, approve changes, or mutate fixture sources.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import uuid

import httpx
from playwright.sync_api import sync_playwright


def wait_job(client, job_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = client.get('/api/edge/jobs/' + job_id).json()
        if state['status'] not in ('queued', 'running'):
            assert state['status'] == 'complete', state.get('error', state['status'])
            return state
        time.sleep(.1)
    raise AssertionError('Fixture engineering job did not finish')


def fixture(client, scenario='inefficient', coverage=None, adapter='json', optimize=False):
    model = client.get('/api/scenarios/' + scenario).json()
    body = dict(drawing_id='ui32-' + uuid.uuid4().hex, model=model, source_adapter=adapter)
    if coverage is not None:
        body['extraction_coverage'] = coverage
    response = client.post('/api/edge/sessions', json=body)
    response.raise_for_status()
    snapshot = response.json()
    if optimize:
        response = client.post('/api/edge/sessions/' + snapshot['id'] + '/jobs', json={
            'revision': snapshot['revision'], 'operation': 'optimize', 'resolution': 1,
        })
        response.raise_for_status()
        wait_job(client, response.json()['id'])
        snapshot = client.get('/api/edge/sessions/' + snapshot['id']).json()
        assert snapshot['proposals'], 'Fixture produced no accepted proposal'
    return snapshot


def authenticated_worker():
    # An operating-system allocated port avoids disturbing an existing worker.
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    token = secrets.token_urlsafe(24)
    environment = {**os.environ, 'PIPEFORGE_EDGE_TOKEN': token, 'PIPEFORGE_EDGE_ALLOWLIST': ''}
    process = subprocess.Popen([
        sys.executable, '-m', 'uvicorn', 'backend.edge_worker:app', '--host', '127.0.0.1',
        '--port', str(port), '--workers', '1', '--no-proxy-headers',
    ], cwd=Path(__file__).resolve().parents[1], env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    endpoint = 'http://127.0.0.1:' + str(port)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError('Temporary authenticated worker exited during startup')
        try:
            response = httpx.get(endpoint + '/api/edge/health',
                                 headers={'Authorization': 'Bearer ' + token}, timeout=1)
            if response.status_code == 200:
                return process, endpoint, token
        except httpx.HTTPError:
            pass
        time.sleep(.1)
    process.terminate()
    process.wait(timeout=5)
    raise AssertionError('Temporary authenticated worker did not become ready')


def main():
    base_url = os.environ.get('PIPEFORGE_UI_BASE_URL', 'http://127.0.0.1:8000')
    worker_url = os.environ.get('PIPEFORGE_UI_WORKER_URL', 'http://127.0.0.1:8010')
    artifacts = Path(os.environ.get('PIPEFORGE_UI_ARTIFACTS', '/workspace/pipeforge-artifacts/edge-3.2-ui'))
    artifacts.mkdir(parents=True, exist_ok=True)
    summary = {}
    with httpx.Client(base_url=base_url, timeout=30) as client:
        client.get('/api/edge/health').raise_for_status()
        coverage = dict(adapter='autocad', scope='modelspace', status='incomplete',
                        mapped_entities=2, unmapped_entities=1, source_generation=1,
                        unresolved_entities=[dict(handle='FF', entity_type='LINE', layer='UNCLASSIFIED',
                            reason='Unclassified fixture entity has no engineering interpretation.')],
                        environment='Unclassified fixture geometry is omitted from this explicit test model.')
        incomplete = fixture(client, coverage=coverage, adapter='autocad', optimize=True)
        unknown = fixture(client, optimize=True)
        impossible = fixture(client, 'impossible')
        fixed = fixture(client)
        process, authenticated_url, token = authenticated_worker()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'),
                    headless=True, args=['--no-sandbox', '--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader'])
                page = browser.new_page(viewport={'width': 1750, 'height': 1150})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))

                def attach(snapshot):
                    page.goto(base_url + '/?edge_session=' + snapshot['id'])
                    page.locator('.model-badge').filter(has_text=snapshot['model']['name']).wait_for()

                attach(incomplete)
                page.get_by_role('button', name='Preview proposal in 3D', exact=True).wait_for(timeout=20000)
                coverage_text = page.get_by_label('Edge extraction coverage').inner_text()
                assert all(text in coverage_text for text in ['INCOMPLETE', 'FF', 'UNCLASSIFIED', 'Unclassified fixture entity'])
                page.get_by_label('Edge reviewing engineer').fill('UI test engineer')
                assert page.get_by_role('button', name='Approve proposal', exact=True).is_disabled()
                assert page.get_by_role('button', name='Reject', exact=True).is_enabled()
                assert page.get_by_role('button', name='Preview proposal in 3D', exact=True).is_enabled()
                evidence = page.locator('.edge-proposal .edge-validation-evidence').first.inner_text()
                assert 'cad extraction coverage' in evidence.lower()
                assert all(status in evidence for status in ['PASS', 'FAIL', 'UNVERIFIED', 'NOT_APPLICABLE'])
                assert '1e-8' in evidence, 'Small endpoint tolerance must not be rounded to zero'
                page.screenshot(path=str(artifacts / 'incomplete-coverage-validation.png'), full_page=True)
                summary['incomplete_scope_visible_approval_blocked_preview_and_reject_available'] = True
                summary['four_validation_statuses_and_measurement_precision'] = True

                attach(unknown)
                page.get_by_role('button', name='Preview proposal in 3D', exact=True).wait_for(timeout=20000)
                page.get_by_label('Edge reviewing engineer').fill('UI test engineer')
                assert page.get_by_role('button', name='Approve proposal', exact=True).is_enabled()
                assert 'UNVERIFIED' in page.get_by_label('Edge extraction coverage').inner_text()
                summary['unknown_scope_qualified_but_reviewable'] = True

                page.get_by_role('button', name='Remote worker', exact=True).click()
                page.get_by_label('Edge remote worker endpoint').fill(worker_url)
                with page.expect_response(lambda r: r.url.endswith('/api/edge/remote/health')) as response:
                    page.get_by_role('button', name='Check worker', exact=True).click()
                assert 'access_token' not in response.value.request.post_data_json
                assert response.value.json()['available']
                page.get_by_label('Edge remote worker endpoint').fill(authenticated_url)
                with page.expect_response(lambda r: r.url.endswith('/api/edge/remote/health')) as response:
                    page.get_by_role('button', name='Check worker', exact=True).click()
                assert not response.value.json()['available']
                page.get_by_label('Edge remote worker access token').fill(token)
                with page.expect_response(lambda r: r.url.endswith('/api/edge/remote/health')) as response:
                    page.get_by_role('button', name='Check worker', exact=True).click()
                assert response.value.request.post_data_json.get('access_token') == token
                assert response.value.json()['available']
                with page.expect_response(lambda r: r.url.endswith('/jobs') and r.request.method == 'POST') as response:
                    page.get_by_role('button', name='Analyze revision', exact=True).click()
                assert response.value.request.post_data_json['remote'].get('access_token') == token
                job_id = response.value.json()['id']
                page.get_by_text('Analysis · complete', exact=True).wait_for(timeout=30000)
                state = client.get('/api/edge/jobs/' + job_id).json()
                assert state['execution']['location'] == 'remote', state['execution'].get('local_fallback_reason')
                assert token not in json.dumps(state)
                assert token not in json.dumps(client.get('/api/edge/sessions/' + unknown['id']).json())
                assert token not in page.evaluate('JSON.stringify({local:{...localStorage},session:{...sessionStorage},url:location.href})')
                summary['optional_token_probe_and_authenticated_remote_analysis_without_persistence'] = True

                attach(impossible)
                page.get_by_role('button', name='Propose route', exact=True).wait_for()
                page.get_by_label('Edge grid resolution').fill('1')
                with page.expect_response(lambda r: r.url.endswith('/jobs') and r.request.method == 'POST') as response:
                    page.get_by_role('button', name='Propose route', exact=True).click()
                job_id = response.value.json()['id']
                page.get_by_label('Edge unsuccessful routing evidence').wait_for(timeout=30000)
                state = client.get('/api/edge/jobs/' + job_id).json()
                reason = state['result']['candidates'][0]['reason']
                assert reason in page.get_by_label('Edge unsuccessful routing evidence').inner_text()
                assert page.locator('.edge-unsuccessful-search .edge-check-status.fail').count() > 0
                assert page.get_by_role('button', name='Preview proposal in 3D', exact=True).count() == 0
                page.screenshot(path=str(artifacts / 'no-route-measured-failure.png'), full_page=True)
                summary['no_route_reason_and_failed_measurements_without_proposal'] = True

                attach(fixed)
                page.get_by_role('button', name='Propose route', exact=True).wait_for()
                fixed_id = next(pipe['id'] for pipe in fixed['model']['pipes'] if not pipe['editable'])
                page.locator('.pipe-row').filter(has_text=fixed_id).click()
                page.get_by_label('Edge selected pipe only').check()
                page.get_by_role('button', name='Propose route', exact=True).click()
                page.get_by_label('Edge unsuccessful routing evidence').wait_for(timeout=30000)
                text = page.get_by_label('Edge unsuccessful routing evidence').inner_text()
                assert 'Pipe is fixed' in text
                assert 'no independent validation rows' in text
                summary['fixed_pipe_rejection_without_fabricated_checks'] = True
                for snapshot in [incomplete, unknown, impossible, fixed]:
                    current = client.get('/api/edge/sessions/' + snapshot['id']).json()
                    assert current['revision'] == snapshot['revision'] and current['model'] == snapshot['model']
                summary['fixture_sources_unchanged'] = True
                assert not errors, errors
                summary['javascript_errors'] = errors
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            (artifacts / 'targeted-checks.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
