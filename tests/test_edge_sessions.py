"""Independent integration checks for revisioned engineering change sessions.

The source model is the authoritative drawing snapshot. Analysis and approval
must never silently replace it with a candidate route.
"""
import copy
import json
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.edge_api import create_router
from backend.edge_store import SessionStore
from backend.models import Model
from backend.scenarios import get_scenario
from backend.validation import analyze


def client_for(path):
    application = FastAPI()
    application.include_router(create_router(SessionStore(path)))
    return TestClient(application)


@pytest.fixture
def edge(tmp_path):
    path = tmp_path / 'edge.sqlite3'
    with client_for(path) as client:
        yield client, path


def create_session(client, scenario='inefficient', object_map=None):
    model = get_scenario(scenario).model_dump(mode='json')
    mapping = object_map or {'P-101': ['A1', 'A2'], 'P-202': ['B1']}
    response = client.post('/api/edge/sessions', json={
        'drawing_id': 'engine-room.dwg', 'model': model,
        'object_map': mapping, 'source_adapter': 'json',
    })
    assert response.status_code == 200, response.text
    return response.json(), model


def snapshot(client, session):
    response = client.get('/api/edge/sessions/' + session['id'])
    assert response.status_code == 200, response.text
    return response.json()


def submit(client, session, operation='analyze', **options):
    response = client.post('/api/edge/sessions/' + session['id'] + '/jobs', json={
        'revision': session['revision'], 'operation': operation, **options,
    })
    assert response.status_code == 200, response.text
    return response.json()['id']


def wait_job(client, job_id, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get('/api/edge/jobs/' + job_id)
        assert response.status_code == 200, response.text
        state = response.json()
        if state['status'] not in {'queued', 'running'}:
            return state
        time.sleep(.01)
    pytest.fail('Edge job did not reach a terminal state')


def optimized_proposal(client, session):
    state = wait_job(client, submit(client, session, 'optimize', resolution=1))
    assert state['status'] == 'complete', state
    current = snapshot(client, session)
    assert current['proposals'], current
    return current['proposals'][0]


def decide(client, session, proposal, decision='approve', **options):
    return client.post('/api/edge/sessions/' + session['id'] + '/proposals/' + proposal['id'] + '/decision', json={
        'revision': session['revision'], 'decision': decision,
        'engineer': 'Test engineer', **options,
    })


def export(client, session, proposal, format='json'):
    return client.get('/api/edge/sessions/' + session['id'] + '/proposals/' + proposal['id'] + '/export/' + format)


def test_create_preserves_source_coordinates_identity_and_cad_handles(edge):
    client, _ = edge
    session, original = create_session(client)
    assert session['drawing_id'] == 'engine-room.dwg'
    assert session['model'] == original
    assert session['object_map'] == {'P-101': ['A1', 'A2'], 'P-202': ['B1']}
    assert snapshot(client, session)['model'] == original
    assert snapshot(client, session)['revision'] == session['revision']
    assert session['audit']


def test_lightweight_status_tracks_external_revisions_analysis_and_decisions(edge):
    client, _ = edge
    session, model = create_session(client)
    path = '/api/edge/sessions/' + session['id']
    initial = client.get(path + '/status').json()
    assert initial['revision'] == 1 and initial['analysis_revision'] is None
    assert initial['last_event_seq'] == session['audit'][-1]['seq']
    assert not {'model', 'proposals', 'object_map', 'audit'} & initial.keys()
    assert len(json.dumps(initial)) < len(json.dumps(session)) / 5
    state = wait_job(client, submit(client, session))
    assert state['status'] == 'complete'
    analyzed = client.get(path + '/status').json()
    assert analyzed['analysis_revision'] == 1
    assert analyzed['last_event_seq'] > initial['last_event_seq']
    proposal = optimized_proposal(client, session)
    generated = client.get(path + '/status').json()
    assert decide(client, session, proposal).status_code == 200
    approved = client.get(path + '/status').json()
    assert approved['revision'] == generated['revision'] == 1
    assert approved['last_event_seq'] > generated['last_event_seq']
    pipe = copy.deepcopy(model['pipes'][0]); pipe['points'][1][1] += .25
    assert client.patch(path, json={'base_revision': 1, 'upsert_pipes': [pipe]}).status_code == 200
    edited = client.get(path + '/status').json()
    assert edited['revision'] == 2 and edited['analysis_revision'] is None
    assert edited['model_digest'] != approved['model_digest']
    assert edited['last_event_seq'] > approved['last_event_seq']
    assert client.get('/api/edge/sessions/missing/status').status_code == 404


def test_source_units_persist_through_reviewed_coordinate_export(edge):
    client, _ = edge
    model = get_scenario('inefficient').model_dump(mode='json')
    response = client.post('/api/edge/sessions', json={
        'drawing_id': 'millimeter-native.dwg', 'model': model, 'source_adapter': 'autocad',
        'coordinate_transform': {'source_units': 'mm', 'meters_per_unit': .001},
        'object_map': {'P-101': ['1A']},
    })
    assert response.status_code == 200, response.text
    session = response.json(); proposal = optimized_proposal(client, session)
    assert decide(client, session, proposal).status_code == 200
    package = export(client, session, proposal).json()
    assert package['edge_approval']['coordinate_transform'] == session['coordinate_transform']
    assert package['object_map']['P-101'] == ['1A']
    instructions = next(p for p in package['source_coordinate_instructions'] if p['pipe_id'] == 'P-101')
    assert instructions['source_units'] == 'mm'
    assert instructions['original'] == [[v * 1000 for v in point] for point in model['pipes'][0]['points']]
    assert instructions['proposed'] == [[v * 1000 for v in point] for point in proposal['proposed_geometry']['P-101']]
    assert snapshot(client, session)['model'] == model


def test_invalid_patch_is_atomic_and_does_not_advance_audit_or_revision(edge):
    client, _ = edge
    session, original = create_session(client)
    valid = copy.deepcopy(original['pipes'][0])
    valid['points'][1][0] += .1
    invalid = copy.deepcopy(original['pipes'][1])
    invalid['outer_diameter'] = -1
    response = client.patch('/api/edge/sessions/' + session['id'], json={
        'base_revision': session['revision'], 'upsert_pipes': [valid, invalid],
        'remove_obstacle_ids': ['Pump skid'],
    })
    assert response.status_code == 422, response.text
    current = snapshot(client, session)
    assert current['revision'] == session['revision']
    assert current['model'] == original
    assert current['audit'] == session['audit']


def test_patch_is_revision_checked_and_retains_unmentioned_handles(edge):
    client, _ = edge
    session, original = create_session(client)
    pipe = copy.deepcopy(original['pipes'][0])
    pipe['points'][1][1] = 5.5
    response = client.patch('/api/edge/sessions/' + session['id'], json={
        'base_revision': session['revision'], 'upsert_pipes': [pipe],
    })
    assert response.status_code == 200, response.text
    current = snapshot(client, session)
    assert current['revision'] != session['revision']
    assert current['object_map'] == session['object_map']
    assert current['model']['pipes'][0] == pipe
    assert current['model']['pipes'][1] == original['pipes'][1]
    assert current['model']['obstacles'] == original['obstacles']
    for endpoint, body in [
        ('', {'base_revision': session['revision'], 'upsert_pipes': [pipe]}),
        ('/jobs', {'revision': session['revision'], 'operation': 'analyze'}),
    ]:
        method = client.patch if not endpoint else client.post
        assert method('/api/edge/sessions/' + session['id'] + endpoint, json=body).status_code == 409
    assert snapshot(client, session) == current


def test_cross_kind_duplicate_id_patch_is_rejected_without_partial_removal(edge):
    client, _ = edge
    session, original = create_session(client)
    obstacle = copy.deepcopy(original['obstacles'][0])
    obstacle['id'] = original['pipes'][0]['id']
    response = client.patch('/api/edge/sessions/' + session['id'], json={
        'base_revision': session['revision'], 'upsert_obstacles': [obstacle],
        'remove_pipe_ids': ['P-202'],
    })
    assert response.status_code == 422, response.text
    assert snapshot(client, session) == session


def test_analysis_matches_existing_engine_and_emits_measured_cad_recommendations(edge):
    client, _ = edge
    session, original = create_session(client, 'congested')
    state = wait_job(client, submit(client, session, clearance=.15))
    assert state['status'] == 'complete', state
    assert state['source_revision'] == session['revision']
    result = state['result']
    expected = analyze(Model.model_validate(original), .15)
    assert json.loads(json.dumps(result['analysis'])) == json.loads(json.dumps(expected))
    recommendation = next(r for r in result['recommendations'] if r['pipe_id'] == 'P-101')
    assert recommendation['id']
    assert recommendation['cad_handles'] == ['A1', 'A2']
    assert recommendation['location']
    assert recommendation['measured'] is not None
    assert recommendation['required'] is not None
    assert recommendation['severity'] in {'error', 'warning', 'info'}
    assert snapshot(client, session)['model'] == original


def test_optimization_and_approval_leave_source_immutable(edge):
    client, _ = edge
    session, original = create_session(client)
    proposal = optimized_proposal(client, session)
    assert proposal['source_revision'] == session['revision']
    assert proposal['status'] == 'pending'
    assert proposal['model'] != original
    assert proposal['original_geometry']
    assert proposal['proposed_geometry']
    assert 'P-101' in proposal['affected_component_ids']
    assert snapshot(client, session)['model'] == original
    response = decide(client, session, proposal, comment='Reviewed implemented constraints')
    assert response.status_code == 200, response.text
    current = snapshot(client, session)
    assert current['revision'] == session['revision']
    assert current['model'] == original
    assert current['proposals'][0]['status'] == 'approved'
    assert len(current['audit']) > len(session['audit'])


@pytest.mark.parametrize('format', ['json', 'dxf', 'csv', 'report'])
def test_export_requires_explicit_current_approval(edge, format):
    client, _ = edge
    session, original = create_session(client)
    proposal = optimized_proposal(client, session)
    assert export(client, session, proposal, format).status_code == 409
    assert decide(client, session, proposal).status_code == 200
    response = export(client, session, proposal, format)
    assert response.status_code == 200, response.text
    assert response.content
    assert snapshot(client, session)['model'] == original
    if format == 'json':
        assert response.json()['model'] == proposal['model']
    if format == 'report':
        assert 'UNVERIFIED' in response.text
        assert 'NOT FOR CONSTRUCTION' in response.text


def test_rejected_proposal_cannot_be_exported(edge):
    client, _ = edge
    session, original = create_session(client)
    proposal = optimized_proposal(client, session)
    response = decide(client, session, proposal, 'reject')
    assert response.status_code == 200, response.text
    current = snapshot(client, session)
    assert current['proposals'][0]['status'] == 'rejected'
    assert current['model'] == original
    assert export(client, session, proposal).status_code == 409


def test_approved_proposal_becomes_unexportable_after_geometry_revision(edge):
    client, _ = edge
    session, original = create_session(client)
    proposal = optimized_proposal(client, session)
    assert decide(client, session, proposal).status_code == 200
    pipe = copy.deepcopy(original['pipes'][0])
    pipe['points'][1][1] = 5
    response = client.patch('/api/edge/sessions/' + session['id'], json={
        'base_revision': session['revision'], 'upsert_pipes': [pipe],
    })
    assert response.status_code == 200, response.text
    assert export(client, session, proposal).status_code == 409
    current = snapshot(client, session)
    assert decide(client, current, proposal).status_code == 409


def test_empty_engineer_cannot_approve(edge):
    client, _ = edge
    session, original = create_session(client)
    proposal = optimized_proposal(client, session)
    response = decide(client, session, proposal, engineer='')
    assert response.status_code == 422, response.text
    assert snapshot(client, session)['proposals'][0]['status'] == 'pending'
    assert snapshot(client, session)['model'] == original


def test_source_proposals_and_audit_survive_store_reopening(edge):
    client, path = edge
    session, original = create_session(client)
    proposal = optimized_proposal(client, session)
    assert decide(client, session, proposal, comment='Persistent approval').status_code == 200
    expected = snapshot(client, session)
    with client_for(path) as reopened:
        actual = snapshot(reopened, session)
        assert actual == expected
        assert actual['model'] == original
        assert actual['object_map'] == session['object_map']
        assert export(reopened, session, proposal).status_code == 200


def test_missing_authoritative_metadata_remains_unverified_after_approval(edge):
    client, _ = edge
    session, _ = create_session(client)
    proposal = optimized_proposal(client, session)
    rows = [row for candidate in proposal['candidates'] for row in candidate['validation']]
    by_requirement = {row['requirement']: row for row in rows}
    for requirement in ['organizer_standards', 'commercial_fittings', 'nozzle_orientation']:
        assert by_requirement[requirement]['status'] == 'UNVERIFIED'
    assert proposal['unverified']
    assert decide(client, session, proposal).status_code == 200
    approved = snapshot(client, session)['proposals'][0]
    assert approved['unverified'] == proposal['unverified']
    assert approved['candidates'] == proposal['candidates']


def test_unknown_session_and_job_are_clear_404_errors(edge):
    client, _ = edge
    assert client.get('/api/edge/sessions/absent').status_code == 404
    assert client.get('/api/edge/jobs/absent').status_code == 404
    assert client.delete('/api/edge/jobs/absent').status_code == 404


def test_cad_handle_cannot_silently_alias_two_engineering_objects(edge):
    client, _ = edge
    model = get_scenario('inefficient').model_dump(mode='json')
    response = client.post('/api/edge/sessions', json={
        'drawing_id': 'ambiguous.dwg', 'model': model,
        'object_map': {'P-101': ['A1'], 'P-202': ['A1']},
    })
    assert response.status_code == 422, response.text


def test_singleton_handle_mapping_is_normalized_without_changing_coordinates(edge):
    client, _ = edge
    session, original = create_session(client, object_map={'P-101': 'A1', 'P-202': ['B1']})
    assert session['object_map'] == {'P-101': ['A1'], 'P-202': ['B1']}
    assert session['model'] == original


def cache_session(client):
    source = get_scenario('efficient').model_dump(mode='json')
    source['bounds'] = {'min': [0, 0, 0], 'max': [50, 50, 5]}
    source['pipes'] = []
    for id, points in [('NEAR-A', [[1, 1, 1], [10, 1, 1]]),
                       ('NEAR-B', [[1, 2, 1], [10, 2, 1]]),
                       ('FAR-C', [[30, 30, 1], [40, 30, 1]])]:
        source['pipes'].append({
            'id': id, 'system_id': id, 'points': points,
            'outer_diameter': .114, 'editable': False,
        })
    source['obstacles'] = []
    response = client.post('/api/edge/sessions', json={
        'drawing_id': 'cache-test.dwg', 'model': source,
        'object_map': {id: [id + '-HANDLE'] for id in ['NEAR-A', 'NEAR-B', 'FAR-C']},
    })
    assert response.status_code == 200, response.text
    return response.json()


def analysis_result(client, session, clearance=.1):
    state = wait_job(client, submit(client, session, clearance=clearance))
    assert state['status'] == 'complete', state
    result = state['result']
    assert json.loads(json.dumps(result['analysis'])) == json.loads(json.dumps(
        analyze(Model.model_validate(session['model']), clearance)))
    return result


def test_unchanged_analysis_reuses_cached_pipe_results(edge):
    client, _ = edge
    session = cache_session(client)
    first = analysis_result(client, session)
    second = analysis_result(client, session)
    assert first['cache']['recomputed'] == 3
    assert second['cache']['reused'] == 3
    assert second['cache']['recomputed'] == 0
    assert set(second['cache']['reused_pipe_ids']) == {'NEAR-A', 'NEAR-B', 'FAR-C'}
    assert first['analysis'] == second['analysis']


def test_incremental_cache_invalidates_other_nearby_system_but_reuses_far_pipe(edge):
    client, _ = edge
    session = cache_session(client)
    baseline = analysis_result(client, session)
    assert not baseline['analysis']['issues']
    changed = copy.deepcopy(session['model']['pipes'][1])
    changed['points'] = [[1, 1.05, 1], [10, 1.05, 1]]
    response = client.patch('/api/edge/sessions/' + session['id'], json={
        'base_revision': session['revision'], 'upsert_pipes': [changed],
    })
    assert response.status_code == 200, response.text
    current = snapshot(client, session)
    result = analysis_result(client, current)
    assert set(result['cache']['recomputed_pipe_ids']) == {'NEAR-A', 'NEAR-B'}
    assert result['cache']['reused_pipe_ids'] == ['FAR-C']
    collisions = [issue for issue in result['analysis']['issues'] if issue['type'] == 'pipe_collision']
    assert {issue['pipe_id'] for issue in collisions} == {'NEAR-A', 'NEAR-B'}
    assert all(issue['measured'] < 0 for issue in collisions)
    assert set(result['affected_pipe_ids']) >= {'NEAR-A', 'NEAR-B'}


def test_changing_clearance_invalidates_cached_geometry_decisions(edge):
    client, _ = edge
    session = cache_session(client)
    analysis_result(client, session, clearance=.1)
    result = analysis_result(client, session, clearance=.2)
    assert result['cache']['recomputed'] == 3
    assert result['cache']['reused'] == 0


def test_removing_physical_pipe_invalidates_surrounding_system_checks(edge):
    client, _ = edge
    session = cache_session(client)
    changed = copy.deepcopy(session['model']['pipes'][1])
    changed['points'] = [[1, 1.05, 1], [10, 1.05, 1]]
    response = client.patch('/api/edge/sessions/' + session['id'], json={
        'base_revision': session['revision'], 'upsert_pipes': [changed],
    })
    assert response.status_code == 200, response.text
    session = snapshot(client, session)
    assert analysis_result(client, session)['analysis']['issues']
    response = client.patch('/api/edge/sessions/' + session['id'], json={
        'base_revision': session['revision'], 'remove_pipe_ids': ['NEAR-B'],
    })
    assert response.status_code == 200, response.text
    current = snapshot(client, session)
    result = analysis_result(client, current)
    assert not result['analysis']['issues']
    assert result['cache']['recomputed_pipe_ids'] == ['NEAR-A']
    assert result['cache']['reused_pipe_ids'] == ['FAR-C']
    assert 'NEAR-B' not in current['object_map']


def test_background_analysis_is_rejected_if_source_changes_before_completion(edge, monkeypatch):
    from backend import edge_store
    client, _ = edge
    session, original = create_session(client)
    entered, release = threading.Event(), threading.Event()
    real_analysis = edge_store.analyze_pipe

    def delayed_analysis(*args, **kwargs):
        entered.set()
        assert release.wait(5), 'Test did not release delayed analysis'
        return real_analysis(*args, **kwargs)

    monkeypatch.setattr(edge_store, 'analyze_pipe', delayed_analysis)
    job_id = submit(client, session)
    try:
        assert entered.wait(5), 'Analysis did not start'
        pipe = copy.deepcopy(original['pipes'][0])
        pipe['points'][1][1] = 5
        response = client.patch('/api/edge/sessions/' + session['id'], json={
            'base_revision': session['revision'], 'upsert_pipes': [pipe],
        })
        assert response.status_code == 200, response.text
    finally:
        release.set()
    state = wait_job(client, job_id)
    assert state['status'] == 'stale', state
    current = snapshot(client, session)
    assert current['analysis'] is None
    assert current['revision'] != session['revision']
    assert current['model']['pipes'][0] == pipe
    assert not any(event['event'] == 'analysis_completed' for event in current['audit'])


def test_cancelled_analysis_does_not_publish_a_result(edge, monkeypatch):
    from backend import edge_store
    client, _ = edge
    session, original = create_session(client)
    entered, release = threading.Event(), threading.Event()
    real_analysis = edge_store.analyze_pipe

    def delayed_analysis(*args, **kwargs):
        entered.set()
        assert release.wait(5), 'Test did not release delayed analysis'
        return real_analysis(*args, **kwargs)

    monkeypatch.setattr(edge_store, 'analyze_pipe', delayed_analysis)
    job_id = submit(client, session)
    try:
        assert entered.wait(5), 'Analysis did not start'
        response = client.delete('/api/edge/jobs/' + job_id)
        assert response.status_code == 200, response.text
    finally:
        release.set()
    assert wait_job(client, job_id)['status'] == 'cancelled'
    current = snapshot(client, session)
    assert current['analysis'] is None
    assert current['model'] == original


def test_approval_replay_is_rejected_and_does_not_duplicate_audit(edge):
    client, _ = edge
    session, _ = create_session(client)
    proposal = optimized_proposal(client, session)
    assert decide(client, session, proposal).status_code == 200
    approved = snapshot(client, session)
    assert decide(client, session, proposal).status_code == 409
    assert snapshot(client, session) == approved


def test_explicit_application_requires_approval_confirmation_and_current_revision(edge):
    client, _ = edge
    session, original = create_session(client)
    proposal = optimized_proposal(client, session)
    url = '/api/edge/sessions/' + session['id'] + '/proposals/' + proposal['id'] + '/apply'
    body = {'revision': session['revision'], 'engineer': 'Test engineer', 'confirmation': True}
    assert client.post(url, json=body).status_code == 409
    assert decide(client, session, proposal).status_code == 200
    unconfirmed = client.post(url, json={**body, 'confirmation': False})
    assert unconfirmed.status_code in {409, 422}, unconfirmed.text
    assert snapshot(client, session)['model'] == original
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    current = snapshot(client, session)
    assert current['revision'] != session['revision']
    assert current['model'] == proposal['model']
    assert current['proposals'][0]['status'] == 'applied'
    assert any(event['event'] == 'proposal_applied_to_session' and event['cad_files_modified'] is False
               for event in current['audit'])
    assert client.post(url, json=body).status_code == 409


def test_background_optimization_cannot_publish_proposals_for_newer_source(edge, monkeypatch):
    from backend import edge_api
    client, _ = edge
    session, original = create_session(client)
    entered, release = threading.Event(), threading.Event()
    real_compute = edge_api.execute_remote_or_local

    def delayed_compute(*args, **kwargs):
        entered.set()
        assert release.wait(5), 'Test did not release delayed optimization'
        return real_compute(*args, **kwargs)

    monkeypatch.setattr(edge_api, 'execute_remote_or_local', delayed_compute)
    job_id = submit(client, session, 'optimize', resolution=1)
    try:
        assert entered.wait(5), 'Optimization did not start'
        pipe = copy.deepcopy(original['pipes'][0])
        pipe['points'][1][1] = 5
        response = client.patch('/api/edge/sessions/' + session['id'], json={
            'base_revision': session['revision'], 'upsert_pipes': [pipe],
        })
        assert response.status_code == 200, response.text
    finally:
        release.set()
    state = wait_job(client, job_id)
    assert state['status'] == 'stale', state
    assert state['result'] is None
    current = snapshot(client, session)
    assert current['model']['pipes'][0] == pipe
    assert not current['proposals']
    assert not any(event['event'] == 'proposals_generated' for event in current['audit'])


def test_completed_result_is_not_returned_against_a_newer_drawing_revision(edge):
    client, _ = edge
    session, original = create_session(client)
    job_id = submit(client, session)
    assert wait_job(client, job_id)['status'] == 'complete'
    pipe = copy.deepcopy(original['pipes'][0])
    pipe['points'][1][1] = 5
    response = client.patch('/api/edge/sessions/' + session['id'], json={
        'base_revision': session['revision'], 'upsert_pipes': [pipe],
    })
    assert response.status_code == 200, response.text
    previous = client.get('/api/edge/jobs/' + job_id).json()
    assert previous['status'] == 'stale'
    assert previous['source_revision'] == session['revision']
    assert previous['result'] is None


def test_handle_only_update_advances_revision_and_keeps_valid_geometry_cache(edge):
    client, _ = edge
    session, original = create_session(client)
    first = analysis_result(client, session)
    response = client.patch('/api/edge/sessions/' + session['id'], json={
        'base_revision': session['revision'], 'object_map': {'P-101': ['NEW-A1']},
    })
    assert response.status_code == 200, response.text
    current = snapshot(client, session)
    assert current['revision'] != session['revision']
    assert current['model'] == original
    result = analysis_result(client, current)
    assert result['analysis'] == first['analysis']
    assert result['cache']['recomputed'] == 0
    assert result['cache']['reused'] == len(original['pipes'])
    assert all(r['source_revision'] == current['revision'] for r in result['recommendations'])
    assert next(r for r in result['recommendations'] if r['pipe_id'] == 'P-101')['cad_handles'] == ['NEW-A1']


def test_concurrent_store_instances_cannot_commit_two_changes_at_same_revision(edge):
    from concurrent.futures import ThreadPoolExecutor
    client, path = edge
    session, original = create_session(client)
    first_pipe = copy.deepcopy(original['pipes'][0])
    first_pipe['points'][1][1] = 5
    second_pipe = copy.deepcopy(original['pipes'][0])
    second_pipe['points'][1][1] = 4
    start = threading.Barrier(2)

    def update(other, pipe):
        start.wait(timeout=5)
        return other.patch('/api/edge/sessions/' + session['id'], json={
            'base_revision': session['revision'], 'upsert_pipes': [pipe],
        })

    with client_for(path) as second_client, ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(update, client, first_pipe)
        second = executor.submit(update, second_client, second_pipe)
        responses = [first.result(timeout=10), second.result(timeout=10)]
    assert sorted(response.status_code for response in responses) == [200, 409]
    current = snapshot(client, session)
    assert current['revision'] == session['revision'] + 1
    assert current['model']['pipes'][0] in [first_pipe, second_pipe]
    assert sum(event['event'] == 'geometry_updated' for event in current['audit']) == 1
