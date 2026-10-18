"""Missing engineering inputs must survive defaults, persistence and reuse."""
import copy
import json

import pytest
from pydantic import ValidationError

from backend.hydraulics import hydraulic
from backend.models import Bounds,Fitting,Model,Pipe


def minimal_pipe(**changes):
    values=dict(id='P-CAD',system_id='Assigned service',points=[(1,1,1),(4,1,1)])
    values.update(changes)
    return Pipe.model_validate(values)


def test_legacy_preview_numbers_do_not_establish_supplied_dimensions():
    pipe=minimal_pipe()
    assert pipe.nominal_diameter==.1 and pipe.outer_diameter==.114
    assert pipe.dimension_metadata()=={
        'status':'UNVERIFIED',
        'missing':['nominal_diameter','outer_diameter','inner_diameter'],
        'origins':{'nominal_diameter':'legacy_default','outer_diameter':'legacy_default','inner_diameter':'missing'},
    }


def test_explicit_dimensions_are_recorded_as_inputs_without_manufacturing_claim():
    pipe=minimal_pipe(nominal_diameter=.1,outer_diameter=.114,inner_diameter=.1)
    assert pipe.dimension_metadata()=={
        'status':'PASS','missing':[],
        'origins':{'nominal_diameter':'supplied','outer_diameter':'supplied','inner_diameter':'supplied'},
    }
    assert hydraulic(pipe)['status']=='UNVERIFIED'  # Operating conditions remain missing.


def test_complete_dump_does_not_promote_omitted_input_to_supplied_value():
    original=minimal_pipe()
    for _ in range(3):
        payload=original.model_dump(mode='json')
        assert payload['nominal_diameter']==.1 and payload['outer_diameter']==.114
        restored=Pipe.model_validate(json.loads(json.dumps(payload)))
        assert restored.dimension_metadata()==original.dimension_metadata()
        assert restored.metadata_missing==original.metadata_missing
        original=restored


def test_empty_missing_list_cannot_hide_fields_omitted_from_same_input():
    pipe=minimal_pipe(metadata_missing=[])
    assert pipe.dimension_metadata()['status']=='UNVERIFIED'
    assert set(pipe.metadata_missing)=={'nominal_diameter','outer_diameter','inner_diameter'}
    fitting=Fitting.model_validate({'metadata_missing':[]})
    assert set(fitting.metadata_missing)=={'angle','radius','k'}


def test_null_numeric_metadata_stays_missing_instead_of_becoming_authoritative_default():
    pipe=minimal_pipe(nominal_diameter=None,outer_diameter=None)
    fitting=Fitting.model_validate({'angle':None,'radius':None,'k':None})
    assert pipe.outer_diameter==.114
    assert pipe.dimension_metadata()['status']=='UNVERIFIED'
    assert fitting.k==.9
    assert fitting.metadata_status()['status']=='UNVERIFIED'
    assert Fitting.model_validate_json(fitting.model_dump_json()).metadata_status()==fitting.metadata_status()


def test_fitting_default_coefficient_provenance_survives_nested_model_serialization():
    model=Model(id='cad',name='CAD metadata',bounds=Bounds(min=(0,0,0),max=(6,6,6)),pipes=[minimal_pipe()])
    restored=Model.model_validate_json(model.model_dump_json())
    fitting=restored.pipes[0].fittings[0]
    assert fitting.metadata_status(('k',))=={
        'status':'UNVERIFIED','missing':['k'],'origins':{'k':'legacy_default'}}
    assert restored.pipes[0].dimension_metadata()==model.pipes[0].dimension_metadata()


def test_supplied_k_does_not_claim_missing_fitting_geometry_is_known():
    fitting=Fitting(k=.9)
    assert fitting.metadata_status(('k',))['status']=='PASS'
    geometry=fitting.metadata_status(('angle','radius'))
    assert geometry['status']=='UNVERIFIED'
    assert geometry['missing']==['angle','radius']
    assert fitting.approved is False


def test_assumption_requires_explicit_redeclaration_to_become_supplied():
    pipe=minimal_pipe()
    payload=pipe.model_dump(mode='json')
    payload.update(nominal_diameter=.1,outer_diameter=.12,inner_diameter=.1)
    still_unknown=Pipe.model_validate(payload)
    assert still_unknown.dimension_metadata()['status']=='UNVERIFIED'
    payload['metadata_missing']=[]
    supplied=Pipe.model_validate(payload)
    assert supplied.dimension_metadata()['status']=='PASS'
    assert pipe.dimension_metadata()['status']=='UNVERIFIED'  # No mutation of the source.


def test_inference_does_not_mutate_user_input_or_weaken_numeric_validation():
    raw={'id':'p','system_id':'s','points':[[1,1,1],[2,1,1]],'metadata_missing':[]}
    before=copy.deepcopy(raw)
    Pipe.model_validate(raw)
    assert raw==before
    for changes in [{'outer_diameter':-1},{'inner_diameter':.3,'outer_diameter':.1},
                    {'metadata_missing':['fabricated_field']}]:
        with pytest.raises(ValidationError):minimal_pipe(**changes)
    with pytest.raises(ValidationError):Fitting(k=-1)
    with pytest.raises(ValueError,match='Unknown engineering metadata field'):
        Fitting().metadata_status(('approved',))  # Input-presence tracking cannot establish approval.


def test_session_cache_preserves_unknown_dimensions_and_default_k_after_reopening(tmp_path):
    from backend.edge_store import SessionStore
    pipe=minimal_pipe(points=[(1,1,1),(4,1,1),(4,4,1)],inner_diameter=.1,
                      flow_rate=.00001,density=1000,viscosity=.001,roughness=.00001)
    model=Model(id='cad-cache',name='Missing CAD metadata',bounds=Bounds(min=(0,0,0),max=(6,6,6)),pipes=[pipe])
    path=tmp_path/'provenance.sqlite3'
    store=SessionStore(path)
    session=store.create('unknown.dwg',model,source_adapter='dxf')
    result,cache=store.incremental_analysis(store.capture(session['id']))
    assert result['metrics'][pipe.id]['hydraulics']['status']=='UNVERIFIED'
    store.publish_analysis(session['id'],session['revision'],result,cache)
    reopened=SessionStore(path)
    capture=reopened.capture(session['id'])
    persisted=Model.model_validate(capture['model']).pipes[0]
    assert persisted.dimension_metadata()==pipe.dimension_metadata()
    assert persisted.fittings[0].metadata_status()==pipe.fittings[0].metadata_status()
    reused,_=reopened.incremental_analysis(capture)
    assert reused['cache']['reused']==1 and reused['cache']['recomputed']==0
    assert reused['metrics']==result['metrics']
