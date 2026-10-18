"""Independent finite-body, local support contact and cache safety regressions."""
from copy import deepcopy
import math

import pytest

from pipe_router.network_components import component_geometry, connection_contact_radius
from pipe_router.network_validation import validate_network, validation_delta
from pipe_router import network_fittings
from pipe_router.network_fittings import fitting_envelopes
from test_network_engineering_v5 import network, check


def flange_source():
    source=network()
    source['nodes'][0].update(type='flange',axis=[1,0,0],dimensions={'body_radius_mm':100,'body_length_mm':40})
    return source


def test_finite_axial_body_collision_is_not_replaced_by_center_sphere():
    source=network()
    source['nodes'][0].update(type='valve',axis=[1,0,0],dimensions={'body_radius_mm':30,'body_length_mm':1000})
    # The far valve body is 450mm from its graph center, outside its old sphere.
    source['obstacles']=[{'id':'axial-body-only','kind':'structure','min':[940,1020,990],'max':[960,1040,1010]}]
    result=validate_network(source,reference=source)
    assert check(result,'collision.fitting_envelopes')['status']=='FAIL'
    assert any(row.get('id')=='a' and row.get('obstacle_id')=='axial-body-only' for row in check(result,'collision.fitting_envelopes')['evidence']['violations'])


def test_flange_installation_offset_is_separate_from_shock_clearance():
    source=flange_source()
    source['nodes'][0]['constraints']={'installation_clearance_mm':75}
    source['obstacles']=[{'id':'bolt-access','kind':'equipment','min':[480,1160,980],'max':[520,1180,1020]}]
    result=validate_network(source,reference=source)
    assert check(result,'clearance.shock')['status']=='PASS'
    row=check(result,'components.installation_clearance')
    assert row['status']=='FAIL'
    assert row['evidence']['violations'][0]['measured_minimum_mm']==pytest.approx(60)
    assert row['evidence']['violations'][0]['required_mm']==75


def test_missing_flange_tool_requirement_stays_unverified():
    result=validate_network(flange_source(),reference=flange_source())
    assert check(result,'components.installation_clearance')['status']=='UNVERIFIED'
    assert not result['manufacturability_verified']


def test_finite_flange_body_hits_other_pipe_when_both_centerlines_miss():
    source=flange_source()
    other=deepcopy(source['spans'][0]);other.update(id='q',from_node='c',to_node='d',points=[[200,1080,1000],[2000,1080,1000]])
    source['spans'].append(other)
    source['nodes'].extend([{'id':identifier,'type':'endpoint','system_id':'s','nominal_diameter_mm':50,
                             'dimensions':{'body_radius_mm':30},'position':point} for identifier,point in [('c',other['points'][0]),('d',other['points'][-1])]])
    result=validate_network(source,reference=source)
    assert check(result,'collision.fitting_envelopes')['status']=='FAIL'
    assert any(row.get('id')=='a' and row.get('other_id')=='q' for row in check(result,'collision.fitting_envelopes')['evidence']['violations'])


def test_undersized_override_fails_and_retains_known_body_collision():
    source=flange_source()
    source['nodes'][0]['physical_envelope']={'representation':'aabb','min':[490,990,990],'max':[510,1010,1010],
                                           'source':'synthetic inconsistent envelope','conservative':True}
    source['obstacles']=[{'id':'known-body','kind':'structure','min':[495,1080,995],'max':[505,1090,1005]}]
    geometry=component_geometry(source)[0]
    assert geometry['status']=='FAIL'
    assert geometry['max'][1]>=1100
    result=validate_network(source,reference=source)
    assert check(result,'components.physical_geometry')['status']=='FAIL'
    assert check(result,'collision.fitting_envelopes')['status']=='FAIL'


def test_component_body_cannot_be_smaller_than_its_preserved_pipe_connection():
    source=flange_source();source['nodes'][0]['dimensions']['body_radius_mm']=20
    result=validate_network(source,reference=source)
    row=check(result,'components.physical_geometry')
    assert row['status']=='FAIL'
    assert any(issue['code']=='component_body_smaller_than_known_connection' for issue in row['evidence']['violations'][0]['issues'])
    assert source['spans'][0]['outer_diameter_mm']==60


def test_movable_explicit_envelope_uses_immutable_capture_anchor():
    source=network()
    source['nodes'][0].update(type='valve',fixed=False,position=[800,1000,1000],dimensions={},
                            physical_envelope={'representation':'aabb','min':[450,950,950],'max':[550,1050,1050],
                                               'anchor':[500,1000,1000],'source':'synthetic captured body','conservative':True})
    geometry=component_geometry(source)[0]
    assert geometry['min']==pytest.approx([750,950,950])
    assert geometry['max']==pytest.approx([850,1050,1050])


@pytest.mark.parametrize('declaration',[False,None])
def test_unconfirmed_explicit_envelope_does_not_claim_conservatism(declaration):
    source=network();source['nodes'][0].update(type='valve',dimensions={})
    envelope={'representation':'aabb','min':[450,950,950],'max':[550,1050,1050],'source':'synthetic unresolved envelope'}
    if declaration is not None:envelope['conservative']=declaration
    source['nodes'][0]['physical_envelope']=envelope
    geometry=component_geometry(source)[0]
    assert geometry['conservative'] is declaration
    assert geometry['conservative_basis']=='unconfirmed'
    assert geometry['status']=='UNVERIFIED'


def test_missing_component_location_is_not_fabricated_at_global_origin():
    source=network();source['supports']=[{'id':'missing','type':'support','dimensions':{'body_radius_mm':20,'body_length_mm':200},'axis':[0,0,1]}]
    geometry=component_geometry(source)[0]
    assert geometry['position'] is None
    assert geometry['position_basis']=='unknown'
    assert geometry['min'] is None
    assert geometry['status']=='UNVERIFIED'


def test_support_envelope_can_have_a_calculated_center_without_claiming_connection():
    source=network();source['supports']=[{'id':'only-envelope','type':'support','physical_envelope':
        {'representation':'capsules','capsules':[{'start':[2000,1000,1000],'end':[2000,1000,1400],'radius_mm':10}],
         'source':'synthetic support envelope','conservative':True}}]
    geometry=component_geometry(source)[0]
    assert geometry['position']==pytest.approx([2000,1000,1200])
    assert geometry['position_basis']=='calculated_envelope_center'


def reducer_source():
    source=network();source['nodes'][1].update(type='reducer',axis=[1,0,0],dimensions={'body_radius_mm':100,'body_length_mm':200})
    source['nodes'].append({'id':'c','type':'endpoint','system_id':'s','position':[8000,1000,1000],
                            'nominal_diameter_mm':80,'dimensions':{'body_radius_mm':50}})
    span=deepcopy(source['spans'][0]);span.update(id='q',from_node='b',to_node='c',points=[[4500,1000,1000],[8000,1000,1000]],nominal_diameter_mm=80,outer_diameter_mm=100)
    source['spans'].append(span)
    source['nodes'][1]['constraints']={'port_nominal_diameters_mm':{'p':50,'q':80},'port_outer_diameters_mm':{'p':60,'q':100}}
    return source


def test_reducer_needs_explicit_permission_and_never_resizes_pipes():
    source=reducer_source()
    assert check(validate_network(source,reference=source),'components.reducer_compatibility')['status']=='UNVERIFIED'
    source['nodes'][1]['constraints'].update(reducer_approved=False,reducer_source='synthetic designer refusal')
    assert check(validate_network(source,reference=source),'components.reducer_compatibility')['status']=='FAIL'
    source['nodes'][1]['constraints'].update(reducer_approved=True,reducer_source='synthetic supplied transition permission')
    before=deepcopy(source)
    assert check(validate_network(source,reference=source),'components.reducer_compatibility')['status']=='PASS'
    assert source==before
    assert [span['outer_diameter_mm'] for span in source['spans']]==[60,100]


def test_support_exception_is_local_and_returning_attached_pipe_still_collides():
    source=network([[500,1000,1000],[4500,1000,1000],[4500,4000,1000],[4500,4000,2000],
                    [2500,4000,2000],[2500,1000,2000],[2500,1000,1500]])
    source['supports']=[{'id':'hanger','type':'support','span_id':'p','position':[2500,1000,1300],'axis':[0,0,1],
                         'dimensions':{'body_radius_mm':25,'body_length_mm':600},
                         'contact_position':[2500,1000,1000],'contact_radius_mm':200}]
    result=validate_network(source,reference=source)
    assert check(result,'components.support_attachment')['status']=='PASS'
    assert any(row.get('id')=='support:hanger' and row.get('other_id')=='p' for row in check(result,'collision.fitting_envelopes')['evidence']['violations'])


def test_cached_geometry_is_isolated_and_catalog_changes_invalidate():
    network_fittings.clear_geometry_cache()
    source=network([[500,1000,1000],[2500,1000,1000],[2500,3000,1000]])
    span=source['spans'][0]
    first=fitting_envelopes(span);first['fittings'][0]['tangent_start'][0]=-999
    assert fitting_envelopes(span)['fittings'][0]['tangent_start']==pytest.approx([2300,1000,1000])
    assert network_fittings.geometry_cache_stats()['hits']>=1
    span['fitting_catalog']['radius_mm']=220
    assert fitting_envelopes(span)['fittings'][0]['tangent_start']==pytest.approx([2280,1000,1000])
    network_fittings.clear_geometry_cache()


def test_physical_cache_never_reuses_validation_after_obstacle_change():
    network_fittings.clear_geometry_cache()
    source=network([[500,1000,1000],[2500,1000,1000],[2500,3000,1000]])
    assert check(validate_network(source,reference=source),'collision.fitting_envelopes')['status']=='PASS'
    x,y=2300+200/math.sqrt(2),1200-200/math.sqrt(2)
    source['obstacles']=[{'id':'new-on-arc','kind':'structure','min':[x-1,y-1,999],'max':[x+1,y+1,1001]}]
    assert check(validate_network(source,reference=source),'collision.fitting_envelopes')['status']=='FAIL'
    assert network_fittings.geometry_cache_stats()['hits']>0


def test_geometry_cache_has_both_count_and_retained_byte_limits(monkeypatch):
    network_fittings.clear_geometry_cache()
    monkeypatch.setattr(network_fittings,'MAX_FITTING_CACHE_ENTRIES',2)
    monkeypatch.setattr(network_fittings,'MAX_FITTING_CACHE_BYTES',100_000)
    for offset in range(8):
        fitting_envelopes({'id':str(offset),'points':[[0,0,0],[1000+offset,0,0],[1000+offset,1000,0]],
                          'outer_diameter_mm':60,'fitting_catalog':{'radius_mm':200,'body_radius_mm':35,'angles_deg':[90]}})
    stats=network_fittings.geometry_cache_stats()
    assert stats['entries']<=2
    assert stats['retained_bytes']<=100_000
    network_fittings.clear_geometry_cache()


def test_violation_delta_distinguishes_preexisting_from_introduced_pairs():
    def report(rows):return {'checks':[{'id':'collision.pipe_to_structure','status':'FAIL','severity':'hard','evidence':{'violations':rows}}]}
    before=report([{'id':'p','obstacle_id':'wall','surface_gap_mm':-10}])
    after=report([{'id':'p','obstacle_id':'wall','surface_gap_mm':-20},{'id':'q','obstacle_id':'wall','surface_gap_mm':-5}])
    delta=validation_delta(before,after)
    assert len(delta['persistent'])==1
    assert len(delta['introduced'])==1
    assert delta['new_hard_failures']==1
    assert delta['introduced'][0]['evidence']['id']=='q'


def test_span_broadphase_prunes_remote_pairs_without_losing_exact_minimum():
    source=network();source['nodes']=[];source['spans']=[]
    for index in range(20):
        points=[[500,500+150*index,1000],[4500,500+150*index,1000]]
        for side,point in enumerate(points):
            source['nodes'].append({'id':f'n{index}-{side}','type':'endpoint','system_id':'s','position':point,
                                    'nominal_diameter_mm':50,'dimensions':{'body_radius_mm':30}})
        source['spans'].append({'id':f'p{index}','system_id':'s','system_type':'pressurized',
                                'from_node':f'n{index}-0','to_node':f'n{index}-1','points':points,
                                'nominal_diameter_mm':50,'outer_diameter_mm':60})
    result=validate_network(source,reference=source)
    assert check(result,'clearance.shock')['evidence']['minimum_surface_gap_mm']==pytest.approx(90)
    assert result['spatial_index']['pruned_span_pairs']>150
    assert result['work_budget']['narrowphase_pairs']<100


def test_oversized_contact_declaration_cannot_hide_shared_junction_pipe_collision():
    source=network([[1000,1000,1000],[6000,1000,1000]])
    source['nodes'][0].update(type='junction',axis=[1,0,0],dimensions={'body_radius_mm':35,'body_length_mm':100},
                              constraints={'contact_radius_mm':1000},
                              fitting_catalog={'status':'verified','available':True,'source':'synthetic audit probe'})
    source['nodes'].append({'id':'c','type':'endpoint','system_id':'s','position':[6000,2000,1000],
                            'nominal_diameter_mm':50,'dimensions':{'body_radius_mm':30}})
    other=deepcopy(source['spans'][0]);other.update(id='q',to_node='c',points=[[1000,1000,1000],[6000,2000,1000]],cad_handle='A2')
    source['spans'].append(other)
    result=validate_network(source,reference=source)
    assert check(result,'collision.pipe_to_pipe')['status']=='FAIL'
    assert check(result,'clearance.shock')['status']=='FAIL'
    assert not result['geometrically_feasible']
    assert check(result,'collision.pipe_to_pipe')['evidence']['bounded_port_exemptions'][0]['radius_mm']==pytest.approx(110.8)


def test_legitimate_physical_connection_body_can_authorize_larger_local_contact():
    source=network()
    node=source['nodes'][0]
    node.update(type='junction',dimensions={'body_radius_mm':200},constraints={'contact_radius_mm':250})
    other=deepcopy(source['spans'][0])
    assert connection_contact_radius(node,source['spans'][0],other,50.8)==250


def test_long_slender_body_cannot_authorize_empty_offaxis_contact_sphere():
    source=network([[1000,1000,1000],[1000,2400,1000],[6000,2400,1000]])
    source['nodes'][0].update(type='junction',axis=[1,0,0],dimensions={'body_radius_mm':35,'body_length_mm':2000},
                              constraints={'contact_radius_mm':1000},
                              fitting_catalog={'status':'verified','available':True,'source':'synthetic slender-body audit probe'})
    points=[[1000,1000,1000],[1000,1000,1600],[1000,1800,1600],[1000,1800,1000],[1000,1600,1000]]
    source['nodes'].append({'id':'c','type':'endpoint','system_id':'s','position':points[-1],
                            'nominal_diameter_mm':50,'dimensions':{'body_radius_mm':30}})
    other=deepcopy(source['spans'][0]);other.update(id='q',to_node='c',points=points,cad_handle='A2')
    source['spans'].append(other)
    result=validate_network(source,reference=source)
    assert check(result,'collision.pipe_to_pipe')['status']=='FAIL'
    assert not result['geometrically_feasible']
    assert check(result,'collision.pipe_to_pipe')['evidence']['bounded_port_exemptions'][0]['radius_mm']==pytest.approx(110.8)


def test_support_body_must_reach_its_declared_pipe_contact():
    source=network()
    source['supports']=[{'id':'detached','type':'support','span_id':'p','position':[2500,3000,1000],'axis':[0,0,1],
                         'dimensions':{'body_radius_mm':20,'body_length_mm':200},
                         'contact_position':[2500,1000,1000],'contact_radius_mm':100}]
    result=validate_network(source,reference=source)
    row=check(result,'components.support_attachment')
    assert row['status']=='FAIL'
    assert row['evidence']['violations'][0]['body_to_contact_gap_mm']==pytest.approx(1980)
    assert not result['geometrically_feasible']


def test_unknown_support_body_cannot_verify_attachment_reach():
    source=network();source['supports']=[{'id':'unknown','type':'support','span_id':'p',
                                          'position':[2500,1000,1000],'contact_position':[2500,1000,1000],'contact_radius_mm':100}]
    result=validate_network(source,reference=source)
    assert check(result,'components.support_attachment')['status']=='UNVERIFIED'


def test_support_contact_does_not_authorize_a_returning_leg_inside_same_ball():
    source=network([[500,1000,1000],[4500,1000,1000],[4500,4000,1000],[4500,4000,2120],
                    [2500,4000,2120],[2500,4000,1120],[2500,1000,1120]])
    source['supports']=[{'id':'wide-hanger','type':'support','span_id':'p','position':[2500,1000,1300],'axis':[0,0,1],
                         'dimensions':{'body_radius_mm':100,'body_length_mm':600},
                         'contact_position':[2500,1000,1000],'contact_radius_mm':200}]
    result=validate_network(source,reference=source)
    assert check(result,'components.support_attachment')['status']=='PASS'
    assert any(row.get('id')=='support:wide-hanger' and row.get('other_id')=='p' for row in check(result,'collision.fitting_envelopes')['evidence']['violations'])
