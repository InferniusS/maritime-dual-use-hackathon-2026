"""End-to-end evidence for physical proposals, beyond a centerline smoke test."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from pipe_router.workflow import run_workflow
from pipe_router.proposal import _score

ROOT = Path(__file__).resolve().parents[1]


def case(name):
    return json.loads((ROOT/'examples'/'assembly'/name).read_text())


@pytest.mark.parametrize('filename,status,ready',[
    ('01-congested-room.json','improved',True),
    ('02-collision-repair.json','improved',True),
    ('03-blocked-route.json','no_route',False),
    ('04-elevation-repair.json','improved',True),
    ('05-already-good.json','unchanged',True),
])
def test_public_assembly_cases(filename,status,ready):
    layout=case(filename);result=run_workflow(layout);assembly=result['assembly']
    assert result['status']==status
    assert (assembly['status']=='ready')==ready
    assert assembly['catalog_verified'] is False
    assert result['challenge_review']['complete_compliance_verified'] is False
    if ready:
        assert assembly['meshes']
        assert result['candidate']['points'][0]==layout['pipe']['start']
        assert result['candidate']['points'][-1]==layout['pipe']['end']
        for stub in layout['protected_endpoints']:
            points=result['candidate']['points'];n=len(stub['points'])
            assert (points[:n] if stub['endpoint']=='start' else points[-n:])==stub['points']
        assert not any(f['status']=='fail' for f in assembly['findings'])
    else:
        assert result['candidate'] is None and assembly['meshes']==[]
    json.dumps(result,allow_nan=False)


def test_measurable_detour_saving_and_physical_bend_count():
    result=run_workflow(case('01-congested-room.json'));a=result['assembly']
    assert a['baseline_metrics']['bend_count']==4
    assert a['metrics']['bend_count']==2
    assert a['baseline_metrics']['centerline_length_mm']-a['metrics']['centerline_length_mm']>3000
    assert a['change_type']=='fewer_bends'
    assert a['metrics']['centerline_length_mm']<result['candidate']['length']


def test_vertical_case_must_change_elevation():
    result=run_workflow(case('04-elevation-repair.json'))
    assert result['assembly']['metrics']['elevation_runs']==2
    assert max(p[2] for p in result['candidate']['points'])>2500


def test_full_width_check_cannot_be_bypassed_by_small_centerline():
    layout=case('02-collision-repair.json')
    layout['pipe']['diameter']=2500
    result=run_workflow(layout)
    assert result['status']=='no_route'
    assert result['assembly']['status']=='blocked'


@pytest.mark.parametrize('proposal',[None,[],True,{'mode':'unsupported'},{'mode':'rounded_pipe','bend_radius_mm':float('nan')},{'mode':'rounded_pipe','bend_radius_mm':0}])
def test_bad_options_fail_as_input_error(proposal):
    layout=case('01-congested-room.json');layout['proposal']=proposal
    with pytest.raises(ValueError):run_workflow(layout)


def transform(layout, scale=1, delta=(0,0,0)):
    def point(p):return [p[i]*scale+delta[i] for i in range(3)]
    for k in ('min','max'):layout['bounds'][k]=point(layout['bounds'][k])
    for k in ('start','end'):layout['pipe'][k]=point(layout['pipe'][k])
    layout['pipe']['original_route']=[point(p) for p in layout['pipe']['original_route']]
    layout['pipe']['diameter']*=scale
    for k in ('clearance','grid_step','bend_penalty'):layout['rules'][k]*=scale
    for stub in layout['protected_endpoints']:
        stub['points']=[point(p) for p in stub['points']];stub['max_distance']*=scale
    for item in layout['obstacles']:
        for k in ('min','max'):item[k]=point(item[k])
        for capsule in item.get('capsules',[]):
            for k in ('start','end'):capsule[k]=point(capsule[k])
            capsule['radius']*=scale
    return layout


@pytest.mark.parametrize('delta',[(29000,-8000,1200),(-40000,6000,-3000)])
def test_translated_environment_still_routes_and_preserves_ports(delta):
    layout=transform(case('01-congested-room.json'),delta=delta)
    result=run_workflow(layout)
    assert result['assembly']['status']=='ready'
    assert result['assembly']['metrics']['bend_count']==2
    assert result['candidate']['points'][0]==layout['pipe']['start']


def test_metre_input_uses_explicit_mm_radius_without_double_scale():
    layout=transform(case('01-congested-room.json'),scale=.001);layout['units']='m'
    result=run_workflow(layout)
    assert result['assembly']['status']=='ready'
    assert result['assembly']['bend_radius_mm']==304.8
    assert result['assembly']['metrics']['centerline_length_mm']==pytest.approx(13419.857440814169)


def test_long_vertical_split_is_one_elevation_run():
    assembly={'metrics':{'bend_count':0,'centerline_length_mm':12000},'components':[
        {'kind':'straight','start':[0,0,0],'end':[0,0,6000]},
        {'kind':'straight','start':[0,0,6000],'end':[0,0,12000]},
    ]}
    assert _score(assembly)==(0,1,12000)


def test_known_mandatory_finding_blocks_rounded_output():
    layout=case('01-congested-room.json')
    # This measurement is bound to the unchanged route; the planned result must
    # not inherit it. A no-change version demonstrates mandatory rejection.
    layout=case('05-already-good.json')
    layout['engineering']={'units':'mm','route_points':deepcopy(layout['pipe']['original_route']),
        'nominal_diameter_mm':100,'nominal_size_basis_verified':True}
    layout['proposal']['bend_radius_mm']=40
    result=run_workflow(layout)
    assert result['assembly']['status']=='blocked'
    assert result['candidate'] is None


def test_rounding_does_not_reuse_engineering_evidence_on_sharp_vertices():
    layout=case('05-already-good.json')
    layout['engineering']={'units':'mm','route_points':deepcopy(layout['pipe']['original_route']),
        'nominal_diameter_mm':100,'nominal_size_basis_verified':True,'inventory_complete':{'pieces':True},
        'pieces':[{'id':'old-piece','source_refs':['old measured route'],
            'centerline_length_mm':4000,'bends':[{'radius_mm':304.8,'angle_deg':90}],
            'straight_between_bends_mm':[],'weld_spacings_mm':[]}]}
    result=run_workflow(layout)
    assert result['candidate']['points']==layout['pipe']['original_route']
    original={f['id']:f for f in result['challenge_review']['original']['findings']}
    candidate={f['id']:f for f in result['challenge_review']['candidate']['findings']}
    assert original['piece_length']['status']=='pass'
    assert candidate['piece_length']['status']=='needs_review'


def test_explicit_bend_rule_is_not_overridden_by_3d_default():
    layout=case('01-congested-room.json');layout['rules']['bend_radius']=500
    layout['proposal'].pop('bend_radius_mm')
    result=run_workflow(layout)
    assert result['assembly']['bend_radius_mm']==500
    layout['proposal']['bend_radius_mm']=304.8
    with pytest.raises(ValueError,match='cannot be smaller'):run_workflow(layout)


def test_blocked_endpoint_does_not_report_reference_tangent_failure_as_cause():
    layout = case('05-already-good.json')
    layout.pop('protected_endpoints')
    layout['pipe']['original_route'] = [
        [600,1200,1000], [600,1600,1000], [610,1610,1000],
        [620,1615,1000], [9400,1615,1000], [9400,1200,1000]]
    layout['obstacles'] = [{'id':'unverified-connector',
                            'min':[500,1100,900], 'max':[700,1300,1100]}]
    result = run_workflow(layout)
    assert result['status'] == 'no_route'
    assert result['candidate'] is None
    assert result['assembly']['meshes'] == []
    assert result['centerline_search']['expanded_states'] == 0
    assert 'unverified-connector' in result['search']['reason']
    assert 'could not start' in result['search']['reason']
    assert any(f['code'] == 'assembly_insufficient_tangent_length'
               for f in result['assembly']['baseline_findings'])
    assert not any(f['code'] == 'assembly_insufficient_tangent_length'
                   for f in result['assembly']['findings'])
    assert any(f['code'] == 'obstacle_collision'
               for f in result['assembly']['findings'])
    assert not result['normalized_layout'].get('protected_endpoints')
