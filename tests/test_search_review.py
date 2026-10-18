"""Search-area diagnostics must not waive constraints or move CAD endpoints."""
from copy import deepcopy

import pytest

from pipe_router.proposal import _compact
from pipe_router.search_review import review_search_region
from pipe_router.workflow import run_workflow


def wall_case(padding=500, *, delta=(0, 0, 0)):
    # Invented input with transform-scale floating point residues, independent
    # of any organizer geometry or particular CAD entity handle.
    point = lambda p: [a + b for a, b in zip(p, delta)]
    start, end = point([1.3e-12, -4e-28, 0]), point([6000.000000000002, -1.3e-12, 0])
    margin = 114.3 / 2 + 50.8
    return {'name': 'Invented search-area test', 'units': 'mm', 'provenance': 'cad-derived',
            'system_type': 'pressurized', 'rule_profile': 'fmg-v3',
            'bounds': {'min': point([-padding-margin]*3),
                       'max': point([6000+padding+margin, padding+margin, padding+margin])},
            'pipe': {'start': start, 'end': end, 'diameter': 114.3, 'original_route': [start, end]},
            'obstacles': [{'id': 'invented-wall', 'min': point([2800, -600, -600]),
                           'max': point([3200, 600, 600])}],
            'rules': {'clearance': 50.8, 'grid_step': 250, 'bend_penalty': 300},
            'cad_context': {'padding_mm': padding, 'obstacle_sources': [
                {'id': 'invented-wall', 'source_handle': 'TEST-WALL'}]},
            'proposal': {'mode': 'rounded_pipe', 'bend_radius_mm': 304.8}}


@pytest.mark.parametrize('delta', [(0, 0, 0), (29000, -8000, 1200)])
def test_small_search_region_explains_box_barrier_and_preserves_settings(delta):
    layout = wall_case(delta=delta)
    before = deepcopy(layout)
    result = run_workflow(layout)
    review = result['search_region_review']
    assert result['status'] == 'no_route'
    assert result['assembly']['meshes'] == []
    assert review['boundary_review'] == 'represented_barrier_spans_search_region'
    assert review['barriers'][0]['source_handle'] == 'TEST-WALL'
    assert review['barriers'][0]['axis'] == 'X'
    assert review['padding_mm'] == 500
    assert review['clearance_mm'] == 50.8
    assert 'rescan' in review['next_action']
    assert 'cross-section' in result['search']['reason']
    assert layout == before
    assert not any(f['code'] == 'end_mismatch' for f in result['assembly']['findings'])


@pytest.mark.parametrize('delta', [(0, 0, 0), (29000, -8000, 1200)])
def test_larger_region_repairs_with_identical_endpoints_diameter_and_clearance(delta):
    layout = wall_case(1500, delta=delta)
    result = run_workflow(layout)
    assert result['status'] == 'improved'
    assert result['assembly']['status'] == 'ready'
    assert result['search_region_review']['boundary_review'] == 'accepted'
    assert not result['search_region_review']['ship_containment_verified']
    assert result['normalized_layout']['rules']['clearance'] == 50.8
    assert result['assembly']['outer_diameter_mm'] == 114.3
    for points in (result['candidate']['points'], result['assembly']['centerline_points']):
        assert points[0] == layout['pipe']['start']
        assert points[-1] == layout['pipe']['end']


def test_capsule_broad_phase_box_is_not_proof_of_a_cross_section_barrier():
    layout = wall_case()
    layout['obstacles'][0]['capsules'] = [{'start': [2800, 600, 600], 'end': [3200, 600, 600], 'radius': 5}]
    review = review_search_region(layout, {})
    assert review['barriers'] == []
    assert review['boundary_review'] == 'bounded_search_exhausted'


def test_box_that_does_not_separate_endpoints_is_not_a_barrier():
    layout = wall_case()
    layout['pipe']['end'] = [1000, 0, 0]
    assert review_search_region(layout, {})['barriers'] == []


def test_fixed_endpoint_diagnosis_takes_priority_over_padding():
    layout = wall_case()
    result = {'centerline_search': {'blocked_endpoints': [{'endpoint': 'start', 'obstacle': 'other'}]}}
    review = review_search_region(layout, result)
    assert review['boundary_review'] == 'fixed_endpoint_contact'
    assert 'Increasing padding does not remove' in review['next_action']


def test_compaction_preserves_both_exact_ends_without_relaxing_validation():
    points = [[1e-12, 0, 0], [1e-12, 1e-14, 0], [6000, 0, 0], [6000.000000000002, -1e-12, 0]]
    compact = _compact(points)
    assert compact[0] == points[0]
    assert compact[-1] == points[-1]
    assert len(compact) == 2


def test_late_mandatory_rejection_cannot_retain_accepted_search_diagnosis():
    layout = wall_case(1500)
    layout['obstacles'] = []
    # Bind an independently supplied failing hanger measurement to the exact
    # unchanged straight route. The geometric search may accept it, but the
    # later mandatory rule must override that outcome and its displayed advice.
    layout['engineering'] = {'units': 'mm', 'route_points': deepcopy(layout['pipe']['original_route']),
        'source_refs': ['invented:measurement'], 'inventory_complete': {'hangers': True},
        'hangers': [{'id': 'H1', 'source_refs': ['invented:H1'],
                     'orientation': 'horizontal', 'distance_mm': 60}]}
    result = run_workflow(layout)
    assert result['status'] == 'no_route'
    assert result['assembly']['status'] == 'blocked'
    assert result['candidate'] is None and result['assembly']['meshes'] == []
    assert result['search_region_review']['boundary_review'] == 'mandatory_rule_rejected'
    assert 'horizontal_hanger_distance' in result['challenge_review']['rejected_candidate']['hard_failures']


def test_optional_null_context_does_not_break_plain_or_rounded_workflow():
    layout = wall_case(1500)
    layout['cad_context'] = None
    result = run_workflow(layout)
    assert result['status'] == 'improved'
    assert result['search_region_review']['padding_mm'] is None
    del layout['proposal']
    assert run_workflow(layout)['status'] == 'improved'
