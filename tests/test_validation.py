import pytest
from backend.models import Bounds, Model, Obstacle, Pipe
from backend.validation import analyze, validate
from backend.scenarios import get_scenario


def fixture_model(points=None, **pipe_changes):
    pipe=Pipe(id='p',system_id='water',editable=True,outer_diameter=.2,
              points=points or [(1,1,1),(5,1,1)],**pipe_changes)
    return Model(id='test',name='Test',bounds=Bounds(min=(0,0,0),max=(8,8,8)),pipes=[pipe])


def statuses(original,proposed):
    return {row['requirement']:row['status'] for row in validate(original,proposed,'p')}


def test_finite_pipe_radius_clashes_even_when_centerlines_do_not_intersect():
    model=fixture_model()
    model.pipes.append(Pipe(id='large',system_id='other',outer_diameter=.8,
                            points=[(1,1.4,1),(5,1.4,1)]))
    assert any(i['type']=='pipe_collision' for i in analyze(model)['issues'])


def test_clearance_checks_surface_gap_not_centerline_distance():
    model=fixture_model()
    model.obstacles=[Obstacle(id='duct',kind='hvac',min=(2,1.15,.5),max=(3,2,2))]
    issues=analyze(model,clearance=.1)['issues']
    assert any(i['type']=='clearance' for i in issues)
    assert not any(i['type']=='collision' for i in issues)


def test_containment_checks_entire_envelope():
    model=fixture_model(points=[(.05,1,1),(.05,5,1)])
    assert any(i['type']=='containment' for i in analyze(model)['issues'])


@pytest.mark.parametrize('field,value,requirement', [
    ('outer_diameter',.25,'size_preservation'),
    ('nominal_diameter',.2,'size_preservation'),
    ('points',[(1,1,1),(6,1,1)],'endpoints'),
    ('system_id','unauthorized-system','system_identity'),
])
def test_validator_rejects_mutated_invariants(field,value,requirement):
    original=fixture_model()
    proposed=original.model_copy(deep=True)
    setattr(proposed.pipes[0],field,value)
    assert statuses(original,proposed)[requirement]=='FAIL'


def test_fixed_environment_and_other_systems_cannot_move():
    original=fixture_model()
    original.obstacles=[Obstacle(id='engine',min=(2,3,1),max=(3,4,2))]
    original.pipes.append(Pipe(id='fixed',system_id='fire',points=[(1,6,1),(5,6,1)]))
    proposed=original.model_copy(deep=True)
    proposed.obstacles[0].min=(2,2,1)
    proposed.pipes[1].points=[(1,7,1),(5,7,1)]
    rows=statuses(original,proposed)
    assert rows['fixed_environment']=='FAIL'
    assert rows['other_systems_preserved']=='FAIL'


def test_invalid_and_valid_drainage_segments():
    bad=fixture_model(points=[(1,1,3),(5,1,3)],category='drainage',min_slope=.02)
    assert statuses(bad,bad)['functional_slope']=='FAIL'
    good=fixture_model(points=[(1,1,3),(5,1,2.8)],category='drainage',min_slope=.02)
    assert statuses(good,good)['functional_slope']=='PASS'


def test_unknown_standards_and_catalog_are_not_passed():
    model=fixture_model()
    rows=statuses(model,model)
    assert rows['organizer_standards']=='UNVERIFIED'
    assert rows['commercial_fittings']=='UNVERIFIED'


def test_unsupported_fitting_angle_is_rejected():
    model=fixture_model(points=[(1,1,1),(3,1,1),(4,2,1)])
    assert statuses(model,model)['fitting_geometry']=='FAIL'


def test_scenarios_are_fresh_and_deterministic():
    a=get_scenario('congested'); b=get_scenario('congested')
    assert a==b
    a.pipes[0].points[0]=(0,0,0)
    assert a!=b


@pytest.mark.parametrize('field,value',[
    ('flow_rate',.02),('density',850),('viscosity',.004),
    ('roughness',.0002),('editable',False),
])
def test_operating_metadata_and_permissions_cannot_change(field,value):
    original=fixture_model()
    proposed=original.model_copy(deep=True)
    setattr(proposed.pipes[0],field,value)
    assert statuses(original,proposed)['system_identity']=='FAIL'
