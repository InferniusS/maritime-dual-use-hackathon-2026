"""Fresh CAD bytes -> plugin HTTP import -> rounded proposal, without route JSON.

These tests prove the service/importer path with independently generated analytic
fixtures. They do not claim AutoCAD loaded the fixtures or ran the .NET plugin.
"""
import base64
import hashlib
import json
import math
from pathlib import Path
import sys
import threading
from urllib.error import HTTPError

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from check_cad_acceptance import CASES, CLEARANCE_MM, DIAMETER_MM, cylinder_sat, make_case, request, run_case
from run_web import make_server


@pytest.fixture(scope='module')
def fresh_service(tmp_path_factory):
    folder = tmp_path_factory.mktemp('fresh-cad-api')
    server = make_server(port=0, output=folder / 'exports')
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield {'base_url': f'http://127.0.0.1:{server.server_port}', 'folder': folder}
    server.shutdown()
    server.server_close()
    worker.join(timeout=5)
    assert not worker.is_alive()


def segment_box_distance(start, end, low, high):
    """Independent exact segment/AABB distance via piecewise quadratics.

    This intentionally does not call the router's collision functions.
    """
    direction = [b - a for a, b in zip(start, end)]
    splits = {0.0, 1.0}
    for axis in range(3):
        if direction[axis]:
            splits.update(t for bound in (low[axis], high[axis])
                          if 0 < (t := (bound - start[axis]) / direction[axis]) < 1)
    ordered = sorted(splits)
    candidates = list(ordered)
    for left, right in zip(ordered, ordered[1:]):
        midpoint = (left + right) / 2
        aa = bb = 0.0
        for axis in range(3):
            value = start[axis] + midpoint * direction[axis]
            boundary = low[axis] if value < low[axis] else high[axis] if value > high[axis] else None
            if boundary is not None:
                aa += direction[axis] ** 2
                bb += direction[axis] * (start[axis] - boundary)
        if aa:
            candidates.append(max(left, min(right, -bb / aa)))
    def squared(t):
        return sum(max(low[i] - (start[i] + t * direction[i]),
                       start[i] + t * direction[i] - high[i], 0) ** 2 for i in range(3))
    return math.sqrt(min(squared(t) for t in candidates))


@pytest.mark.parametrize('case_name', CASES)
def test_fresh_dxf_pipeline_preserves_identity_and_checks_actual_environment(fresh_service, case_name):
    fixture = make_case(fresh_service['folder'] / case_name, case_name)
    source = Path(fixture['path'])
    before = source.read_bytes()
    record = run_case(fresh_service['base_url'], fixture)
    result, layout = record['checked']['result'], record['layout']
    assert result['status'] == fixture['expected']
    assert source.read_bytes() == before
    assert record['imported']['source_sha256'] == hashlib.sha256(before).hexdigest()
    assert record['imported']['candidates'][0]['source_handles'] == [fixture['pipe_handle']]
    assert layout['cad_context']['selected_handles'] == [fixture['selection_handle']]
    assert layout['cad_context']['source_snapshot_sha256'] == hashlib.sha256(before).hexdigest()
    assert layout['pipe']['start'] == fixture['start']
    assert layout['pipe']['end'] == fixture['end']
    assert layout['pipe']['diameter'] == DIAMETER_MM
    assert layout['pipe']['original_route'] == [fixture['start'], fixture['end']]
    assert len(layout['obstacles']) == 1
    assert layout['obstacles'][0]['min'] == fixture['wall_min']
    assert layout['obstacles'][0]['max'] == fixture['wall_max']
    assert not layout.get('protected_endpoints')
    assert not result['challenge_review']['complete_compliance_verified']
    original_distance = segment_box_distance(fixture['start'], fixture['end'], fixture['wall_min'], fixture['wall_max'])
    if fixture['expected'] == 'unchanged':
        assert original_distance > DIAMETER_MM / 2 + CLEARANCE_MM
        assert result['original']['valid']
    else:
        assert original_distance == 0
        assert not result['original']['valid']
    if fixture['expected'] == 'no_route':
        assert result['candidate'] is None
        assert result['assembly']['status'] == 'blocked'
        assert result['assembly']['meshes'] == []
        assert layout['cad_context']['endpoint_contacts']
        return
    assembly = result['assembly']
    assert assembly['status'] == 'ready'
    assert assembly['outer_diameter_mm'] == DIAMETER_MM
    assert result['candidate']['points'][0] == fixture['start']
    assert result['candidate']['points'][-1] == fixture['end']
    assert assembly['centerline_points'][0] == fixture['start']
    assert assembly['centerline_points'][-1] == fixture['end']
    assert assembly['meshes']
    for component in assembly['components']:
        assert component['outer_diameter_mm'] == DIAMETER_MM
        minimum = DIAMETER_MM / 2 + CLEARANCE_MM + component.get('chord_error_mm', 0)
        for start, end in zip(component['centerline_points'], component['centerline_points'][1:]):
            assert segment_box_distance(start, end, fixture['wall_min'], fixture['wall_max']) >= minimum - 1e-6
    if fixture['expected'] == 'improved':
        assert assembly['metrics']['bend_count'] >= 2
        assert assembly['centerline_points'] != layout['pipe']['original_route']
    else:
        assert assembly['metrics']['bend_count'] == 0
        assert assembly['centerline_points'] == layout['pipe']['original_route']


def test_fresh_unsupported_line_never_becomes_a_pipe(fresh_service):
    path = fresh_service['folder'] / 'unsupported-line.dxf'
    document = ezdxf.new('R2010')
    line = document.modelspace().add_line((0, 0, 0), (6000, 0, 0))
    document.saveas(path)
    before = path.read_bytes()
    with pytest.raises(HTTPError) as caught:
        request(fresh_service['base_url'], '/api/autocad/inspect', {
            'filename': path.name, 'content_base64': base64.b64encode(before).decode(),
            'source_units': 'mm', 'selected_handles': [line.dxf.handle],
        })
    assert caught.value.code == 400
    failure = json.loads(caught.value.read())
    assert 'No pipe route' in failure['error']
    assert path.read_bytes() == before


def test_fresh_whole_drawing_review_matches_the_same_real_pipe(fresh_service):
    fixture = make_case(fresh_service['folder'] / 'whole', '01-collision-repair')
    source = Path(fixture['path'])
    imported = request(fresh_service['base_url'], '/api/autocad/review', {
        'filename': source.name, 'content_base64': base64.b64encode(source.read_bytes()).decode(),
        'source_units': 'mm', 'document': {'name': 'Synthetic whole drawing'},
        'system_type': 'pressurized', 'clearance': CLEARANCE_MM, 'padding': 1500,
        'rule_profile': 'fmg-v3',
    })
    candidates = imported['candidates']
    assert len(candidates) == 1
    assert candidates[0]['source_handles'] == [fixture['pipe_handle']]
    assert candidates[0]['diameter_mm'] == DIAMETER_MM
    assert candidates[0]['review_status'] == 'attention'


def test_independent_distance_checker_catches_middle_of_long_segment():
    # Endpoint-only checks would miss this collision.
    assert segment_box_distance((-10, 0, 0), (10, 0, 0), (-1, -1, -1), (1, 1, 1)) == 0
    assert segment_box_distance((-10, 3, 0), (10, 3, 0), (-1, -1, -1), (1, 1, 1)) == 2


def test_fresh_nine_component_cad_detour_is_joined_and_shortened(fresh_service):
    """Five independent cylinder solids plus four quarter-bend solids form a run.

    The CAD importer must recover and join their circular-face geometry itself.
    No router input centerline is authored or replaced in this test.
    """
    document = ezdxf.new('R2010')
    document.header['$INSUNITS'] = 4
    radius = DIAMETER_MM / 2
    handles = []
    def text(values):
        return ' '.join(str(value) for value in values)
    def add_cylinder(start, end):
        length = math.dist(start, end)
        ux, uy, uz = [(b - a) / length for a, b in zip(start, end)]
        assert uz == 0
        data = cylinder_sat(radius, length)
        data[5] = f'transform $-1 -1 {-uy} {ux} 0 0 0 1 {ux} {uy} 0 {text(start)} 1 rotate no_reflect no_shear #'
        solid = document.modelspace().add_3dsolid()
        solid.sat = data
        handles.append(solid.dxf.handle)
    def add_bend(start, end, center):
        data = cylinder_sat(radius, 1)
        data[5] = 'transform $-1 -1 1 0 0 0 1 0 0 0 1 0 0 0 1 no_rotate no_reflect no_shear #'
        data[9] = f'torus-surface $-1 -1 $-1 {text(center)} 0 0 1 400 {radius} 1 0 0 forward_v I I I I #'
        for side, point in enumerate((start, end)):
            normal = [-(point[1] - center[1]) / 400, (point[0] - center[0]) / 400, 0]
            data[17 + side] = f'ellipse-curve $-1 -1 $-1 {text(point)} {text(normal)} 0 0 {radius} 1 I I #'
            data[19 + side] = f'point $-1 -1 $-1 {point[0]} {point[1]} {radius} #'
            data[27 + side] = f'plane-surface $-1 -1 $-1 {text(point)} {text(normal)} 0 0 1 forward_v I I I I #'
        solid = document.modelspace().add_3dsolid()
        solid.sat = data
        handles.append(solid.dxf.handle)
    add_cylinder((0, 0, 0), (1000, 0, 0))
    add_bend((1000, 0, 0), (1400, 400, 0), (1000, 400, 0))
    add_cylinder((1400, 400, 0), (1400, 1600, 0))
    add_bend((1400, 1600, 0), (1800, 2000, 0), (1800, 1600, 0))
    add_cylinder((1800, 2000, 0), (4200, 2000, 0))
    add_bend((4200, 2000, 0), (4600, 1600, 0), (4200, 1600, 0))
    add_cylinder((4600, 1600, 0), (4600, 400, 0))
    add_bend((4600, 400, 0), (5000, 0, 0), (5000, 400, 0))
    add_cylinder((5000, 0, 0), (6000, 0, 0))
    path = fresh_service['folder'] / 'nine-component-detour.dxf'
    document.saveas(path)
    before = path.read_bytes()
    imported = request(fresh_service['base_url'], '/api/autocad/inspect', {
        'filename': path.name, 'content_base64': base64.b64encode(before).decode(),
        'source_units': 'mm', 'selected_handles': [handles[0]],
        'document': {'name': path.name, 'fingerprint': 'synthetic-nine-component-case'},
    })
    chain, = [item for item in imported['candidates'] if item['kind'] == 'chain']
    assert chain['member_count'] == 9
    assert set(chain['source_handles']) == set(handles)
    assert chain['diameter_mm'] == DIAMETER_MM
    # Exact source arc length is 6800 mm of straight pipe plus four quarter arcs.
    # Import uses sampled arc chords, so retain its documented approximation.
    assert chain['length_mm'] == pytest.approx(6800 + 800 * math.pi, abs=2)
    prepared = request(fresh_service['base_url'], '/api/prepare-cad-layout', {
        'cad_id': imported['cad_id'], 'candidate_id': chain['id'], 'system_type': 'pressurized',
        'clearance': CLEARANCE_MM, 'padding': 1500, 'grid_step': 250, 'rule_profile': 'fmg-v3',
    })
    layout = prepared['layout']
    assert not layout['obstacles']
    assert not layout['cad_context']['endpoint_contacts']
    original_points = list(layout['pipe']['original_route'])
    layout['proposal'] = {'mode': 'rounded_pipe', 'bend_radius_mm': 304.8}
    checked = request(fresh_service['base_url'], '/api/run', {'layout': layout})
    result = checked['result']
    assert result['status'] == 'improved'
    assert result['assembly']['status'] == 'ready'
    assert result['assembly']['metrics']['centerline_length_mm'] == pytest.approx(6000)
    assert result['assembly']['metrics']['bend_count'] == 0
    assert {tuple(point) for point in (layout['pipe']['start'], layout['pipe']['end'])} == {(0, 0, 0), (6000, 0, 0)}
    assert result['assembly']['centerline_points'] == [layout['pipe']['start'], layout['pipe']['end']]
    assert result['assembly']['outer_diameter_mm'] == DIAMETER_MM
    assert layout['pipe']['original_route'] == original_points
    assert path.read_bytes() == before
