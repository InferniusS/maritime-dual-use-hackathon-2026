import csv
import io
import json
import ezdxf
import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.exports import export_data,import_dxf
from backend.models import Model
from backend.optimizer import optimize
from backend.scenarios import get_scenario
from backend.geometry import length


@pytest.fixture
def proposal():
    original=get_scenario('inefficient')
    return original,Model.model_validate(optimize(original)['model'])


def test_json_retains_semantics_and_precise_change_instructions(proposal):
    original,proposed=proposal
    text,mime=export_data(original,proposed)
    report=json.loads(text)
    assert mime=='application/json'
    assert Model.model_validate(report['model'])==proposed
    change=report['changes'][0]
    assert change['pipe_id']==proposed.pipes[0].id
    assert change['original_coordinates']==[list(p) for p in original.pipes[0].points]
    assert change['proposed_coordinates']==[list(p) for p in proposed.pipes[0].points]
    assert change['after']['length']==pytest.approx(length(proposed.pipes[0].points))
    assert 'NOT FOR CONSTRUCTION' in report['approval']


def test_dxf_roundtrip_retains_centerlines_sizes_and_meter_units(proposal):
    original,proposed=proposal
    text,mime=export_data(original,proposed,'dxf')
    assert mime=='application/dxf'
    doc=ezdxf.read(io.StringIO(text))
    assert doc.units==6
    for entity,pipe in zip(doc.modelspace().query('POLYLINE'),proposed.pipes):
        assert [tuple(v.dxf.location) for v in entity.vertices]==pipe.points
        values=entity.get_xdata('PIPEFORGE')
        assert values[0].value==pipe.id
        assert values[2].value==pipe.nominal_diameter
        assert values[3].value==pipe.outer_diameter
    imported=import_dxf(text,proposed)
    assert imported==proposed


def test_csv_and_human_report_include_original_and_proposed(proposal):
    original,proposed=proposal
    text,_=export_data(original,proposed,'csv')
    rows=list(csv.DictReader(io.StringIO(text)))
    assert {r['route'] for r in rows}=={'original_coordinates','proposed_coordinates'}
    assert len(rows)==len(original.pipes[0].points)+len(proposed.pipes[0].points)
    text,_=export_data(original,proposed,'report')
    assert 'UNVERIFIED' in text
    assert original.pipes[0].id in text
    assert 'NOT FOR CONSTRUCTION' in text


@pytest.mark.parametrize('format',['json','dxf','csv','report'])
def test_invalid_modified_geometry_is_not_exported(proposal,format):
    original,proposed=proposal
    proposed.pipes[0].outer_diameter*=2
    with pytest.raises(ValueError,match='invalid modification'):
        export_data(original,proposed,format)


def test_dxf_requires_units_and_explicit_semantics(proposal):
    original,proposed=proposal
    text,_=export_data(original,proposed,'dxf')
    doc=ezdxf.read(io.StringIO(text));doc.units=4
    buf=io.StringIO();doc.write(buf)
    with pytest.raises(ValueError,match='meter units'):
        import_dxf(buf.getvalue(),proposed)
    doc=ezdxf.read(io.StringIO(text))
    doc.modelspace().delete_entity(next(iter(doc.modelspace())))
    buf=io.StringIO();doc.write(buf)
    with pytest.raises(ValueError,match='Missing pipe geometry'):
        import_dxf(buf.getvalue(),proposed)


def test_api_real_analysis_optimization_and_export_roundtrip():
    client=TestClient(app)
    assert client.get('/api/health').json()['certified'] is False
    items=client.get('/api/scenarios').json()
    assert {m['id'] for m in items}=={'inefficient','congested','drainage','impossible','efficient','gravity-detour','vent-rise','multi-system'}
    original=client.get('/api/scenarios/inefficient').json()
    assert client.post('/api/analyze',json=original).status_code==200
    response=client.post('/api/optimize',json={'model':original})
    assert response.status_code==200
    proposed=response.json()['model']
    exported=client.post('/api/export/dxf',json={'original':original,'proposed':proposed})
    assert exported.status_code==200
    imported=client.post('/api/import/dxf',json={'dxf':exported.text,'metadata':proposed})
    assert imported.status_code==200
    assert imported.json()==proposed
    assert client.get('/api/scenarios/missing').status_code==404
    assert client.post('/api/optimize',json={'model':original,'resolution':0}).status_code==422


@pytest.mark.parametrize('field,value',[('id','different-source'),('coordinate_system','shifted origin')])
def test_export_rejects_source_identity_and_frame_mutation(proposal,field,value):
    original,proposed=proposal
    setattr(proposed,field,value)
    with pytest.raises(ValueError,match='source reference'):
        export_data(original,proposed)


def wait_job(client,id,timeout=5):
    import time
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        response=client.get('/api/jobs/'+id)
        assert response.status_code==200
        state=response.json()
        assert isinstance(state['expanded'],int)
        assert 'error' in state and 'result' in state
        if state['status'] not in ('queued','running'):
            return state
        time.sleep(.01)
    pytest.fail('Job did not reach a terminal state within timeout')


def test_job_progress_and_completed_result():
    with TestClient(app) as client:
        response=client.post('/api/jobs',json={'model':get_scenario('congested').model_dump(),'resolution':1})
        assert response.status_code==200
        state=wait_job(client,response.json()['id'])
        assert state['status']=='complete'
        assert state['error'] is None
        assert state['expanded']>0
        assert state['result']['candidates'][0]['status']=='accepted'


def test_job_cancellation_interrupts_actual_slow_search():
    with TestClient(app) as client:
        original=get_scenario('impossible')
        response=client.post('/api/jobs',json={'model':original.model_dump(),'resolution':.2})
        assert response.status_code==200
        id=response.json()['id']
        assert client.delete('/api/jobs/'+id).json()['status']=='cancellation_requested'
        state=wait_job(client,id)
        assert state['status']=='cancelled'
        assert state['error'] is None
        assert state['result']['model']==original.model_dump(mode='json')
        assert all(c['status']!='accepted' for c in state['result']['candidates'])
        assert client.get('/api/jobs/absent').status_code==404
        assert client.delete('/api/jobs/absent').status_code==404
