"""Whole-drawing scanning never hides a route behind the quick-review limit."""
from copy import deepcopy
import random

import pytest

from pipe_router import drawing_review
from pipe_router.cad_layout import _overlaps, _solid_boxes, prepare_cad_layout
from pipe_router.drawing_scan import _SolidIndex, scan_drawing
from test_drawing_review import drawing
from test_cad_layout import solid


def separated(count):
    return drawing([[[0, i * 2000, 0], [1000, i * 2000, 0]] for i in range(count)])


def test_more_than_fifty_sections_all_checked_with_stable_numbers_and_identity():
    cad = separated(61)
    before = deepcopy(cad)
    events = []
    result = scan_drawing(cad, progress=events.append)
    review = result['review']
    assert review['total_inventoried'] == review['reviewed_routes'] == review['processed_routes'] == 61
    assert review['not_processed_routes'] == review['omitted_from_list'] == 0
    assert review['coverage'] == 'all_supported_routes_screened'
    assert not review['complete_model_verified']
    assert [row['number'] for row in result['candidates']] == list(range(1, 62))
    for row in result['candidates']:
        individual = prepare_cad_layout(cad, row['id'], clearance=50.8, rule_profile='fmg-v3')
        assert row['start'] == individual['pipe']['start']
        assert row['source_handles'] == individual['cad_context']['source_handles']
        assert row['source_instances'] == individual['cad_context']['source_instances']
        assert row['bounds']['min'][0] < row['start'][0]
        assert not row['full_compliance_verified']
    again = scan_drawing(cad)
    assert [(r['number'], r['id']) for r in again['candidates']] == [(r['number'], r['id']) for r in result['candidates']]
    assert events[0]['processed_routes'] == 0 and events[-1]['processed_routes'] == 61
    assert cad == before
    assert len(drawing_review.review_drawing(cad)['candidates']) == 50


def test_cancellation_preserves_checked_rows_and_numbers_for_remaining_sections():
    events = []
    result = scan_drawing(separated(8), progress=events.append,
        should_cancel=lambda: events[-1]['processed_routes'] == 3)
    assert result['review']['status'] == 'cancelled'
    assert result['review']['processed_routes'] == result['review']['reviewed_routes'] == 3
    assert result['review']['not_processed_routes'] == 5
    assert result['review']['unreviewed_routes'] == 5
    assert all(row['review_status'] == 'not_processed' for row in result['candidates'][3:])
    assert [row['number'] for row in result['candidates']] == list(range(1, 9))


def test_one_unexpected_section_failure_does_not_hide_following_sections(monkeypatch):
    actual = drawing_review._screen_candidate
    calls = []
    def fail_second(cad, candidate, row, prepared, **settings):
        calls.append(candidate['id'])
        if len(calls) == 2:
            raise RuntimeError('unsupported unexpected section')
        return actual(cad, candidate, row, prepared, **settings)
    monkeypatch.setattr(drawing_review, '_screen_candidate', fail_second)
    result = scan_drawing(separated(4))
    assert result['review']['processed_routes'] == 4
    assert result['review']['reviewed_routes'] == 3
    assert result['review']['needs_review_routes'] == 1
    assert result['candidates'][1]['review_status'] == 'needs_review'
    assert 'RuntimeError' in result['candidates'][1]['review_message']
    assert result['candidates'][3]['geometry_checked']


def test_pair_budget_is_review_item_not_pass_or_unprocessed(monkeypatch):
    cad = drawing([[[0,0,0], [1000,0,0]]], [solid('F', [400,-10,-10], [600,10,10])])
    monkeypatch.setattr(drawing_review, 'MAX_REVIEW_SEGMENT_OBSTACLE_PAIRS', 0)
    result = scan_drawing(cad)
    assert result['review']['needs_review_routes'] == 1
    assert result['review']['processed_routes'] == 1
    assert result['review']['reviewed_routes'] == 0
    assert 'work limit' in result['candidates'][0]['review_message']


def test_spatial_cache_matches_full_prepare_exactly_and_does_not_lose_far_solids():
    cad = drawing([[[0,0,0], [1000,0,0]]], [solid('F', [400,-10,-10], [600,10,10]), solid('AA', [20000,0,0], [21000,100,100])])
    network, _ = drawing_review._review_inventory(cad)
    solids = _solid_boxes(cad)
    index = _SolidIndex(solids[0])
    expected = prepare_cad_layout(cad, 'run-1')
    actual = prepare_cad_layout(cad, 'run-1', _prepared_geometry={'network': network, 'solids': solids, 'solid_index': index})
    assert actual == expected
    assert actual['cad_context']['counts']['solid_instances_with_extracted_geometry'] == 3
    assert actual['cad_context']['counts']['outside_local_bounds'] == 1
    with pytest.raises(ValueError, match='does not match'):
        prepare_cad_layout(cad, 'run-1', _prepared_geometry={'network': network, 'solids': _solid_boxes(cad), 'solid_index': index})


def test_spatial_index_matches_brute_force_inclusive_overlap():
    rng = random.Random(9021)
    boxes = []
    for n in range(100):
        low = [rng.uniform(-100,100) for _ in range(3)]
        high = [a + rng.uniform(.01,40) for a in low]
        boxes.append({'min': low, 'max': high, 'key': str(n)})
    index = _SolidIndex(boxes)
    for _ in range(30):
        low = [rng.uniform(-100,100) for _ in range(3)]
        bounds = {'min': low, 'max': [a+30 for a in low]}
        assert index.query(bounds) == [box for box in boxes if _overlaps(box, bounds)]
    assert boxes[0] in index.query({'min': boxes[0]['max'], 'max': [a+1 for a in boxes[0]['max']]})


def test_unrecognized_geometry_inventory_does_not_become_complete_model():
    cad = separated(1)
    cad['counts'].update(solid_instances=3, failed_solid_instances=2)
    cad['unsupported_geometry'] = {'face:mystery': 40, 'edge:mystery': 11, 'SPLINE': 2}
    cad['route_candidates'].append({'id': 'bad', 'points': [], 'geometric_diameter': 10})
    result = scan_drawing(cad)
    assert result['review']['unsupported_objects'] == 4
    assert result['review']['geometry_counts']['source_solid_instances_missing_geometry'] == 2
    assert result['review']['geometry_counts']['unsupported_geometry_records']['face:mystery'] == 40
    assert result['network_counts']['invalid_candidates'] == 1
    assert result['inventory_findings']['invalid_candidates'][0]['candidate_id'] == 'bad'
    assert result['review']['reviewed_routes'] == 1
    assert not result['review']['complete_model_verified']


def test_network_and_full_geometry_are_prepared_once(monkeypatch):
    original_network = drawing_review.inspect_network
    original_solids = _solid_boxes
    calls = {'network': 0, 'solids': 0}
    def network(cad):
        calls['network'] += 1
        return original_network(cad)
    def solids(cad):
        calls['solids'] += 1
        return original_solids(cad)
    import pipe_router.drawing_scan as scanner
    monkeypatch.setattr(drawing_review, 'inspect_network', network)
    monkeypatch.setattr(scanner, '_solid_boxes', solids)
    scan_drawing(separated(60))
    assert calls == {'network': 1, 'solids': 1}


@pytest.mark.parametrize('settings', [{'clearance': float('nan')}, {'padding': float('inf')}, {'padding': -1}, {'min_slope': 0}, {'reverse_flow': 1}])
def test_nonfinite_and_invalid_parameters_fail_before_scan(settings):
    with pytest.raises(ValueError):
        scan_drawing(separated(1), **settings)


def test_dense_scan_checks_late_batch_collisions_without_relaxing_optimizer_limit():
    obstacles = [solid(f'F{i:X}', [400,200,-10], [600,210,10]) for i in range(299)]
    obstacles.append(solid('LATE', [400,-10,-10], [600,10,10]))
    cad = drawing([[[0,0,0], [1000,0,0]]], obstacles)
    with pytest.raises(ValueError, match='at most 256'):
        prepare_cad_layout(cad, 'run-1')
    row = scan_drawing(cad)['candidates'][0]
    assert row['geometry_checked']
    assert row['obstacle_count'] == 300
    assert row['obstacle_batches_checked'] == 2
    assert row['issue_count'] == 1
    assert row['findings'][0]['obstacle'] == 'cad-solid-301'
    assert row['findings'][0]['segment_start'] == [0, 0, 0]
    assert row['findings'][0]['segment_end'] == [1000, 0, 0]
    assert row['findings'][0]['obstacle_source']['source_handle'] == 'LATE'
    shock = next(f for f in row['rule_findings'] if f['id'] == 'shock_clearance')
    assert shock['status'] == 'fail'
    assert shock['measured']['obstacle_count'] == 300
    assert shock['measured']['collision_count'] == 1
    assert shock['source_refs'] == ['cad-solid-301']


def test_dense_collision_details_are_bounded_with_complete_counts():
    obstacles = [solid(f'F{i:X}', [400,-10,-10], [600,10,10]) for i in range(300)]
    row = scan_drawing(drawing([[[0,0,0], [1000,0,0]]], obstacles))['candidates'][0]
    assert row['issue_count'] == row['finding_counts']['obstacle_collision'] == 300
    assert len(row['findings']) == 20
    assert row['omitted_geometry_findings'] == 280
    shock = next(f for f in row['rule_findings'] if f['id'] == 'shock_clearance')
    assert shock['measured']['collision_count'] == 300
    assert len(shock['measured']['collisions']) == 20
    assert shock['measured']['omitted_collisions'] == 280


def test_work_budget_counts_each_capsule_not_only_the_parent_solid(monkeypatch):
    cad = drawing([[[0,0,0], [1000,0,0]]], [solid('F', [400,200,-10], [600,210,10])])
    cad['solid_envelopes'] = [{'source_handle': 'F', 'instance_path': [], 'eligible': True,
        'capsules': [{'start': [400,200,0], 'end': [600,200,0], 'radius': 5} for _ in range(20)]}]
    monkeypatch.setattr(drawing_review, 'MAX_REVIEW_SEGMENT_OBSTACLE_PAIRS', 10)
    row = scan_drawing(cad)['candidates'][0]
    assert row['review_status'] == 'needs_review'
    assert not row['geometry_checked']
    assert 'work limit' in row['review_message']


def test_full_scan_report_keeps_every_number_and_safe_literal_values(tmp_path):
    import json
    from zipfile import ZipFile
    from pipe_router.drawing_scan import write_drawing_scan
    cad = separated(51)
    cad['route_candidates'][0]['name'] = '=HYPERLINK("<script>bad</script>")'
    summary = {**scan_drawing(cad), 'source_sha256': 'snapshot'}
    write_drawing_scan(summary, tmp_path)
    page = (tmp_path/'report.html').read_text()
    assert 'MARITIMEFIND' in page
    assert 'row.dataset.number===term' in page
    assert 'Evidence origins' in page
    assert 'Computed from geometry' in page
    assert 'Not implemented' in page
    assert len(summary['rule_catalog']) < 100
    assert all(f['rule_ref'] in summary['rule_catalog'] for row in summary['candidates'] for f in row['rule_findings'])
    assert 'Section 51' in page
    assert '<script>bad</script>' not in page
    assert '&lt;script&gt;bad&lt;/script&gt;' in page
    assert json.loads((tmp_path/'result.json').read_text()) == summary
    with ZipFile(tmp_path/'route-review.xlsx') as book:
        xml = book.read('xl/worksheets/sheet1.xml').decode()
        assert '<f>' not in xml
        assert 'run-51' in xml
    assert summary['review']['rule_coverage']['component_sizes']['needs_review'] == 51
