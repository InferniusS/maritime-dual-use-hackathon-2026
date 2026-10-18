"""Rule outcome must not obscure where its supporting evidence came from."""
from copy import deepcopy
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pipe_router.challenge_rules import EVIDENCE_KINDS, STATUSES, assess_layout
from pipe_router.review_export import challenge_panel, evidence_labels, write_review_xlsx
from pipe_router.workflow import run_workflow


def layout():
    points = [[0, 0, 1000], [1000, 0, 1000]]
    return {
        'name': 'Evidence audit', 'units': 'mm', 'system_type': 'pressurized',
        'pipe': {'start': points[0], 'end': points[-1], 'diameter': 100, 'original_route': points},
        'obstacles': [{'id': 'wall', 'min': [400, 300, 900], 'max': [500, 400, 1100]}],
        'engineering': {
            'units': 'mm', 'route_points': deepcopy(points),
            'inventory_complete': {'pieces': True},
            'pieces': [{'id': 'P1', 'source_refs': ['drawing:P1'], 'centerline_length_mm': 1000}],
            'evidence': {'fitting_catalog': {'verified': True, 'source_refs': ['review:catalog-selection']}},
        },
    }


def by_id(assessment):
    return {item['id']: item for item in assessment['findings']}


def exported_result(value, candidate=None):
    points = value['pipe']['original_route']
    return {
        'status': 'unchanged', 'rule_profile': 'fmg-v3',
        'original': {'points': points, 'length': 1000},
        'candidate': None if candidate is None else {'points': candidate, 'length': 1200},
        'challenge_review': {'original': assess_layout(value),
                             'candidate': assess_layout(value, candidate) if candidate is not None else None},
    }


def sheet_rows(path, name):
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        sheets = workbook.findall('s:sheets/s:sheet', ns)
        number = next(i for i, sheet in enumerate(sheets, 1) if sheet.attrib['name'] == name)
        sheet = ET.fromstring(archive.read(f'xl/worksheets/sheet{number}.xml'))
        assert not sheet.findall('.//s:f', ns)
        return [[cell.findtext('s:is/s:t', namespaces=ns) if cell.attrib.get('t') == 'inlineStr'
                 else cell.findtext('s:v', namespaces=ns) for cell in row.findall('s:c', ns)]
                for row in sheet.findall('s:sheetData/s:row', ns)]


def test_same_pass_status_has_distinct_computed_supplied_and_declared_origins():
    value = layout()
    before = deepcopy(value)
    report = assess_layout(value)
    rows = by_id(report)
    for rule in ('shock_clearance', 'piece_length', 'fitting_catalog'):
        assert rows[rule]['status'] == 'pass'
    assert rows['shock_clearance']['evidence_kind'] == 'computed_geometry'
    assert rows['shock_clearance']['evidence_completeness'] == 'supplied_local_geometry_only'
    assert rows['piece_length']['evidence_kind'] == 'supplied_measurement'
    assert rows['piece_length']['evidence_completeness'] == 'declared_complete_inventory'
    assert 'does not independently extract' in rows['piece_length']['evidence_note']
    assert rows['fitting_catalog']['evidence_kind'] == 'reviewer_declaration'
    assert rows['fitting_catalog']['evidence_completeness'] == 'reviewer_declared_verified'
    assert 'does not independently verify' in rows['fitting_catalog']['evidence_note']
    assert not report['complete']
    assert value == before
    json.dumps(report, allow_nan=False)


def test_origin_counts_include_all_outcomes_and_keep_pending_rules_unimplemented():
    report = assess_layout(layout())
    assert set(report['evidence_counts']) == set(EVIDENCE_KINDS)
    assert sum(report['evidence_counts'].values()) == len(report['findings'])
    for kind in EVIDENCE_KINDS:
        assert set(report['evidence_status_counts'][kind]) == set(STATUSES)
        assert sum(report['evidence_status_counts'][kind].values()) == report['evidence_counts'][kind]
    pending = by_id(report)['physical_installability']
    assert pending['status'] == 'needs_review'
    assert pending['evidence_kind'] == 'unimplemented'
    assert pending['evidence_completeness'] == 'not_implemented'
    assert report['evidence_status_counts']['unimplemented']['pass'] == 0
    assert report['evidence_status_counts']['computed_geometry']['not_applicable'] == 2


def test_missing_inventory_and_self_attestation_never_become_automated_passes():
    value = layout()
    del value['engineering']['pieces']
    value['engineering']['evidence']['fitting_catalog']['source_refs'] = []
    rows = by_id(assess_layout(value))
    assert rows['piece_length']['status'] == 'needs_review'
    assert rows['piece_length']['evidence_kind'] == 'supplied_measurement'
    assert rows['piece_length']['evidence_completeness'] == 'not_provided'
    assert rows['fitting_catalog']['status'] == 'needs_review'
    assert rows['fitting_catalog']['evidence_kind'] == 'reviewer_declaration'
    assert rows['fitting_catalog']['evidence_completeness'] == 'unverified_declaration'
    assert rows['ship_boundary']['evidence_completeness'] == 'not_provided'


def test_incomplete_inventory_can_contain_a_known_failure_without_becoming_complete():
    value = layout()
    value['engineering']['inventory_complete']['pieces'] = False
    value['engineering']['pieces'][0]['centerline_length_mm'] = 7000
    row = by_id(assess_layout(value))['piece_length']
    assert row['status'] == 'fail'
    assert row['evidence_kind'] == 'supplied_measurement'
    assert row['evidence_completeness'] == 'incomplete_inventory'


def test_changed_route_invalidates_supplied_evidence_but_recalculates_geometry():
    value = layout()
    candidate = [[0, 0, 1000], [450, 350, 1000], [1000, 0, 1000]]
    rows = by_id(assess_layout(value, candidate))
    assert rows['shock_clearance']['status'] == 'fail'
    assert rows['shock_clearance']['evidence_kind'] == 'computed_geometry'
    for rule, kind in [('piece_length', 'supplied_measurement'), ('fitting_catalog', 'reviewer_declaration')]:
        assert rows[rule]['status'] == 'needs_review'
        assert rows[rule]['evidence_kind'] == kind
        assert rows[rule]['evidence_completeness'] == 'route_mismatch'


def test_empty_complete_inventory_is_a_declared_non_applicability_not_cad_verification():
    value = layout()
    value['engineering']['pieces'] = []
    row = by_id(assess_layout(value))['piece_length']
    assert row['status'] == 'not_applicable'
    assert row['evidence_kind'] == 'supplied_measurement'
    assert row['evidence_completeness'] == 'declared_complete_inventory'


def test_gravity_geometry_origin_discloses_missing_and_declared_axes():
    value = layout()
    value['system_type'] = 'gravity'
    value['pipe']['original_route'][-1][2] = 960
    missing = by_id(assess_layout(value))['directional_drain_slope']
    assert missing['status'] == 'needs_review'
    assert missing['evidence_completeness'] == 'missing_axes_or_flow'
    value['engineering'].update(ship_axes={'longitudinal': '+X', 'transverse': '+Y', 'up': '+Z', 'confirmed': True},
                                flow_direction='start_to_end')
    measured = by_id(assess_layout(value))['directional_drain_slope']
    assert measured['status'] == 'pass'
    assert measured['evidence_kind'] == 'computed_geometry'
    assert measured['evidence_completeness'] == 'route_geometry_only'
    assert 'supplied declarations' in measured['evidence_note']


def test_html_and_excel_preserve_origin_and_completeness_separately_from_result(tmp_path):
    value = layout()
    result = exported_result(value)
    note = '=HYPERLINK("untrusted") <script>not executable</script>'
    by_id(result['challenge_review']['original'])['fitting_catalog']['evidence_note'] = note
    page = challenge_panel(result)
    assert '<th>Evidence</th>' in page
    assert 'Computed from geometry' in page
    assert 'Supplied measurement' in page
    assert 'Reviewer declaration' in page
    assert 'Not implemented' in page
    assert 'all outcomes, not passed checks' in page
    assert '<script>' not in page
    assert '&lt;script&gt;not executable&lt;/script&gt;' in page
    path = tmp_path / 'evidence.xlsx'
    write_review_xlsx(value, result, path)
    raw = sheet_rows(path, 'Rule findings')
    records = [dict(zip(raw[0], row)) for row in raw[1:]]
    rows = {row['Rule']: row for row in records}
    assert rows['piece_length']['Status'] == 'pass'
    assert rows['piece_length']['Evidence kind'] == 'Supplied measurement'
    assert rows['piece_length']['Evidence completeness'] == 'declared complete inventory'
    assert rows['fitting_catalog']['Evidence kind'] == 'Reviewer declaration'
    assert rows['fitting_catalog']['Evidence note'] == note
    assert rows['physical_installability']['Evidence kind'] == 'Not implemented'
    assert any('finding origins' in row[0] for row in sheet_rows(path, 'Summary'))


def test_legacy_pass_is_not_guessed_to_be_automatically_computed():
    assert evidence_labels({'status': 'pass', 'measured': 10})[0] == 'Evidence not classified'
    assert evidence_labels({'status': 'pass', 'evidence_basis': 'generated_component_geometry'})[0] == 'Computed from generated geometry'
    assert evidence_labels({'status': 'needs_review', 'evidence_basis': 'not_provided_for_generated_components'})[0] == 'Evidence not provided'


def workflow_case(name):
    return json.loads((Path(__file__).resolve().parents[1] / 'examples' / 'challenge' / f'{name}.json').read_text())


def assert_complete_origin_totals(assessment):
    assert all(item.get('evidence_note') and item.get('evidence_completeness') for item in assessment['findings'])
    assert sum(assessment['evidence_counts'].values()) == len(assessment['findings'])
    for kind in EVIDENCE_KINDS:
        for status in STATUSES:
            assert assessment['evidence_status_counts'][kind][status] == sum(
                item['evidence_kind'] == kind and item['status'] == status for item in assessment['findings'])


def test_workflow_missing_space_check_is_unknown_and_counted_after_append():
    result = run_workflow(workflow_case('valid-straight'))
    for side in ('original', 'candidate'):
        assessment = result['challenge_review'][side]
        assert_complete_origin_totals(assessment)
        row = by_id(assessment)['context_allowed_spaces']
        assert row['status'] == 'needs_review'
        assert row['evidence_kind'] == 'computed_geometry'
        assert row['evidence_completeness'] == 'not_provided'
        assert not assessment['complete']


def test_workflow_distinguishes_computed_space_checks_from_coverage_declarations(tmp_path):
    value = workflow_case('protected-passage')
    result = run_workflow(value)
    assert result['candidate'] is not None
    for side in ('original', 'candidate'):
        assessment = result['challenge_review'][side]
        assert_complete_origin_totals(assessment)
        rows = by_id(assessment)
        for rule in ('context_allowed_spaces', 'context_required_space'):
            assert rows[rule]['status'] == 'pass'
            assert rows[rule]['evidence_kind'] == 'computed_geometry'
            assert rows[rule]['evidence_completeness'] == 'supplied_space_geometry_only'
        for rule in ('context_coverage_structure', 'context_context_geometry'):
            assert rows[rule]['status'] == 'needs_review'
            assert rows[rule]['evidence_kind'] == 'reviewer_declaration'
            assert rows[rule]['evidence_completeness'] == 'incomplete_context_inventory'
        assert not assessment['complete']
    assert 'Evidence not classified' not in challenge_panel(result)
    path = tmp_path / 'workflow-context.xlsx'
    write_review_xlsx(result['normalized_layout'], result, path)
    raw = sheet_rows(path, 'Rule findings')
    rows = [dict(zip(raw[0], row)) for row in raw[1:]]
    assert all(row['Evidence kind'] != 'Evidence not classified' for row in rows)
    assert any(row['Rule'] == 'context_allowed_spaces' and row['Evidence kind'] == 'Computed from geometry' for row in rows)


def test_workflow_space_violation_remains_a_computed_failure():
    value = workflow_case('protected-passage')
    value['model_context']['keep_outs'] = []
    value['pipe']['original_route'] = [[0, 0, 1000], [0, 1600, 1000], [2000, 1600, 1000], [2000, 0, 1000]]
    result = run_workflow(value)
    original = result['challenge_review']['original']
    assert_complete_origin_totals(original)
    failures = [item for item in original['findings'] if item['id'] == 'context_allowed_spaces' and item['status'] == 'fail']
    assert failures
    assert all(item['evidence_kind'] == 'computed_geometry' for item in failures)
    assert 'context_allowed_spaces' in original['hard_failures']
