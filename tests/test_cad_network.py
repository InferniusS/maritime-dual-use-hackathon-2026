"""Endpoint graph tests prevent fictitious joins and preserve source identity."""
from copy import deepcopy
import math

import pytest

from pipe_router.cad_network import inspect_network


def candidate(identifier, points, diameter=10, handle=None, instance=()):
    return {'id': identifier, 'points': points, 'geometric_diameter': diameter,
            'source_handles': [handle or identifier], 'instance_path': list(instance),
            'source_records': [5], 'requires_review': True}


def cad(*members):
    return {'units': 'mm', 'source_sha256': 'sourcehash', 'route_candidates': list(members)}


def test_two_component_chain_joins_with_orientation_and_exact_outer_endpoints():
    a = candidate('A', [[0,0,0], [10,0,0]])
    b = candidate('B', [[30,0,0], [10,0,0]])
    result = inspect_network(cad(a,b))
    assert result['counts']['accepted_joins'] == 1
    chain = result['chains'][0]
    assert chain['points'] == [[0,0,0], [10,0,0], [30,0,0]]
    assert chain['member_ids'] == ['A','B']
    assert chain['source_instances'] == [{'source_handle':'A','instance_path':[]}, {'source_handle':'B','instance_path':[]}]
    assert chain['geometric_diameter'] == 10
    assert chain['geometric_length_mm'] == 30
    assert chain['requires_review']
    assert not chain['connectivity_verified']
    assert not chain['required_components_verified']


def test_three_components_across_one_bend_preserve_real_member_points():
    a = candidate('A', [[0,0,0], [10,0,0]])
    bend = candidate('B', [[10,0,0], [12,0,0], [14,2,0], [14,4,0]])
    bend['contains_inferred_bend'] = True
    c = candidate('C', [[14,4,0], [14,20,0]])
    chain = inspect_network(cad(a,bend,c))['chains'][0]
    assert chain['points'] == [[0,0,0], [10,0,0], [12,0,0], [14,2,0], [14,4,0], [14,20,0]]
    assert chain['inferred_bend_component_count'] == 1
    assert chain['inferred_bend_member_ids'] == ['B']
    assert chain['original_member_routes'][1]['points'] == bend['points']


def test_branch_cluster_stops_all_matching_joins_even_if_third_diameter_differs():
    a = candidate('A', [[0,0,0], [10,0,0]])
    b = candidate('B', [[10,0,0], [20,0,0]])
    c = candidate('C', [[10,0,0], [10,10,0]], diameter=5)
    result = inspect_network(cad(a,b,c))
    assert result['chains'] == []
    assert result['counts']['branch_or_ambiguous_junctions'] == 1
    assert result['junctions'][0]['reason'] == 'branch_or_ambiguous_cluster'


def test_valid_chain_stops_before_branch_and_keeps_branch_endpoint():
    members = [candidate('A', [[0,0,0], [10,0,0]]),
               candidate('B', [[10,0,0], [20,0,0]]),
               candidate('C', [[20,0,0], [30,0,0]]),
               candidate('D', [[20,0,0], [20,10,0]])]
    result = inspect_network(cad(*members))
    assert len(result['chains']) == 1
    assert result['chains'][0]['member_ids'] == ['A','B']
    assert result['chains'][0]['points'][-1] == [20,0,0]


def test_larger_diameter_flange_or_different_pipe_size_is_not_crossed():
    result = inspect_network(cad(candidate('PIPE', [[0,0,0], [10,0,0]], 10),
                                 candidate('FLANGE', [[10,0,0], [20,0,0]], 10.01)))
    assert result['chains'] == []
    assert result['junctions'][0]['reason'] == 'diameter_mismatch'


@pytest.mark.parametrize('end', [[10,10,0], [0,0,0]])
def test_right_angle_or_overlapping_backwards_join_is_rejected(end):
    result = inspect_network(cad(candidate('A', [[0,0,0], [10,0,0]]),
                                 candidate('B', [[10,0,0], end])))
    assert result['chains'] == []
    assert any(j['reason'] == 'tangent_mismatch' for j in result['junctions'])


def test_small_gap_is_explicitly_welded_and_original_member_geometry_retained():
    a = candidate('A', [[0,0,0], [10,0,0]])
    b = candidate('B', [[10.4,0,0], [20,0,0]])
    chain = inspect_network(cad(a,b), tolerance_mm=0.5)['chains'][0]
    assert chain['points'] == [[0,0,0], [10.2,0,0], [20,0,0]]
    assert chain['join_records'][0]['gap_mm'] == pytest.approx(0.4)
    assert chain['join_records'][0]['max_endpoint_adjustment_mm'] == pytest.approx(0.2)
    assert chain['original_member_routes'][0]['points'] == a['points']
    assert chain['original_member_routes'][1]['points'] == b['points']
    assert inspect_network(cad(a,b), tolerance_mm=0.1)['chains'] == []


def test_same_source_instance_is_never_joined_to_itself():
    result = inspect_network(cad(candidate('A', [[0,0,0], [10,0,0]], handle='SOLID'),
                                 candidate('B', [[10,0,0], [20,0,0]], handle='SOLID')))
    assert result['chains'] == []
    assert result['junctions'][0]['reason'] == 'same_source_instance'


def test_same_definition_handle_at_different_placements_keeps_instance_identity():
    result = inspect_network(cad(candidate('A', [[0,0,0], [10,0,0]], handle='SOLID',instance=['INSERT-1']),
                                 candidate('B', [[10,0,0], [20,0,0]], handle='SOLID',instance=['INSERT-2'])))
    chain = result['chains'][0]
    assert chain['source_instances'] == [{'source_handle':'SOLID','instance_path':['INSERT-1']},
                                         {'source_handle':'SOLID','instance_path':['INSERT-2']}]
    assert 'source_handles' not in chain
    assert 'instance_path' not in chain


def test_duplicate_source_geometry_is_removed_without_false_branch():
    a = candidate('A', [[0,0,0], [10,0,0]],handle='SOLID')
    duplicate = candidate('COPY', [[10,0,0], [0,0,0]],handle='SOLID')
    b = candidate('B', [[10,0,0], [20,0,0]])
    result = inspect_network(cad(a,duplicate,b))
    assert result['counts']['duplicate_candidates'] == 1
    assert result['counts']['usable_members'] == 2
    assert result['chains'][0]['member_ids'] == ['A','B']


def test_conflicting_repeated_candidate_id_is_not_used():
    result = inspect_network(cad(candidate('A', [[0,0,0], [10,0,0]]),
                                 candidate('A', [[50,0,0], [60,0,0]]),
                                 candidate('B', [[10,0,0], [20,0,0]])))
    assert result['chains'] == []
    assert result['counts']['invalid_candidates'] == 1
    assert result['counts']['usable_members'] == 1


def test_nearby_endpoints_transitive_cluster_is_ambiguous_not_greedy_pairing():
    result = inspect_network(cad(candidate('A', [[-10,0,0], [0,0,0]]),
                                 candidate('B', [[0.75,0,0], [10,0,0]]),
                                 candidate('C', [[1.5,0,0], [1.5,10,0]])), tolerance_mm=1)
    assert result['chains'] == []
    assert result['counts']['branch_or_ambiguous_junctions'] == 1


def test_source_is_unchanged_and_graph_id_is_stable_across_input_order():
    source = cad(candidate('A', [[0,0,0], [10,0,0]]), candidate('B', [[10,0,0], [20,0,0]]))
    original = deepcopy(source)
    one = inspect_network(source)
    assert source == original
    reversed_source = deepcopy(source)
    reversed_source['route_candidates'].reverse()
    two = inspect_network(reversed_source)
    assert one['chains'][0]['id'] == two['chains'][0]['id']
    one['chains'][0]['points'][0][0] = 99
    one['chains'][0]['original_member_routes'][0]['points'][0][0] = 99
    assert source == original


def test_closed_component_is_reported_and_not_given_arbitrary_endpoints():
    # Two smooth half-loop components have opposing tangents at both joins.
    one = candidate('A', [[0,0,0],[1,0,0],[2,2,0],[1,4,0],[0,4,0]])
    two = candidate('B', [[0,4,0],[-1,4,0],[-2,2,0],[-1,0,0],[0,0,0]])
    result = inspect_network(cad(one,two))
    assert result['chains'] == []
    assert result['counts']['accepted_joins'] == 2
    assert result['counts']['closed_components_omitted'] == 1


@pytest.mark.parametrize('value', [0, -1, float('nan'), True])
def test_invalid_endpoint_tolerance_is_rejected(value):
    with pytest.raises(ValueError):
        inspect_network(cad(), tolerance_mm=value)


def test_requires_mm_before_comparing_endpoint_tolerances():
    source = cad()
    source['units'] = 'in'
    with pytest.raises(ValueError, match='converted to mm'):
        inspect_network(source)
