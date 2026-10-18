"""Regression coverage for source completeness, review races and upgrade assurance."""
import copy,json,sqlite3,threading,time
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from backend import edge_api
from backend.edge_store import SessionStore
from backend.scenarios import get_scenario

@pytest.fixture
def client(tmp_path):
    app=FastAPI();app.include_router(edge_api.create_router(SessionStore(tmp_path/'state.sqlite3')))
    with TestClient(app) as instance:yield instance

def session(client,adapter='json',coverage=None,scenario='inefficient'):
    body={'drawing_id':'review.dwg','model':get_scenario(scenario).model_dump(mode='json'),'source_adapter':adapter}
    if coverage is not None:body['extraction_coverage']=coverage
    response=client.post('/api/edge/sessions',json=body);assert response.status_code==200,response.text
    return response.json()

def wait(client,state,operation='optimize',**options):
    r=client.post('/api/edge/sessions/'+state['id']+'/jobs',json={'revision':state['revision'],'operation':operation,'resolution':1,**options})
    assert r.status_code==200,r.text
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        result=client.get('/api/edge/jobs/'+r.json()['id']).json()
        if result['status'] not in ('queued','running'):return result
        time.sleep(.01)
    pytest.fail('Job did not terminate')

def proposal(client,state):
    result=wait(client,state);assert result['status']=='complete',result
    return result['result']['proposals'][0]

def decide(client,state,p):
    return client.post(f"/api/edge/sessions/{state['id']}/proposals/{p['id']}/decision",json={'revision':state['revision'],'decision':'approve','engineer':'Review engineer'})

def complete_coverage():
    return {'adapter':'autocad','scope':'modelspace','status':'complete','mapped_entities':3,'environment':'Explicit mapped drawing entities.'}

def test_unknown_source_scope_is_retained_and_qualifies_proposal(client):
    state=session(client);p=proposal(client,state)
    assert state['extraction_coverage']['status']=='unverified'
    assert p['extraction_coverage']==state['extraction_coverage']
    for candidate in p['candidates']:
        assert any(r['requirement']=='cad_extraction_coverage' and r['status']=='UNVERIFIED' for r in candidate['validation'])
        assert candidate['assurance']['cad_scope_verified'] is False
        assert candidate['assurance']['geometrically_valid'] is False

def test_coverage_only_source_generation_invalidates_approval_and_preserves_cache(client):
    state=session(client,adapter='autocad',coverage=complete_coverage())
    baseline=wait(client,state,'analyze');assert baseline['status']=='complete',baseline
    p=proposal(client,state);assert decide(client,state,p).status_code==200
    path='/api/edge/sessions/'+state['id']
    changed={**state['extraction_coverage'],'source_generation':1}
    response=client.patch(path,json={'base_revision':1,'extraction_coverage':changed});assert response.status_code==200,response.text
    current=response.json()
    assert current['revision']==2 and current['model']==state['model']
    assert current['proposals'][0]['status']=='stale'
    assert client.get(path+'/proposals/'+p['id']+'/export/json').status_code==409
    fresh=wait(client,current,'analyze');assert fresh['status']=='complete',fresh
    assert fresh['result']['cache']['recomputed']==0
    assert fresh['result']['cache']['reused']==len(state['model']['pipes'])
    backwards=client.patch(path,json={'base_revision':2,'extraction_coverage':state['extraction_coverage']})
    assert backwards.status_code==422
    assert client.get(path).json()['revision']==2

def test_known_unclassified_geometry_prevents_approval_without_modifying_source(client):
    coverage={**complete_coverage(),'status':'incomplete','unmapped_entities':1,'unresolved_ids':['ABC'],
              'unresolved_entities':[{'handle':'ABC','entity_type':'3DSOLID','layer':'Structure','reason':'Classification required'}]}
    state=session(client,adapter='dxf',coverage=coverage);p=proposal(client,state)
    response=decide(client,state,p);assert response.status_code==409
    assert 'unclassified' in response.text
    current=client.get('/api/edge/sessions/'+state['id']).json()
    assert current['model']==state['model'] and current['proposals'][0]['status']=='pending'
    assert current['extraction_coverage']['unresolved_entities'][0]['handle']=='ABC'

def test_complete_claim_cannot_hide_unresolved_entities(client):
    bad={**complete_coverage(),'unmapped_entities':1,'unresolved_ids':['B1']}
    r=client.post('/api/edge/sessions',json={'drawing_id':'bad','model':get_scenario('efficient').model_dump(mode='json'),'extraction_coverage':bad})
    assert r.status_code==422

def test_native_owned_source_cannot_be_replaced_by_virtual_application(client):
    state=session(client,adapter='autocad',coverage=complete_coverage());p=proposal(client,state)
    assert decide(client,state,p).status_code==200
    r=client.post(f"/api/edge/sessions/{state['id']}/proposals/{p['id']}/apply",json={'revision':1,'engineer':'Review engineer','confirmation':True})
    assert r.status_code==409 and 'AutoCAD owns' in r.text
    current=client.get('/api/edge/sessions/'+state['id']).json()
    assert current['revision']==1 and current['model']==state['model']

def test_source_edit_during_export_is_rejected(client,monkeypatch):
    state=session(client);p=proposal(client,state);assert decide(client,state,p).status_code==200
    entered=threading.Event();release=threading.Event();original=edge_api.export_data
    def delayed(*args,**kwargs):
        entered.set();assert release.wait(10);return original(*args,**kwargs)
    monkeypatch.setattr(edge_api,'export_data',delayed)
    path='/api/edge/sessions/'+state['id']
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(client.get,path+'/proposals/'+p['id']+'/export/json')
            assert entered.wait(5)
            pipe=copy.deepcopy(state['model']['pipes'][0]);pipe['points'][1][1]+=.25
            assert client.patch(path,json={'base_revision':1,'upsert_pipes':[pipe]}).status_code==200
            release.set();response=future.result(10)
    finally:release.set()
    assert response.status_code==409

def test_work_budget_failure_publishes_no_approval_or_analysis(client):
    state=session(client,scenario='congested')
    # Accepted finite input previously reached an enormous np.arange before checking cancellation.
    model=copy.deepcopy(state['model']);model['bounds']['max'][0]=1e12
    r=client.post('/api/edge/sessions',json={'drawing_id':'huge','model':model});assert r.status_code==200
    huge=r.json();result=wait(client,huge,'optimize',resolution=.2)
    assert result['status']=='failed',result
    assert 'axis' in result['error'].lower() or 'grid' in result['error'].lower()
    current=client.get('/api/edge/sessions/'+huge['id']).json()
    assert current['analysis'] is None and current['proposals']==[]

def test_access_token_never_enters_result_session_or_audit(client):
    state=session(client,scenario='efficient');token='synthetic-test-token-not-a-real-secret'
    result=wait(client,state,'analyze',remote={'enabled':True,'endpoint':'http://127.0.0.1:1','access_token':token,'timeout_s':.1})
    assert result['status']=='complete',result
    assert result['execution']['location']=='local'
    current=client.get('/api/edge/sessions/'+state['id']).json()
    assert token not in json.dumps(result) and token not in json.dumps(current)

def test_legacy_assurance_is_archived_invalidated_and_not_repromoted(tmp_path):
    path=tmp_path/'legacy.sqlite3'
    store=SessionStore(path);state=store.create('old.dwg',get_scenario('inefficient'))
    old=store.capture(state['id']);old.pop('engineering_version')
    for pipe in old['model']['pipes']:
        pipe.pop('metadata_missing',None)
        for fitting in pipe['fittings']:fitting.pop('metadata_missing',None)
    old['analysis']={'metrics':{'P-101':{'hydraulics':{'status':'PASS'}}}}
    old['_cache']={'P-101':{'unsafe':'legacy assurance'}}
    old['proposals']=[{'id':'old-approval','status':'approved'}]
    encoded=json.dumps(old)
    with store.db:store.db.execute('UPDATE sessions SET data=? WHERE id=?',(encoded,state['id']))
    upgraded=SessionStore(path);current=upgraded.capture(state['id'])
    assert current['revision']==2 and current['engineering_version']=='3.2'
    assert current['analysis'] is None and current['_cache']=={}
    assert current['proposals'][0]['status']=='stale'
    for old_pipe,new_pipe in zip(old['model']['pipes'],current['model']['pipes']):
        assert new_pipe['points']==old_pipe['points']
        assert new_pipe['outer_diameter']==old_pipe['outer_diameter']
        assert 'outer_diameter' in new_pipe['metadata_missing']
    backup=upgraded.db.execute('SELECT source_snapshot FROM engineering_migrations WHERE session_id=?',(state['id'],)).fetchone()[0]
    assert backup==encoded
    assert SessionStore(path).get(state['id'])['revision']==2


def test_expired_work_waiting_for_sqlite_writer_cannot_publish(tmp_path):
    from backend.work_limits import work_scope,WorkLimitExceeded
    store=SessionStore(tmp_path/'locked.sqlite3');state=store.create('locked',get_scenario('efficient'))
    blocker=sqlite3.connect(store.path);blocker.execute('BEGIN IMMEDIATE')
    entered=threading.Event()
    def publish():
        with work_scope({'deadline_s':.02}):
            entered.set();store.publish_analysis(state['id'],1,{'issues':[],'metrics':{}},{})
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(publish);assert entered.wait(2)
            time.sleep(.08);blocker.rollback()
            with pytest.raises(WorkLimitExceeded):future.result(3)
    finally:blocker.close()
    current=store.get(state['id'])
    assert current['analysis'] is None
    assert not any(event['event']=='analysis_completed' for event in current['audit'])


def test_legacy_dxf_endpoint_rejects_closed_centerline():
    import io,ezdxf
    from backend.app import app
    model=get_scenario('efficient');doc=ezdxf.new();doc.units=6
    for index,pipe in enumerate(model.pipes):
        layer='PIPE_'+pipe.id;doc.layers.new(layer)
        entity=doc.modelspace().add_polyline3d(pipe.points,dxfattribs={'layer':layer})
        if index==0:entity.close(True)
    buffer=io.StringIO();doc.write(buffer)
    with TestClient(app) as legacy:
        r=legacy.post('/api/import/dxf',json={'dxf':buffer.getvalue(),'metadata':model.model_dump(mode='json')})
    assert r.status_code==422 and 'open, simple' in r.text
