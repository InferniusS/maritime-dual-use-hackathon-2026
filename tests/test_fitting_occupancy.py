"""Engineering regressions for existing elbows and obtuse fillet enclosures."""
import math
import pytest
from backend.models import Bounds,Fitting,Model,Obstacle,Pipe
from backend.validation import analyze,validate
from backend.optimizer import optimize
from backend.evidence import clearance_evidence
from backend.fitting_geometry import fitting_extent,fitting_envelopes,conservative_pipe_margin,pipe_pair_clearance


def pipe(id,points,**kwargs):
    return Pipe(id=id,system_id=id,nominal_diameter=.1,outer_diameter=.1,points=points,**kwargs)


def surrounding_elbow():
    x,y=4+math.sqrt(.5),6-math.sqrt(.5)
    fixed=pipe('fixed',[(1,5,5),(5,5,5),(5,9,5)],fittings=[Fitting(angle=90,radius=1,k=.9)])
    moving=pipe('moving',[(x,y,2),(8,y,2),(8,y,8),(x,y,8)],editable=True)
    return Model(id='existing-elbow',name='Safe source route',bounds=Bounds(min=(0,0,0),max=(10,10,10)),pipes=[moving,fixed])


@pytest.mark.parametrize('degrees',[15,45,89,90,120,150,170])
def test_tangent_point_bound_encloses_entire_sampled_fillet(degrees):
    fitting=Fitting(angle=degrees,radius=1.3,connection_length=.07,minimum_tangent=.02)
    theta=math.radians(degrees);tangent=fitting.radius*math.tan(theta/2)
    center=(-tangent,fitting.radius)
    # Analytic circle from incoming tangent at -90deg through the actual turn.
    for step in range(101):
        phi=-math.pi/2+theta*step/100
        point=(center[0]+fitting.radius*math.cos(phi),center[1]+fitting.radius*math.sin(phi))
        assert math.hypot(*point)+.09<=fitting_extent(fitting,degrees)+1e-10


def test_right_angle_bound_retains_existing_allowances():
    fitting=Fitting(angle=90,radius=.15,connection_length=.02,minimum_tangent=.03)
    assert fitting_extent(fitting,90)==pytest.approx(.2)


def test_surrounding_elbow_is_checked_from_both_sides_and_in_evidence():
    original=surrounding_elbow();proposal=original.model_copy(deep=True)
    moving=proposal.pipes[0];moving.points=[moving.points[0],moving.points[-1]]
    rows=validate(original,proposal,moving.id)
    collision=next(r for r in rows if r['requirement']=='collision_clearance')
    assert collision['status']=='FAIL'
    assert any(i['type']=='fitting_envelope' and 'fixed' in i['object_ids'] for i in collision['evidence'])
    own=clearance_evidence(proposal,moving,.1)[0]
    reverse=clearance_evidence(proposal,proposal.pipes[1],.1)[0]
    assert own['status']==reverse['status']=='FAIL'
    assert own['measured']==pytest.approx(reverse['measured'])
    # This is a physical intersection with the circular elbow, not only a
    # centerline crossing or an overlap between conservative bounds.
    witness=(moving.points[0][0],moving.points[0][1],5)
    assert math.dist(witness,(4,6,5))==pytest.approx(1)


def test_optimizer_and_recommendation_do_not_introduce_surrounding_elbow_clash():
    original=surrounding_elbow();source=analyze(original)
    assert not [i for i in source['issues'] if i['severity']=='error']
    assert not any(i['type']=='inefficiency' for i in source['issues'])
    result=optimize(original,pipe_ids=['moving'],mode='shortest',resolution=1)
    proposed=Model.model_validate(result['model'])
    assert proposed.pipes[0].points!=[original.pipes[0].points[0],original.pipes[0].points[-1]]
    assert not [i for i in result['issues'] if i['severity']=='error']
    if result['candidates'][0]['status']=='accepted':
        assert not any(r['status']=='FAIL' for r in validate(original,proposed,'moving'))
        assert not any(r['status']=='FAIL' for r in validate(proposed,proposed,'fixed'))


def test_obtuse_fillet_obstacle_fails_independent_validation_and_evidence():
    theta=math.radians(150);tangent=math.tan(theta/2)
    center=(12-tangent,11,10)
    witness=(center[0]+math.cos(math.radians(-15)),center[1]+math.sin(math.radians(-15)),10)
    p=pipe('obtuse',[(2,10,10),(12,10,10),(12+10*math.cos(theta),10+10*math.sin(theta),10)],fittings=[Fitting(angle=150,radius=1,k=.9)])
    obstacle=Obstacle(id='on-arc',min=tuple(v-.01 for v in witness),max=tuple(v+.01 for v in witness))
    model=Model(id='obtuse',name='Actual fillet obstruction',bounds=Bounds(min=(0,0,0),max=(20,20,20)),pipes=[p],obstacles=[obstacle])
    assert math.dist(witness,center)==pytest.approx(1)
    assert math.dist(witness,p.points[1])>2.8
    assert any(i['type']=='fitting_envelope' for i in analyze(model)['issues'])
    collision=next(r for r in validate(model,model,p.id) if r['requirement']=='collision_clearance')
    assert collision['status']=='FAIL'
    assert collision['clearance_evidence'][0]['status']=='FAIL'
    assert collision['measured']<collision['required']
    assert conservative_pipe_margin(p)==pytest.approx(.05+tangent)
    assert fitting_envelopes(p)[0].radius==pytest.approx(.05+tangent)


def test_two_fitting_enclosures_are_mutual_occupancy():
    offset=.6/math.sqrt(2)
    left=pipe('left',[(1,4,4),(4,4,4),(4,7,4)],fittings=[Fitting(angle=90,radius=.3,k=.9)])
    right=pipe('right',[(7,4-offset,4),(4+offset,4-offset,4),(4+offset,1,4)],fittings=[Fitting(angle=90,radius=.3,k=.9)])
    model=Model(id='two-elbows',name='Conservative sphere pair',bounds=Bounds(min=(0,0,0),max=(10,10,10)),pipes=[left,right])
    assert pipe_pair_clearance(left,right)==pytest.approx(-.1)
    for p in model.pipes:
        row=next(r for r in validate(model,model,p.id) if r['requirement']=='collision_clearance')
        assert row['status']=='FAIL'
        assert any(i['explanation']=='Conservative fitting enclosures overlap' for i in row['evidence'])


def test_verified_obstacle_failure_is_not_hidden_by_unknown_neighbor_dimensions():
    known=pipe('known',[(1,1,1),(5,1,1)])
    unknown=Pipe(id='unknown',system_id='unknown',points=[(1,8,8),(5,8,8)])
    model=Model(id='mixed',name='Known failure and incomplete surroundings',bounds=Bounds(min=(0,0,0),max=(10,10,10)),pipes=[known,unknown],obstacles=[Obstacle(id='wall',min=(2,.5,.5),max=(3,1.5,1.5))])
    collision=next(r for r in validate(model,model,known.id) if r['requirement']=='collision_clearance')
    assert collision['status']=='FAIL'
    assert any(check['status']=='FAIL' and check['object_ids']==['known','wall'] for check in collision['clearance_evidence'])
    assert any(check['status']=='UNVERIFIED' and check['object_ids']==['known','unknown'] for check in collision['clearance_evidence'])


@pytest.mark.parametrize('kind',['obstacle','other_pipe','bounds'])
def test_unknown_diameter_cannot_hide_dimension_independent_failure(kind):
    unknown=Pipe(id='unknown',system_id='unknown',points=[(1,1,1),(5,1,1)])
    model=Model(id='definite',name='Dimension independent violation',bounds=Bounds(min=(0,0,0),max=(10,10,10)),pipes=[unknown])
    requirement='collision_clearance'
    if kind=='obstacle':model.obstacles=[Obstacle(id='wall',min=(2,.5,.5),max=(3,1.5,1.5))]
    elif kind=='other_pipe':model.pipes.append(pipe('crossing',[(3,.5,1),(3,2,1)]))
    else:unknown.points=[(-1,1,1),(5,1,1)];requirement='containment'
    row=next(r for r in validate(model,model,unknown.id) if r['requirement']==requirement)
    assert row['status']=='FAIL'
    assert any(item['dimension_independent_failure'] and item['verification_status']=='verified' for item in row['evidence'])


def test_known_straight_clearance_failure_is_not_hidden_by_unknown_fitting_radius():
    p=pipe('known-size',[(1,1,1),(5,1,1),(5,5,1)])
    model=Model(id='unverified-elbow',name='Known straight separation',bounds=Bounds(min=(0,0,0),max=(10,10,10)),pipes=[p],obstacles=[Obstacle(id='near-straight',min=(2,1.08,.5),max=(3,2,1.5))])
    collision=next(r for r in validate(model,model,p.id) if r['requirement']=='collision_clearance')
    assert collision['status']=='FAIL'
    issue=next(item for item in collision['evidence'] if item['type']=='clearance')
    assert issue['verification_status']=='verified'
    assert issue['measured']==pytest.approx(.03)
