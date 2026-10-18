"""CAD test preparation preserves identity and does not hide nearby solids."""
from copy import deepcopy

import pytest

from pipe_router.cad_layout import prepare_cad_layout
from pipe_router.workflow import normalize_layout


def solid(handle, low, high, instance=(), kind='mesh'):
    return {'id': f'{handle}:{len(instance)}:{low}', 'type': '3DSOLID',
            'source_handle': handle, 'instance_path': list(instance),
            'geometry': ({'kind': 'mesh', 'vertices': [low, high], 'faces': []}
                         if kind == 'mesh' else {'kind': 'polyline', 'points': [low, high]})}


@pytest.fixture
def cad():
    return {
        'name': 'Example imported drawing', 'provenance': 'cad', 'units': 'mm',
        'source_units': 'in', 'unit_assumption': True, 'source_sha256': 'knownhash',
        'source': '/local/example.dxf', 'warnings': ['Partial source geometry'],
        'counts': {'solid_instances': 4}, 'unsupported_geometry': {'face:spline': 2},
        'objects': [
            solid('A', [-5,-5,-5], [105,5,5]),
            solid('A', [20,20,-5], [30,30,5], instance=['INSERT-1']),
            solid('B', [60,80,-10], [70,90,10]),
            solid('B', [50,90,-5], [80,120,20], kind='polyline'),
            solid('FAR', [10000,0,0], [10010,10,10]),
        ],
        'route_candidates': [{
            'id': 'A:candidate-run', 'points': [[0,0,0], [100,0,0]],
            'geometric_diameter': 10, 'source_handles': ['A'], 'instance_path': [],
            'source_records': [7, 14], 'requires_review': True,
        }],
    }


def test_only_selected_source_instance_is_excluded(cad):
    result = prepare_cad_layout(cad, 'A:candidate-run', padding=200)
    context = result['cad_context']
    assert context['selected_source_instances'] == [{'source_handle': 'A', 'instance_path': []}]
    sources = context['obstacle_sources']
    assert len(sources) == 2
    assert any(o['source_handle'] == 'A' and o['instance_path'] == ['INSERT-1'] for o in sources)
    assert context['counts']['solid_instances_with_extracted_geometry'] == 4
    assert context['counts']['outside_local_bounds'] == 1
    assert context['counts']['checked_obstacles'] == 2
    assert context['source_sha256'] == 'knownhash'
    assert context['requires_review']
    assert context['geometry_origin'] == 'full_extracted_geometry'


def test_boxes_group_all_full_geometry_and_filter_only_far_instances(cad):
    result = prepare_cad_layout(cad, 'A:candidate-run', padding=200)
    source = next(o for o in result['cad_context']['obstacle_sources'] if o['source_handle'] == 'B')
    obstacle = next(o for o in result['obstacles'] if o['id'] == source['id'])
    assert obstacle['min'] == [50,80,-10]
    assert obstacle['max'] == [80,120,20]
    assert source['source_object_count'] == 2
    assert source['source_point_count'] == 4
    assert not any(o['source_handle'] == 'FAR' for o in result['cad_context']['obstacle_sources'])


def test_preparation_has_no_source_mutation_and_does_not_rescale_mm(cad):
    before = deepcopy(cad)
    result = prepare_cad_layout(cad, 'A:candidate-run', clearance=3, padding=10)
    assert cad == before
    assert result['units'] == 'mm'
    assert result['pipe']['start'] == [0,0,0]
    assert result['pipe']['end'] == [100,0,0]
    assert result['pipe']['diameter'] == 10
    assert result['cad_context']['source_units'] == 'in'
    result['pipe']['original_route'][0][0] = -999
    result['cad_context']['source_counts']['solid_instances'] = -999
    assert cad == before


def test_bounds_include_all_original_points_plus_padding_and_pipe_envelope(cad):
    cad['route_candidates'][0]['points'] = [[0,0,0], [0,700,300], [100,0,0]]
    result = prepare_cad_layout(cad, 'A:candidate-run', clearance=20, padding=100)
    assert result['bounds']['min'] == [-125,-125,-125]
    assert result['bounds']['max'] == [225,825,425]
    assert result['cad_context']['bounds_scope'] == 'assumed_local_search_box'
    assert not result['cad_context']['ship_boundary_verified']
    assert any('not the ship boundary' in warning for warning in result['cad_context']['limitations'])


def test_neighboring_endpoint_connector_remains_a_contact(cad):
    cad['objects'].append(solid('CONNECTOR', [100,-5,-5], [115,5,5]))
    result = prepare_cad_layout(cad, 'A:candidate-run', clearance=0, padding=50)
    contacts = result['cad_context']['endpoint_contacts']
    assert any(item['endpoint'] == 'end' and item['source_handle'] == 'CONNECTOR' for item in contacts)
    assert any(item['source_handle'] == 'CONNECTOR' for item in result['cad_context']['obstacle_sources'])
    assert any('fixed endpoints' in warning for warning in result['cad_context']['warnings'])


def test_thin_geometry_gets_a_nonempty_box(cad):
    cad['objects'] = [cad['objects'][0], solid('PLANE', [20,0,0], [20,10,0])]
    result = prepare_cad_layout(cad, 'A:candidate-run', padding=50)
    obstacle = result['obstacles'][0]
    assert all(a < b for a, b in zip(obstacle['min'], obstacle['max']))
    assert obstacle['min'][0] < 20 < obstacle['max'][0]
    assert obstacle['min'][2] < 0 < obstacle['max'][2]
    assert result['cad_context']['obstacle_sources'][0]['thin_axes_padded'] == [0, 2]


def test_reverse_flow_changes_only_route_direction(cad):
    normal = prepare_cad_layout(cad, 'A:candidate-run', system_type='gravity')
    reversed_run = prepare_cad_layout(cad, 'A:candidate-run', system_type='gravity', reverse_flow=True)
    assert reversed_run['pipe']['original_route'] == list(reversed(normal['pipe']['original_route']))
    assert reversed_run['pipe']['start'] == normal['pipe']['end']
    assert reversed_run['pipe']['end'] == normal['pipe']['start']
    assert reversed_run['bounds'] == normal['bounds']
    assert reversed_run['obstacles'] == normal['obstacles']
    assert reversed_run['rules']['min_slope'] == 0.01
    assert reversed_run['cad_context']['reverse_flow']


def test_layout_is_accepted_by_workflow_without_losing_cad_context(cad):
    result = prepare_cad_layout(cad, 'A:candidate-run')
    normalized = normalize_layout(result)
    assert normalized['provenance'] == 'cad-derived'
    assert normalized['cad_context'] == result['cad_context']
    assert normalized['pipe'] == result['pipe']


def test_over_limit_region_is_rejected_without_truncation(cad):
    cad['objects'] = [cad['objects'][0]] + [
        solid(f'NEAR-{index}', [10,10,10], [20,20,20]) for index in range(257)
    ]
    with pytest.raises(ValueError, match='257 obstacle solids.*256.*No obstacles were dropped'):
        prepare_cad_layout(cad, 'A:candidate-run')
    assert len(cad['objects']) == 258


@pytest.mark.parametrize('case', ['missing_id', 'ambiguous_id', 'preview', 'units', 'missing_geometry'])
def test_invalid_sources_fail_with_actionable_errors(cad, case):
    candidate_id = 'A:candidate-run'
    if case == 'missing_id':
        candidate_id = 'not-in-this-drawing'
    elif case == 'ambiguous_id':
        cad['route_candidates'].append(deepcopy(cad['route_candidates'][0]))
    elif case == 'preview':
        cad['preview'] = {'mode': 'reduced_surfaces'}
    elif case == 'units':
        cad['units'] = 'in'
    elif case == 'missing_geometry':
        cad['objects'] = []
    with pytest.raises(ValueError):
        prepare_cad_layout(cad, candidate_id)


def test_partial_geometry_and_unchecked_explicit_curves_stay_visible(cad):
    cad['counts']['solid_instances'] = 6
    cad['objects'].append({'id': 'line', 'type': 'LINE',
                           'geometry': {'kind': 'polyline', 'points': [[0,0,0], [20,20,20]]}})
    result = prepare_cad_layout(cad, 'A:candidate-run')
    context = result['cad_context']
    assert context['counts']['solid_instances_without_extracted_geometry'] == 2
    assert context['counts']['explicit_curve_objects_unchecked'] == 1
    assert context['unsupported_geometry'] == {'face:spline': 2}
    assert context['source_warnings'] == ['Partial source geometry']
    assert not context['complete_ship_geometry']


@pytest.mark.parametrize('field,value', [('clearance', -1), ('padding', -1), ('padding', float('inf')), ('reverse_flow', 'yes'), ('system_type', [])])
def test_invalid_options_are_rejected(cad, field, value):
    with pytest.raises(ValueError):
        prepare_cad_layout(cad, 'A:candidate-run', **{field: value})


def test_authoritative_source_instances_keep_other_placements_fixed(cad):
    cad['route_candidates'][0]['source_instances'] = [{'source_handle': 'A', 'instance_path': ['INSERT-1']}]
    # Legacy handle/path fields deliberately conflict with the authoritative pair.
    result = prepare_cad_layout(cad, 'A:candidate-run', padding=200)
    selected = result['cad_context']['selected_source_instances']
    assert selected == [{'source_handle': 'A', 'instance_path': ['INSERT-1']}]
    assert any(item['source_handle'] == 'A' and item['instance_path'] == [] for item in result['cad_context']['obstacle_sources'])


def test_multiple_source_instances_are_excluded_exactly(cad):
    cad['route_candidates'][0]['source_instances'] = [
        {'source_handle': 'A', 'instance_path': []},
        {'source_handle': 'B', 'instance_path': []},
    ]
    result = prepare_cad_layout(cad, 'A:candidate-run', padding=200)
    assert len(result['cad_context']['selected_source_instances']) == 2
    assert result['cad_context']['obstacle_sources'][0]['source_handle'] == 'A'
    assert result['cad_context']['obstacle_sources'][0]['instance_path'] == ['INSERT-1']


def test_bridge_explicit_connection_keeps_neighbor_obstacle_and_fixed_stub(cad):
    cad['objects'].append(solid('CONNECTOR', [100,-5,-5], [115,5,5]))
    ordinary = prepare_cad_layout(cad, 'A:candidate-run', clearance=0, padding=50)
    connector_id = next(item['obstacle_id'] for item in ordinary['cad_context']['endpoint_contacts'] if item['source_handle'] == 'CONNECTOR')
    protected = prepare_cad_layout(cad, 'A:candidate-run', clearance=0, padding=50,
                                  endpoint_connections={'end': [connector_id]}, max_stub_length=25)
    assert protected['obstacles'] == ordinary['obstacles']
    assert protected['protected_endpoints'][0]['endpoint'] == 'end'
    assert protected['protected_endpoints'][0]['points'][-1] == [100,0,0]
    assert protected['protected_endpoints'][0]['max_distance'] == 25
    assert protected['cad_context']['endpoint_connection_assumptions']


def test_network_chain_resolution_keeps_join_traceability(cad, monkeypatch):
    from pipe_router import cad_network
    chain = deepcopy(cad['route_candidates'][0])
    chain.update({'id':'network:test', 'source_instances':[{'source_handle':'A','instance_path':[]}],
                  'member_ids':['member-1','member-2'], 'join_records':[{'distance_mm':0.2}],
                  'confidence':'inferred_endpoint_network', 'warnings':['Connection is inferred']})
    monkeypatch.setattr(cad_network, 'inspect_network', lambda _: {'chains':[chain]})
    result = prepare_cad_layout(cad, 'network:test')
    assert result['cad_context']['network_member_ids'] == ['member-1','member-2']
    assert result['cad_context']['network_join_records'] == [{'distance_mm':0.2}]
    assert 'Connection is inferred' in result['cad_context']['warnings']


@pytest.mark.parametrize('connections', [False, [], '', 0, {'middle':[]}, {'start':False}, {'end':'box'}, {'start':[None]}])
def test_bridge_rejects_malformed_endpoint_options(cad, connections):
    with pytest.raises(ValueError):
        prepare_cad_layout(cad, 'A:candidate-run', endpoint_connections=connections)


@pytest.mark.parametrize('connections', [None, {}, {'start':[], 'end':[]}])
def test_bridge_empty_endpoint_choices_do_not_create_contact_exemptions(cad, connections):
    result = prepare_cad_layout(cad, 'A:candidate-run', endpoint_connections=connections)
    assert not result.get('protected_endpoints')


@pytest.mark.parametrize('maximum', [False, 0, -1, 1001, float('inf'), '250'])
def test_bridge_checks_stub_limit_without_contacts(cad, maximum):
    with pytest.raises(ValueError):
        prepare_cad_layout(cad, 'A:candidate-run', max_stub_length=maximum)
