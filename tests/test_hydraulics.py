import pytest
from backend.models import Pipe,Fitting
from backend.hydraulics import hydraulic


def known_pipe(**changes):
    values=dict(id='p',system_id='water',points=[(0,0,0),(2,0,0)],
                inner_diameter=.1,outer_diameter=.12,flow_rate=.00001,
                density=1000,viscosity=.001,roughness=.00001)
    values.update(changes)
    return Pipe(**values)


def test_laminar_darcy_weisbach_and_route_length_scaling():
    p=known_pipe()
    result=hydraulic(p)
    assert result['status']=='PASS'
    assert result['friction_factor']==pytest.approx(64/result['reynolds'])
    expected=result['friction_factor']*(2/.1)*(1000*result['velocity']**2/2)
    assert result['pressure_loss_pa']==pytest.approx(expected)
    longer=p.model_copy(update={'points':[(0,0,0),(4,0,0)]})
    assert hydraulic(longer)['pressure_loss_pa']==pytest.approx(2*result['pressure_loss_pa'])


def test_missing_operating_conditions_remain_unverified():
    assert hydraulic(known_pipe(density=None))['status']=='UNVERIFIED'


def test_drainage_pitch_does_not_prove_hydraulic_capacity():
    assert hydraulic(known_pipe(category='drainage',min_slope=.02))['status']=='UNVERIFIED'


def test_zero_flow_has_zero_loss():
    assert hydraulic(known_pipe(flow_rate=0))['pressure_loss_pa']==0


def test_actual_bend_does_not_treat_default_catalog_k_as_supplied():
    pipe=known_pipe(points=[(0,0,0),(2,0,0),(2,2,0)])
    assert pipe.fittings[0].k==.9  # Retained illustrative geometry/catalog default.
    result=hydraulic(pipe)
    assert result['status']=='UNVERIFIED'
    assert result['missing_fields']==['fittings[DEMO-90].k']
    assert 'pressure_loss_pa' not in result
    restored=Pipe.model_validate_json(pipe.model_dump_json())
    assert hydraulic(restored)==result


@pytest.mark.parametrize('coefficient',[0.,.9,2.4])
def test_explicit_unapproved_coefficient_is_math_input_without_catalog_approval(coefficient):
    pipe=known_pipe(points=[(0,0,0),(2,0,0),(2,2,0)],fittings=[
        Fitting(k=coefficient,approved=False,provenance='Test engineering input; supplier approval absent')])
    result=hydraulic(pipe)
    assert result['status']=='PASS'
    expected=(result['friction_factor']*(4/.1)+coefficient)*(1000*result['velocity']**2/2)
    assert result['pressure_loss_pa']==pytest.approx(expected)
    assert result['fitting_coefficients']==[{
        'fitting_id':'DEMO-90','k':coefficient,'input_origin':'supplied','catalog_approved':False,
        'provenance':'Test engineering input; supplier approval absent'}]


def test_straight_route_does_not_require_unused_bend_coefficients():
    pipe=known_pipe(fittings=[])
    result=hydraulic(pipe)
    assert result['status']=='PASS'
    assert result['fitting_coefficients']==[]


def test_zero_flow_does_not_promote_unknown_bend_coefficient_to_verified():
    pipe=known_pipe(flow_rate=0,points=[(0,0,0),(2,0,0),(2,2,0)])
    assert hydraulic(pipe)['status']=='UNVERIFIED'


def test_assumed_bore_remains_unverified_despite_numeric_value():
    pipe=known_pipe(metadata_missing=['inner_diameter'])
    result=hydraulic(pipe)
    assert result['status']=='UNVERIFIED'
    assert result['missing_fields']==['inner_diameter']
