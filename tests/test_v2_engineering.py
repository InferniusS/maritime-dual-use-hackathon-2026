import io,math,threading
import ezdxf
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from backend.app import app
from backend.models import Model,Fitting,Obstacle,Pipe,Bounds
from backend.scenarios import get_scenario
from backend.optimizer import optimize,point_segment_distance
from backend.advanced import run_optimization
from backend.validation import validate,violations
from backend.exports import export_data,import_dxf
from backend.geometry import segment_distance

def checked(original,result):
    assert result['candidates'][0]['status']=='accepted'
    proposed=Model.model_validate(result['model'])
    assert not any(r['status']=='FAIL' for r in validate(original,proposed,original.pipes[0].id))
    return proposed

@pytest.mark.parametrize('id,sign',[('gravity-detour',1),('vent-rise',-1)])
def test_real_obstructed_pitch_detour(id,sign):
    original=get_scenario(id)
    assert any(i['type']=='collision' for i in violations(original,original.pipes[0]))
    result=optimize(original);proposed=checked(original,result)
    pipe=proposed.pipes[0]
    assert len(pipe.points)>2
    assert result['candidates'][0]['expanded']>0
    assert pipe.points[0]==original.pipes[0].points[0]
    assert pipe.points[-1]==original.pipes[0].points[-1]
    assert proposed.pipes[1:]==original.pipes[1:]
    for a,b in zip(pipe.points,pipe.points[1:]):
        assert (a[2]-b[2])*sign+1e-8>=pipe.min_slope*math.hypot(b[0]-a[0],b[1]-a[1])
    assert not result['issues']

@pytest.mark.parametrize('change',['endpoint','reverse','trap'])
def test_gravity_adversaries_rejected_per_segment(change):
    model=get_scenario('gravity-detour');p=model.pipes[0]
    if change=='endpoint':
        p.points[-1]=(9,3,3.1)
        assert optimize(model)['candidates'][0]['status']=='no_route'
    elif change=='reverse':
        p.points=[(1,1,3),(3,1,2.5),(5,1,2.8),(9,1,2.4)]
        assert any(i['type']=='slope' for i in violations(model,p))
    else:
        p.points=[(1,1,3),(4,1,2.2),(9,1,2.4)]
        assert any(i['type']=='slope' for i in violations(model,p))

def test_insufficient_pitch_budget_for_detour_is_not_feasible():
    model=get_scenario('gravity-detour');model.pipes[0].min_slope=.07
    result=optimize(model)
    assert result['candidates'][0]['status']=='no_route'
    assert result['model']==model.model_dump()

@pytest.mark.parametrize('change',['diameter','specification','angle','tangent'])
def test_incompatible_catalog_rejects_construction(change):
    model=get_scenario('gravity-detour');p=model.pipes[0]
    p.points=[(1,1,3),(1,5,2.8),(9,5,2.4)]
    if change=='diameter':p.fittings[0].nominal_diameters=[.2]
    elif change=='specification':p.fittings[0].pipe_specifications=['OTHER']
    elif change=='angle':p.fittings[0].angle=45
    else:p.fittings[0].minimum_tangent=10
    rows=validate(model,model,p.id)
    assert next(r for r in rows if r['requirement']=='fitting_geometry')['status']=='FAIL'


def test_declared_angle_tolerance_is_not_an_approved_fitting_claim():
    model=get_scenario('gravity-detour');proposed=checked(model,optimize(model))
    rows=validate(model,proposed,model.pipes[0].id)
    assert next(r for r in rows if r['requirement']=='commercial_fittings')['status']=='UNVERIFIED'
    assert next(r for r in rows if r['requirement']=='catalog_size_metadata')['status']=='PASS'


def test_clearance_numerical_evidence_matches_requirement():
    model=get_scenario('efficient');model.obstacles=[Obstacle(id='structure',min=(3,2,0),max=(4,3,2))]
    row=next(r for r in validate(model,model,'P-101',.2) if r['requirement']=='collision_clearance')
    assert row['measured']==pytest.approx(1-model.pipes[0].outer_diameter/2)
    assert row['required']==.2
    assert row['status']=='PASS'
    assert row['clearance_evidence'][0]['object_ids']==['P-101','structure']
    assert row['status_code']=='PASS'


def test_distinct_alternatives_and_empirical_frontier_are_independently_valid():
    original=get_scenario('congested')
    result=run_optimization(original,alternatives=True,multiresolution=True,resolution=1)
    assert len(result['alternatives'])>=2
    geometries=set()
    for c in result['alternatives']:
        model=Model.model_validate(c['model']);geometries.add(str(c['model']['pipes']))
        assert not any(r['status']=='FAIL' for r in validate(original,model,'P-101'))
        assert c['metrics']['length']==pytest.approx(sum(sum(math.dist(a,b) for a,b in zip(p.points,p.points[1:])) for p in model.pipes))
        dims=result['frontier']['dimensions']
        dominated=any(all(d['metrics'][k]<=c['metrics'][k]+1e-9 for k in dims) and any(d['metrics'][k]<c['metrics'][k]-1e-9 for k in dims) for d in result['alternatives'] if d is not c)
        assert c['nondominated'] is not dominated
    assert len(geometries)==len(result['alternatives'])
    assert 'Approximate' in result['frontier']['label']
    assert any(a['resolution']==2 for a in result['exploration'])
    assert any(a['resolution']==1 for a in result['exploration'])


def test_two_system_order_search_preserves_dimensions_and_avoids_final_clashes():
    original=get_scenario('multi-system')
    result=run_optimization(original,try_orders=True,resolution=1)
    model=Model.model_validate(result['model'])
    assert len({tuple(a['order']) for a in result['exploration']})==2
    assert not result['issues']
    for p in model.pipes:
        old=next(q for q in original.pipes if p.id==q.id)
        assert p.outer_diameter==old.outer_diameter
        assert p.points[0]==old.points[0] and p.points[-1]==old.points[-1]
        assert not violations(model,p)


def test_duplicate_selection_rejected():
    with pytest.raises(ValueError):run_optimization(get_scenario('efficient'),pipe_ids=['P-101','P-101'])


def test_dxf_includes_environment_and_changed_original_and_rejects_tampering():
    original=get_scenario('inefficient');proposed=checked(original,optimize(original))
    text,_=export_data(original,proposed,'dxf');doc=ezdxf.read(io.StringIO(text))
    assert len(doc.modelspace().query('3DFACE'))==6*len(proposed.obstacles)
    assert any(e.dxf.layer=='ORIGINAL_P-101' for e in doc.modelspace().query('POLYLINE'))
    assert import_dxf(text,proposed)==proposed
    face=next(iter(doc.modelspace().query('3DFACE')));face.dxf.vtx0=(100,100,100)
    out=io.StringIO();doc.write(out)
    with pytest.raises(ValueError,match='obstacle dimensions'):import_dxf(out.getvalue(),proposed)


def test_dxf_xdata_size_disagreement_rejected():
    model=get_scenario('efficient');text,_=export_data(model,model,'dxf');doc=ezdxf.read(io.StringIO(text));e=next(iter(doc.modelspace().query('POLYLINE')))
    e.set_xdata('PIPEFORGE',[(1000,'P-101'),(1000,model.pipes[0].system_id),(1040,.1),(1040,99.)]);out=io.StringIO();doc.write(out)
    with pytest.raises(ValueError,match='diameter metadata'):import_dxf(out.getvalue(),model)


def test_analysis_honors_explicit_clearance_and_catalog_import_is_typed():
    client=TestClient(app);model=get_scenario('efficient');model.obstacles=[Obstacle(id='nearby',min=(2,1.3,.5),max=(3,2,2))]
    low=client.post('/api/analyze',json={'model':model.model_dump(),'clearance':.1}).json()
    high=client.post('/api/analyze',json={'model':model.model_dump(),'clearance':.5}).json()
    assert len(high['issues'])>len(low['issues'])
    assert client.post('/api/catalog/validate',json={'fittings':[{'radius':-1}]}).status_code==422
    assert client.post('/api/catalog/validate',json={'fittings':[Fitting().model_dump()]}).status_code==200


def test_cached_point_distance_matches_independent_segment_reference():
    for point in [(0,0,0),(1,1,1),(9,3,5)]:
        assert point_segment_distance(point,(1,2,3),(4,5,6))==pytest.approx(segment_distance(point,point,(1,2,3),(4,5,6)))


def test_no_catalog_fitting_available_never_accepts_obstructed_gravity_detour():
    model=get_scenario('gravity-detour');model.pipes[0].fittings=[]
    result=optimize(model)
    assert result['candidates'][0]['status']=='no_route'


def test_centerline_clear_but_diameter_and_clearance_close_corridor():
    p=Pipe(id='wide',system_id='water',editable=True,outer_diameter=.4,points=[(1,2,1),(9,2,1)])
    model=Model(id='narrow',name='Narrow corridor',bounds=Bounds(min=(0,0,0),max=(10,4,3)),pipes=[p],obstacles=[Obstacle(id='left',min=(3,0,0),max=(7,1.85,3)),Obstacle(id='right',min=(3,2.15,0),max=(7,4,3))])
    result=optimize(model,resolution=1)
    assert result['candidates'][0]['status']=='no_route'
    assert any(i['type']=='collision' for i in violations(model,p))


def test_no_pipe_membership_can_be_added_through_validation():
    model=get_scenario('efficient');proposal=model.model_copy(deep=True)
    proposal.pipes.append(Pipe(id='added',system_id='unknown',points=[(1,7,1),(3,7,1)]))
    assert next(r for r in validate(model,proposal,'P-101') if r['requirement']=='pipe_membership')['status']=='FAIL'
    missing=validate(model,proposal,'absent')[0]
    assert missing['status_code']=='FAIL' and missing['object_ids']==['absent']
    assert missing['severity']=='error'


def test_uphill_gravity_drain_cannot_be_enabled_by_relabeling_pitch():
    with pytest.raises(ValidationError,match='Gravity drainage must fall'):
        Pipe(id='invalid',system_id='drain',category='drainage',slope_direction='upward',points=[(1,1,1),(5,1,2)],min_slope=.02)


def test_issue_marker_is_a_geometric_witness_rather_than_pipe_start():
    model=get_scenario('congested')
    issue=next(i for i in violations(model,model.pipes[0]) if i['type']=='collision')
    assert 4<=issue['location'][0]<=6
    assert issue['location']!=model.pipes[0].points[0]


def test_spatial_index_never_discards_true_narrow_phase_candidates():
    import numpy as np
    from backend.spatial import SpatialCandidates
    from backend.geometry import segment_box_distance
    rng=np.random.default_rng(481)
    boxes=[(tuple(a),tuple(a+rng.uniform(.1,3,3))) for a in rng.uniform(0,15,(50,3))]
    index=SpatialCandidates(boxes)
    for a,b in zip(rng.uniform(0,15,(15,3)),rng.uniform(0,15,(15,3))):
        returned=set(index.query(a,b,.2))
        assert all(i in returned for i,(lo,hi) in enumerate(boxes) if segment_box_distance(a,b,lo,hi)<.2)


def test_progress_is_monotone_across_multi_system_order_attempts():
    observed=[]
    result=run_optimization(get_scenario('multi-system'),resolution=1,try_orders=True,progress=observed.append)
    assert observed==sorted(observed)
    assert observed[-1]==sum(a['expanded'] for a in result['exploration'])


def test_changed_segment_instructions_and_report_measurements_are_precise():
    import json
    original=get_scenario('inefficient');proposal=checked(original,optimize(original))
    data=json.loads(export_data(original,proposal,'json')[0]);change=data['changes'][0]
    assert change['removed_segments'] and change['added_segments']
    assert change['added_segments'][0]['start']==list(proposal.pipes[0].points[0])
    assert 'measured=' in export_data(original,proposal,'report')[0]
