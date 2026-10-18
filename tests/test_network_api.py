"""Synthetic-only connected workflow: HTTP boundaries, review freshness, actual exports."""
from copy import deepcopy
from http.client import HTTPConnection
from io import BytesIO
import json
import sys
import threading
import time
from pathlib import Path
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.network_api import create_router
from pipe_router.network_service import NetworkWorkspace, MAX_NETWORK_REQUEST_BYTES

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def network():
    return {
        'schema_version':'pipeforge.network/4.0', 'id':'http-network', 'units':'mm', 'provenance':'synthetic',
        'drawing':{'id':'synthetic-http-drawing','revision':'r1'},
        'coordinate_system':{'frame':'WCS','up':[0,0,1],'fore_aft':[1,0,0],'athwartship':[0,1,0]},
        'bounds':{'min':[-500,-500,-500],'max':[2500,1000,1000]},
        'nodes':[{'id':'a','type':'equipment_port','system_id':'s','position':[0,0,0],'fixed':True},
                 {'id':'b','type':'equipment_port','system_id':'s','position':[2000,0,0],'fixed':True}],
        'spans':[{'id':'ab','from_node':'a','to_node':'b','system_id':'s','system_type':'pressurized','points':[[0,0,0],[2000,0,0]],'outer_diameter_mm':60,'nominal_diameter_mm':50,'cad_handle':'ABC','modifiable':True}],
        'obstacles':[], 'coverage':{'complete':True,'omitted_count':0,'unknown_count':0,'notes':['Synthetic full test geometry']},
    }


@pytest.fixture
def client(tmp_path):
    workspace = NetworkWorkspace(tmp_path)
    app = FastAPI()
    app.include_router(create_router(workspace))
    with TestClient(app, base_url='http://127.0.0.1') as client:
        yield client
    workspace.close()


def test_inspection_preserves_input_and_independent_evidence(client, network):
    original = deepcopy(network)
    response = client.post('/api/network/inspect', json={'network':network})
    assert response.status_code == 200, response.text
    record = response.json()
    assert record['network']['drawing'] == network['drawing']
    assert record['network']['spans'][0]['cad_handle'] == 'ABC'
    assert record['validation']['checks']
    assert all(check['status'] in ('PASS','FAIL','UNVERIFIED','NOT_APPLICABLE') for check in record['validation']['checks'])
    geometry=record['geometry']
    assert geometry['complete']
    segment=geometry['spans'][0]['segments'][0]
    assert segment['radius'] == 30
    assert segment['start'] == [0,0,0]
    assert segment['end'] == [2000,0,0]
    assert network == original
    result = client.post('/api/network/validate', json={'network':record['network'], 'reference':network})
    assert result.status_code == 200
    assert len(result.json()['source_digest']) == 64


def test_host_origin_content_and_budget_guards(client, network):
    assert client.post('/api/network/inspect',json={'network':network},headers={'Host':'attacker.invalid'}).status_code == 403
    assert client.post('/api/network/inspect',json={'network':network},headers={'Origin':'https://attacker.invalid'}).status_code == 403
    assert client.post('/api/network/inspect',content='{}',headers={'Content-Type':'text/plain'}).status_code == 415
    assert client.post('/api/network/inspect',content='x'*(MAX_NETWORK_REQUEST_BYTES+1),headers={'Content-Type':'application/json'}).status_code == 413
    assert client.post('/api/network/optimize',json={'network':network,'max_evaluations':True}).status_code == 422
    assert client.post('/api/network/optimize',json={'network':network,'max_evaluations':65}).status_code == 422
    for raw in (17, True, None, [], 'cad_id'):
        assert client.post('/api/network/inspect',content=json.dumps(raw),headers={'Content-Type':'application/json'}).status_code == 422
    assert client.post('/api/network/inspect',content='['*1500+'0'+']'*1500,headers={'Content-Type':'application/json'}).status_code == 422
    assert client.get('/api/network/downloads/'+'a'*32+'/requirements.txt').status_code == 404


def test_cad_inspection_requires_declared_permitted_volume(client):
    response = client.post('/api/network/inspect',json={'cad':{'units':'mm','bounds':{'min':[0,0,0],'max':[100,100,100]},'route_candidates':[]}})
    assert response.status_code == 422
    assert 'permitted ship bounds' in response.text


def test_confirmation_is_explicit_and_auditable(client, network):
    a,b=network['nodes']
    network['nodes']=[a,{**b,'id':'b','type':'endpoint','position':[1000,0,0]}, {**b,'id':'c','type':'endpoint','position':[1000,0,0]}, {**b,'id':'d','position':[2000,0,0]}]
    template=network['spans'][0]
    network['spans']=[{**template,'points':[[0,0,0],[1000,0,0]]},{**template,'id':'cd','from_node':'c','to_node':'d','points':[[1000,0,0],[2000,0,0]]}]
    network['unresolved_connections']=[{'id':'near','endpoint_ids':['b','c'],'reason':'Geometric proximity','status':'unresolved'}]
    inspected=client.post('/api/network/inspect',json={'network':network}).json()['network']
    assert len(inspected['nodes']) == 4
    record=client.post('/api/network/confirm',json={'network':network,'decisions':[{'connection_id':'near','action':'connect','node_ids':['b','c'],'position':[1000,0,0]}]})
    assert record.status_code == 200, record.text
    confirmed=record.json()['network']
    assert len(confirmed['nodes']) == 3
    assert not confirmed['unresolved_connections']
    assert confirmed['connection_decisions'][0]['members'] == ['b','c']


def test_review_requires_server_result_approval_and_current_revision(client, network):
    result=client.post('/api/network/optimize',json={'network':network,'max_evaluations':1})
    assert result.status_code == 200, result.text
    record=result.json()
    review={'network':record['network'],'review_token':record['review_token']}
    assert client.post('/api/network/review',json=review).status_code == 409
    assert client.post('/api/network/review',json={**review,'approved':True,'review_token':'forged'}).status_code == 409
    changed=deepcopy(record['network']);changed['drawing']['revision']='r2'
    assert client.post('/api/network/review',json={**review,'network':changed,'approved':True}).status_code == 409
    exported=client.post('/api/network/review',json={**review,'approved':True,'result':{'proposed_network':{'injected':True}}})
    assert exported.status_code == 200, exported.text
    bundle=exported.json()
    assert bundle['native_applied'] is False
    downloads=bundle['exports']
    excel=next(url for url in downloads.values() if url.endswith('.xlsx'))
    content=client.get(excel)
    assert content.status_code == 200
    with ZipFile(BytesIO(content.content)) as workbook:
        assert 'xl/workbook.xml' in workbook.namelist()
    result_url=next(url for url in downloads.values() if url.endswith('network-result.json'))
    saved=client.get(result_url).json()
    assert saved['source_digest'] == record['result']['source_digest']
    assert saved['original_network']['drawing']['revision'] == 'r1'
    assert 'injected' not in (saved.get('proposed_network') or {})


def test_background_job_reports_actual_completion(client, network):
    started=client.post('/api/network/jobs',json={'network':network,'max_evaluations':1})
    assert started.status_code == 200
    identifier=started.json()['id']
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        job=client.get('/api/network/jobs/'+identifier).json()
        if job['status'] not in ('queued','running'):break
        time.sleep(.01)
    assert job['status'] == 'complete', job
    assert job['elapsed_seconds'] >= 0
    assert job['response']['result']['status'] == 'NO IMPROVEMENT FOUND'
    assert job['response']['review_token']
    assert client.post('/api/network/jobs/unknown/cancel',json={}).status_code == 404


def test_companion_static_assets_are_available_without_legacy_build(client):
    assert client.get('/connected').status_code == 200
    assert 'Connected piping networks' in client.get('/connected').text
    assert client.get('/connected/connected.js').status_code == 200
    assert client.get('/connected/connected.css').status_code == 200
    assert client.get('/connected/not-a-file').status_code == 404


def test_team_server_connected_endpoints_preserve_host_and_origin_guards(tmp_path, network):
    sys.path.insert(0,str(ROOT/'scripts'))
    from run_web import make_server
    server=make_server(port=0,output=tmp_path)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(path,payload=None,headers=None):
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=20)
        try:
            conn.request('GET' if payload is None else 'POST',path,body=None if payload is None else json.dumps(payload),headers={'Content-Type':'application/json',**(headers or {})})
            response=conn.getresponse();return response.status,response.read()
        finally:conn.close()
    try:
        status,body=request('/api/network/inspect',{'network':network})
        assert status == 200,body
        assert json.loads(body)['network']['drawing']['revision']=='r1'
        assert request('/connected')[0] == 200
        assert request('/api/network/inspect',{'network':network},{'Origin':'http://attacker.invalid'})[0] == 403
        assert request('/api/network/examples',headers={'Host':'attacker.invalid'})[0] == 403
        status,body=request('/api/network/optimize',{'network':network,'max_evaluations':1})
        assert status == 200,body
        record=json.loads(body)
        status,body=request('/api/network/review',{'network':record['network'],'review_token':record['review_token'],'approved':True})
        assert status == 200,body
        exports=json.loads(body)['exports']
        assert all(request(url)[0]==200 for url in exports.values())
    finally:
        server.shutdown();server.server_close();server.RequestHandlerClass.keywords['workspace'].network.close();thread.join(3)


def test_team_acknowledgement_releases_calculation_lock_before_next_request(tmp_path, monkeypatch):
    """A slow response thread must not make a sequential request appear busy."""
    sys.path.insert(0,str(ROOT/'scripts'))
    from run_web import Handler, make_server
    acknowledged=threading.Event();release=threading.Event()
    original=Handler.send_bytes
    def delayed_after_acknowledgement(self,*args,**kwargs):
        original(self,*args,**kwargs)
        if self.path == '/api/make-detour' and not acknowledged.is_set():
            acknowledged.set();release.wait(3)
    monkeypatch.setattr(Handler,'send_bytes',delayed_after_acknowledgement)
    server=make_server(port=0,output=tmp_path)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def post():
        connection=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            connection.request('POST','/api/make-detour',body='{}',headers={'Content-Type':'application/json'})
            response=connection.getresponse();response.read();return response.status
        finally:connection.close()
    try:
        assert post() == 400
        assert acknowledged.wait(1)
        assert post() == 400
    finally:
        release.set();server.shutdown();server.server_close();server.RequestHandlerClass.keywords['workspace'].network.close();thread.join(3)
