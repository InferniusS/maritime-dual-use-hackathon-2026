"""Fresh mixed CAD must produce different, source-traceable scan outcomes.

These authored analytic fixtures exercise DXF import and the live service. They
are invented test geometry, not organizer models or an AutoCAD runtime claim.
No extracted points, route IDs, or obstacle records are replaced after import.
"""
import hashlib
import json
import math
import random

import ezdxf
from ezdxf.acis import api as acis
from ezdxf.render import forms
import pytest

from test_autocad import autocad_workspace, payload_for
from test_fresh_cad_acceptance import cylinder_sat, segment_box_distance
from test_system_api import wait_job
from test_web import json_request

OD = 114.3
CLEARANCE = 50.8


def make_mixed_drawing(destination, *, seed=41, translation=(0, 0, 0), block=False):
    """Author four independent zones; entity order deliberately does not match roles."""
    document = ezdxf.new('R2010')
    document.units = ezdxf.units.MM
    space = document.blocks.new('INVENTED_MIXED_CASE') if block else document.modelspace()
    records = []
    handles = {key: [] for key in ('clear', 'collision', 'detour', 'branch', 'wall', 'unsupported', 'curve')}
    radius = OD / 2
    def cylinder(role, start, end):
        records.append((role, 'cylinder', start, end))
    def bend(start, end, center):
        records.append(('detour', 'bend', start, end, center))
    cylinder('clear', (0, 0, 0), (6000, 0, 0))
    cylinder('collision', (0, 10000, 0), (6000, 10000, 0))
    wall_low, wall_high = (2800, 9400, -600), (3200, 10600, 600)
    records.append(('wall', 'box', wall_low, wall_high))
    # Four quarter-circle bends and five straight solids form a needless detour.
    # Exact length is 6800 + 800*pi; a same-endpoint straight route is 6000 mm.
    cylinder('detour', (0, 20000, 0), (1000, 20000, 0))
    bend((1000, 20000, 0), (1400, 20400, 0), (1000, 20400, 0))
    cylinder('detour', (1400, 20400, 0), (1400, 21600, 0))
    bend((1400, 21600, 0), (1800, 22000, 0), (1800, 21600, 0))
    cylinder('detour', (1800, 22000, 0), (4200, 22000, 0))
    bend((4200, 22000, 0), (4600, 21600, 0), (4200, 21600, 0))
    cylinder('detour', (4600, 21600, 0), (4600, 20400, 0))
    bend((4600, 20400, 0), (5000, 20000, 0), (5000, 20400, 0))
    cylinder('detour', (5000, 20000, 0), (6000, 20000, 0))
    cylinder('branch', (-1000, 32000, 0), (0, 32000, 0))
    cylinder('branch', (0, 32000, 0), (1000, 32000, 0))
    cylinder('branch', (0, 32000, 0), (0, 33000, 0))
    records += [('unsupported', 'point', (0, 50000, 0)),
                ('curve', 'line', (0, 52000, 0), (6000, 52000, 0))]
    random.Random(seed).shuffle(records)
    text = lambda values: ' '.join(str(value) for value in values)
    for role, kind, *data in records:
        if kind == 'point':
            entity = space.add_point(data[0])
        elif kind == 'line':
            entity = space.add_line(*data)
        elif kind == 'box':
            low, high = data
            mesh = forms.cube(center=False)
            mesh.scale(*(high[i] - low[i] for i in range(3)))
            mesh.translate(*low)
            entity = space.add_3dsolid()
            acis.export_dxf(entity, [acis.body_from_mesh(mesh)])
        elif kind == 'cylinder':
            start, end = data
            length = math.dist(start, end)
            ux, uy, uz = [(b - a) / length for a, b in zip(start, end)]
            assert uz == 0
            sat = cylinder_sat(radius, length)
            sat[5] = f'transform $-1 -1 {-uy} {ux} 0 0 0 1 {ux} {uy} 0 {text(start)} 1 rotate no_reflect no_shear #'
            entity = space.add_3dsolid()
            entity.sat = sat
        else:
            start, end, center = data
            sat = cylinder_sat(radius, 1)
            sat[5] = 'transform $-1 -1 1 0 0 0 1 0 0 0 1 0 0 0 1 no_rotate no_reflect no_shear #'
            sat[9] = f'torus-surface $-1 -1 $-1 {text(center)} 0 0 1 400 {radius} 1 0 0 forward_v I I I I #'
            for side, point in enumerate((start, end)):
                normal = [-(point[1] - center[1]) / 400, (point[0] - center[0]) / 400, 0]
                sat[17 + side] = f'ellipse-curve $-1 -1 $-1 {text(point)} {text(normal)} 0 0 {radius} 1 I I #'
                sat[19 + side] = f'point $-1 -1 $-1 {point[0]} {point[1]} {radius} #'
                sat[27 + side] = f'plane-surface $-1 -1 $-1 {text(point)} {text(normal)} 0 0 1 forward_v I I I I #'
            entity = space.add_3dsolid()
            entity.sat = sat
        handles[role].append(entity.dxf.handle)
    placement = document.modelspace().add_blockref('INVENTED_MIXED_CASE', translation) if block else None
    if not block:
        assert translation == (0, 0, 0)
    document.saveas(destination)
    moved = lambda point: [a + b for a, b in zip(point, translation)]
    return {'handles': handles, 'placement_handle': placement.dxf.handle if placement else None,
            'wall_min': moved(wall_low), 'wall_max': moved(wall_high),
            'clear_endpoints': [moved((0, 0, 0)), moved((6000, 0, 0))],
            'collision_endpoints': [moved((0, 10000, 0)), moved((6000, 10000, 0))],
            'detour_endpoints': [moved((0, 20000, 0)), moved((6000, 20000, 0))]}


@pytest.mark.parametrize('seed,translation,block', [
    (41, (0, 0, 0), False),
    (97, (31700, -12200, 4300), True),
])
def test_fresh_mixed_cad_scan_distinguishes_clear_collision_detour_and_unknown_topology(
        tmp_path, autocad_workspace, seed, translation, block):
    source = tmp_path / f'invented-mixed-{seed}.dxf'
    fixture = make_mixed_drawing(source, seed=seed, translation=translation, block=block)
    before = source.read_bytes()
    payload = payload_for(source, [])
    payload.update(system_type='pressurized', clearance=CLEARANCE, padding=1500, rule_profile='fmg-v3')
    status, pending = json_request(autocad_workspace, 'POST', '/api/autocad/scan', payload)
    assert status == 202, pending
    job = wait_job(autocad_workspace, pending['status_url'])
    assert job['status'] == 'complete', job
    summary = job['summary']
    assert summary['source_sha256'] == hashlib.sha256(before).hexdigest()
    assert source.read_bytes() == before
    assert summary['review']['total_supported_routes'] == 6
    assert summary['review']['reviewed_routes'] == 6
    assert summary['review']['not_processed_routes'] == summary['review']['omitted_from_list'] == 0
    assert not summary['review']['complete_model_verified']
    rows = summary['candidates']
    assert {row['number'] for row in rows} == set(range(1, 7))
    def role_row(role):
        wanted = set(fixture['handles'][role])
        matches = [row for row in rows if set(row['source_handles']) == wanted]
        assert len(matches) == 1
        return matches[0]
    clear, collision, detour = (role_row(role) for role in ('clear', 'collision', 'detour'))
    assert clear['review_status'] == 'no_obvious_detour'
    assert clear['issue_count'] == clear['endpoint_contact_count'] == 0
    assert collision['review_status'] == 'attention'
    assert collision['endpoint_contact_count'] == 0  # Known collision is midway along the pipe.
    hits = [finding for finding in collision['findings'] if finding['code'] == 'obstacle_collision']
    assert len(hits) == 1
    assert hits[0]['obstacle_source']['source_handle'] == fixture['handles']['wall'][0]
    assert detour['review_status'] == 'opportunity'
    assert detour['member_count'] == 9
    assert detour['issue_count'] == 0
    assert detour['length_mm'] == pytest.approx(6800 + 800 * math.pi, abs=2)
    assert detour['axis_backtracking_mm'] == pytest.approx(4000, abs=1e-7)
    # Independent numeric truth comes from authored solid positions, not the
    # router's own collision utility or its inferred classification.
    assert segment_box_distance(*fixture['clear_endpoints'], fixture['wall_min'], fixture['wall_max']) > OD / 2 + CLEARANCE
    assert segment_box_distance(*fixture['collision_endpoints'], fixture['wall_min'], fixture['wall_max']) == 0
    assert math.dist(*fixture['detour_endpoints']) == 6000
    assert detour['length_mm'] > math.dist(*fixture['detour_endpoints']) + 3000
    for role, row in (('clear', clear), ('collision', collision), ('detour', detour)):
        assert {tuple(row['start']), tuple(row['end'])} == {tuple(point) for point in fixture[role + '_endpoints']}
        assert row['diameter_mm'] == OD
        assert not row['full_compliance_verified']
        if block:
            assert all(instance['instance_path'] for instance in row['source_instances'])
    branch_rows = [row for row in rows if set(row['source_handles']).issubset(set(fixture['handles']['branch']))]
    assert len(branch_rows) == 3  # The three-way junction must not become an invented through-run.
    assert all(row['member_count'] == 1 for row in branch_rows)
    assert summary['network_counts']['branch_or_ambiguous_junctions'] == 1
    assert summary['inventory_findings']['branch_or_ambiguous_junctions']
    geometry = summary['review']['geometry_counts']
    assert geometry['unsupported_geometry_records']['POINT'] == 1
    assert geometry['unsupported_objects'] == 1
    assert geometry['explicit_curve_objects_unchecked'] == 1
    assert not set(fixture['handles']['unsupported'] + fixture['handles']['curve']) & {handle for row in rows for handle in row['source_handles']}
    cached = json.loads((autocad_workspace['output'] / pending['cad_id'] / 'cad-inspection.json').read_text())
    assert cached['counts']['solid_instances'] == 15
    assert {row['review_status'] for row in rows} == {'no_obvious_detour', 'attention', 'opportunity'}
