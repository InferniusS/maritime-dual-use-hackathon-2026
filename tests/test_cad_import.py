"""Known geometry tests for the preview importer, independent of private CAD."""
from pathlib import Path
import hashlib
import math
from types import SimpleNamespace

import ezdxf
from ezdxf.acis import sat
import pytest

from pipe_router.cad_import import inspect_cad, _parse_acis, _candidate_chains


def cylinder_sat(translation=(10.125, 20.25, 30.5)):
    # Independently constructed ACIS fixture: a radius 2 cylinder from z=0 to 10.
    # It has one lateral face and two complete circular boundary loops.
    tx, ty, tz = translation
    records = [
        'body $-1 -1 $-1 $1 $-1 $2 #',
        'lump $-1 -1 $-1 $-1 $3 $0 #',
        f'transform $-1 -1 1 0 0 0 1 0 0 0 1 {tx} {ty} {tz} 1 no_rotate no_reflect no_shear #',
        'shell $-1 -1 $-1 $-1 $-1 $4 $-1 $1 #',
        'face $-1 -1 $-1 $-1 $5 $3 $-1 $6 forward single #',
        'loop $-1 -1 $-1 $7 $8 $4 #',
        'cone-surface $-1 -1 $-1 0 0 0 0 0 1 2 0 0 1 I I 0 1 2 forward I I I I #',
        'loop $-1 -1 $-1 $-1 $9 $4 #',
        'coedge $-1 -1 $-1 $8 $8 $-1 $10 forward $5 $-1 #',
        'coedge $-1 -1 $-1 $9 $9 $-1 $11 forward $7 $-1 #',
        'edge $-1 -1 $-1 $12 0 $12 6.283185307179586 $8 $14 forward @7 unknown #',
        'edge $-1 -1 $-1 $13 0 $13 6.283185307179586 $9 $15 forward @7 unknown #',
        'vertex $-1 -1 $-1 $10 2 $16 #',
        'vertex $-1 -1 $-1 $11 2 $17 #',
        'ellipse-curve $-1 -1 $-1 0 0 0 0 0 1 2 0 0 1 I I #',
        'ellipse-curve $-1 -1 $-1 0 0 10 0 0 1 2 0 0 1 I I #',
        'point $-1 -1 $-1 2 0 0 #',
        'point $-1 -1 $-1 2 0 10 #',
        'End-of-ACIS-data',
    ]
    return ['21500 18 1 0 ', '@4 Test @4 Test @4 Test ', '1 1e-7 1e-10 ', *records]


def make_cylinder_dxf(path, *, in_block=False, nonuniform=False):
    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 0
    space = doc.blocks.new('TUBE') if in_block else doc.modelspace()
    solid = space.add_3dsolid(dxfattribs={'layer': '0'})
    solid.sat = cylinder_sat()
    if in_block:
        doc.modelspace().add_blockref('TUBE', (100, 200, 300), dxfattribs={
            'rotation': 90, 'xscale': 2, 'yscale': 3 if nonuniform else 2,
            'zscale': 2, 'layer': 'PIPE',
        })
    doc.saveas(path)
    return path


def test_actual_source_geometry_and_body_transform(tmp_path):
    source = make_cylinder_dxf(tmp_path / 'cylinder.dxf')
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    report = inspect_cad(source)
    assert report['counts']['parsed_solid_instances'] == 1
    assert report['counts']['surface_meshes'] == 1
    assert report['counts']['boundary_curves'] == 2
    assert report['bounds']['min'] == pytest.approx([8.125, 18.25, 30.5])
    assert report['bounds']['max'] == pytest.approx([12.125, 22.25, 40.5])
    axis = report['axis_candidates'][0]
    assert axis['points'][0] == pytest.approx([10.125, 20.25, 30.5])
    assert axis['points'][1] == pytest.approx([10.125, 20.25, 40.5])
    assert axis['geometric_radius'] == 2
    assert report['routes'] == []
    assert report['route_candidates'][0]['requires_review']
    assert report['route_candidates'][0]['geometric_diameter'] == 4
    assert not report['pipe_graph_ready']
    assert before == hashlib.sha256(source.read_bytes()).hexdigest()


def test_insert_transform_applied_after_acis_transform(tmp_path):
    report = inspect_cad(make_cylinder_dxf(tmp_path / 'insert.dxf', in_block=True))
    axis = report['axis_candidates'][0]
    assert axis['points'][0] == pytest.approx([59.5, 220.25, 361])
    assert axis['points'][1] == pytest.approx([59.5, 220.25, 381])
    assert axis['geometric_radius'] == 4
    assert axis['instance_path']
    assert all(o['layer'] == 'PIPE' for o in report['objects'])


def test_nonuniform_insert_has_no_scalar_pipe_radius(tmp_path):
    report = inspect_cad(make_cylinder_dxf(tmp_path / 'stretch.dxf', in_block=True, nonuniform=True))
    assert report['axis_candidates'][0]['geometric_radius'] is None
    assert report['route_candidates'] == []


def test_unit_change_rescales_all_geometry(tmp_path):
    path = make_cylinder_dxf(tmp_path / 'units.dxf')
    mm, inches = inspect_cad(path, 'mm'), inspect_cad(path, 'in')
    assert inches['bounds']['max'] == pytest.approx([v * 25.4 for v in mm['bounds']['max']])
    assert inches['axis_candidates'][0]['geometric_radius'] == pytest.approx(50.8)
    assert inches['route_candidates'][0]['geometric_diameter'] == pytest.approx(101.6)
    assert inches['units'] == 'mm'
    assert inches['unit_assumption']
    with pytest.raises(ValueError):
        inspect_cad(path, 'feet')


def test_dxf_sat_chunk_split_does_not_corrupt_number():
    lines = cylinder_sat()
    transform = lines[5]
    cut = transform.index('10.125') + 3
    lines[5:6] = [transform[:cut], transform[cut:]]
    builder = _parse_acis(SimpleNamespace(sat=lines, sab=b''))
    matrix = next(e for e in builder.entities if e.name == 'transform')
    assert float(matrix.data[9]) == 10.125


def test_units_header_agreement_and_explicit_curve_not_claimed_pipe(tmp_path):
    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 4
    doc.modelspace().add_line((1, 2, 3), (4, 5, 6))
    path = tmp_path / 'line.dxf'
    doc.saveas(path)
    report = inspect_cad(path)
    assert not report['unit_assumption']
    assert report['objects'][0]['geometry']['points'] == [[1, 2, 3], [4, 5, 6]]
    assert not report['objects'][0]['pipe_centerline_verified']
    assert report['route_candidates'] == []


def test_unknown_geometry_is_reported(tmp_path):
    doc = ezdxf.new('R2010')
    doc.modelspace().add_point((1, 2, 3))
    path = tmp_path / 'point.dxf'
    doc.saveas(path)
    report = inspect_cad(path)
    assert report['bounds'] is None
    assert report['unsupported_geometry'] == {'POINT': 1}
    assert not report['compliance_verified']


def test_polyline_virtual_entities_have_unique_ids(tmp_path):
    doc = ezdxf.new('R2010')
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10)])
    path = tmp_path / 'polyline.dxf'
    doc.saveas(path)
    report = inspect_cad(path)
    assert len(report['objects']) == 2
    assert len({o['id'] for o in report['objects']}) == 2


def test_candidate_chain_rejects_branches_and_different_radius():
    def axis(start, end, radius, record):
        return {'source_handle': 'S1', 'instance_path': [], 'kind': 'cylinder',
                'points': [start, end], 'geometric_radius': radius,
                'source_record': record, 'confidence': 'analytic_geometry'}
    first = axis([0,0,0], [0,0,10], 2, 1)
    second = axis([0,0,10], [0,0,20], 2, 2)
    assert len(_candidate_chains([first, second])) == 1
    assert _candidate_chains([first, second, axis([0,0,10], [0,10,10], 2, 3)]) == []
    assert _candidate_chains([first, axis([0,0,10], [0,0,20], 3, 2)]) == []


def test_torus_short_arc_is_explicitly_inferred(tmp_path):
    doc = ezdxf.new('R2010')
    solid = doc.modelspace().add_3dsolid()
    data = cylinder_sat(translation=(0, 0, 0))
    data[3 + 6] = 'torus-surface $-1 -1 $-1 0 0 0 0 0 1 10 2 1 0 0 forward_v I I I I #'
    data[3 + 14] = 'ellipse-curve $-1 -1 $-1 10 0 0 0 1 0 2 0 0 1 I I #'
    data[3 + 15] = 'ellipse-curve $-1 -1 $-1 0 10 0 -1 0 0 0 2 0 1 I I #'
    solid.sat = data
    path = tmp_path / 'torus.dxf'
    doc.saveas(path)
    report = inspect_cad(path)
    axis = report['axis_candidates'][0]
    assert axis['confidence'] == 'inferred_short_arc'
    assert axis['points'][0] == pytest.approx([10, 0, 0])
    assert axis['points'][-1] == pytest.approx([0, 10, 0])
    assert all(math.hypot(*p[:2]) == pytest.approx(10) for p in axis['points'])
    assert report['route_candidates'][0]['contains_inferred_bend']
    assert any('shorter arc' in warning for warning in report['warnings'])


def test_unparseable_solid_is_reported_without_source_edit(tmp_path):
    doc = ezdxf.new('R2010')
    solid = doc.modelspace().add_3dsolid()
    solid.sat = ['invalid', 'data', 'more', 'body #']
    path = tmp_path / 'bad-solid.dxf'
    doc.saveas(path)
    report = inspect_cad(path)
    assert report['counts']['failed_solid_instances'] == 1
    assert report['objects'] == []
    assert any('failed extraction' in warning for warning in report['warnings'])


def test_nested_insert_shear_does_not_claim_scalar_radius(tmp_path):
    doc = ezdxf.new('R2010')
    tube = doc.blocks.new('TUBE')
    solid = tube.add_3dsolid()
    solid.sat = cylinder_sat()
    wrapper = doc.blocks.new('WRAPPER')
    wrapper.add_blockref('TUBE', (0,0,0), dxfattribs={'rotation': 45})
    doc.modelspace().add_blockref('WRAPPER', (0,0,0), dxfattribs={
        'xscale': 2, 'yscale': 1, 'zscale': math.sqrt(2.5),
    })
    path = tmp_path / 'nested-shear.dxf'
    doc.saveas(path)
    report = inspect_cad(path)
    assert report['counts']['insert_instances'] == 2
    assert report['axis_candidates'][0]['geometric_radius'] is None
    assert report['route_candidates'] == []


def _ring_mesh_object(kind='torus', rings=8):
    from pipe_router.cad_import import _rings_mesh
    vertices, faces = _rings_mesh([
        [[math.cos(math.tau * j / 32), math.sin(math.tau * j / 32), row]
         for j in range(32)] for row in range(rings)
    ])
    return {'id': 'mesh', 'type': '3DSOLID', 'surface_type': kind,
            'geometry': {'kind': 'mesh', 'vertices': vertices, 'faces': faces}}


def _large_preview_result(mesh):
    # Boundary curves make the drawing large without duplicating every mesh.
    return {'objects': [mesh] + [
        {'id': f'edge{i}', 'type': '3DSOLID',
         'geometry': {'kind': 'polyline', 'points': [[0,0,0], [1,0,0]]}}
        for i in range(2000)
    ], 'counts': {'acis_faces': 1}, 'warnings': ['Original source warning']}


def test_small_preview_is_unchanged():
    from pipe_router.cad_import import lightweight_preview
    result = {'objects': [_ring_mesh_object()], 'warnings': []}
    assert lightweight_preview(result) is result
    assert 'preview' not in result


def test_lightweight_preview_preserves_end_rings_and_mesh_connectivity():
    from collections import Counter
    from pipe_router.cad_import import lightweight_preview
    mesh = _ring_mesh_object(rings=8)
    result = _large_preview_result(mesh)
    preview = lightweight_preview(result)
    geometry = preview['objects'][0]['geometry']
    vertices, faces = geometry['vertices'], geometry['faces']
    assert len(vertices) == 4 * 8  # Original rows 0, 3, 6 and 7.
    assert len(faces) == 3 * 8 * 2
    assert vertices[:8] == mesh['geometry']['vertices'][:32:4]
    assert vertices[-8:] == mesh['geometry']['vertices'][-32::4]
    assert {p[2] for p in vertices} == {0, 3, 6, 7}
    used = set()
    edges = Counter()
    for triangle in faces:
        assert len(set(triangle)) == 3
        assert all(0 <= index < len(vertices) for index in triangle)
        used.update(triangle)
        for a, b in zip(triangle, triangle[1:] + triangle[:1]):
            edges[tuple(sorted((a, b)))] += 1
    assert used == set(range(len(vertices)))
    assert set(edges.values()) == {1, 2}
    boundary_edges = [edge for edge, count in edges.items() if count == 1]
    assert len(boundary_edges) == 16
    assert all((a < 8 and b < 8) or (a >= 24 and b >= 24) for a, b in boundary_edges)
    assert preview['preview']['mode'] == 'reduced_surfaces'
    assert preview['preview']['omitted_solid_boundary_curves'] == 2000
    assert preview['preview']['displayed_mesh_vertices'] == 32


def test_lightweight_preview_does_not_mutate_full_download_or_drop_explicit_curves():
    import copy
    from pipe_router.cad_import import lightweight_preview
    result = _large_preview_result(_ring_mesh_object(kind='cylinder', rings=2))
    explicit = {'id': 'line', 'type': 'LINE',
                'geometry': {'kind': 'polyline', 'points': [[10,20,30], [40,50,60]]}}
    result['objects'].append(explicit)
    original = copy.deepcopy(result)
    preview = lightweight_preview(result)
    assert result == original
    assert preview['counts'] == result['counts']
    assert len(preview['warnings']) == len(result['warnings']) + 1
    assert preview['objects'][-1] == explicit
    assert preview['preview']['retained_explicit_curves'] == 1
    assert preview['preview']['total_object_count'] == 2002
    assert preview['preview']['displayed_object_count'] == 2
    assert len(preview['objects'][0]['geometry']['vertices']) == 16


@pytest.mark.parametrize('failure', ['unknown_surface', 'wrong_faces', 'wrong_ring_size', 'wrong_cylinder_rings'])
def test_lightweight_preview_leaves_unsupported_mesh_unchanged(failure):
    from pipe_router.cad_import import lightweight_preview
    mesh = _ring_mesh_object()
    if failure == 'unknown_surface':
        mesh['surface_type'] = 'spline'
    elif failure == 'wrong_faces':
        mesh['geometry']['faces'][0] = [0, 2, 32]
    elif failure == 'wrong_ring_size':
        mesh['geometry']['vertices'].pop()
    elif failure == 'wrong_cylinder_rings':
        mesh['surface_type'] = 'cylinder'
    preview = lightweight_preview(_large_preview_result(mesh))
    assert preview['objects'][0] is mesh
    assert preview['preview']['unchanged_mesh_count'] == 1
    assert preview['preview']['reduced_mesh_count'] == 0
