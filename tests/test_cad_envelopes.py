"""Conservative analytic envelopes do not use the shorter-arc display mesh."""
from copy import deepcopy
import math
from types import SimpleNamespace

import ezdxf
from ezdxf.math import Matrix44, Vec3
import pytest

from pipe_router import cad_import
from pipe_router.cad_import import inspect_cad, _parse_acis, _solid_envelope
from test_cad_import import cylinder_sat, make_cylinder_dxf


def torus_sat():
    data = cylinder_sat(translation=(0, 0, 0))
    data[3 + 6] = 'torus-surface $-1 -1 $-1 0 0 0 0 0 1 10 2 1 0 0 forward_v I I I I #'
    data[3 + 14] = 'ellipse-curve $-1 -1 $-1 10 0 0 0 1 0 2 0 0 1 I I #'
    data[3 + 15] = 'ellipse-curve $-1 -1 $-1 0 10 0 -1 0 0 0 2 0 1 I I #'
    return data


def annulus_sat():
    data = cylinder_sat(translation=(0, 0, 0))
    data[3 + 6] = 'plane-surface $-1 -1 $-1 0 0 0 0 0 1 1 0 0 forward_v I I I I #'
    data[3 + 15] = 'ellipse-curve $-1 -1 $-1 0 0 0 0 0 1 3 0 0 1 I I #'
    return data


def inspect_sat(tmp_path, data, name='surface'):
    doc = ezdxf.new('R2010')
    doc.modelspace().add_3dsolid().sat = data
    path = tmp_path / f'{name}.dxf'
    doc.saveas(path)
    return inspect_cad(path)


def builder_for(data):
    return _parse_acis(SimpleNamespace(sat=data, sab=b''))


def envelope_for(builder, matrix=None):
    return _solid_envelope(builder, matrix or Matrix44(), 1.0,
                           {'source_handle': 'fixture', 'instance_path': []})


def point_capsule_distance(point, capsule):
    point, start, end = Vec3(point), Vec3(capsule['start']), Vec3(capsule['end'])
    delta = end - start
    t = max(0, min(1, (point - start).dot(delta) / delta.magnitude_square)) if delta.magnitude_square else 0
    return (point - start - delta * t).magnitude


def test_cylinder_analytic_extent_and_body_transform(tmp_path):
    path = make_cylinder_dxf(tmp_path / 'cylinder.dxf')
    before = path.read_bytes()
    result = inspect_cad(path)
    envelope, = result['solid_envelopes']
    assert envelope['eligible']
    assert envelope['reason'] == 'all_source_faces_supported'
    assert envelope['face_count'] == envelope['supported_face_count'] == 1
    capsule, = envelope['capsules']
    assert capsule['start'] == pytest.approx([10.125, 20.25, 30.5])
    assert capsule['end'] == pytest.approx([10.125, 20.25, 40.5])
    assert 2 < capsule['radius'] < 2.000001
    assert envelope['source_handle'] == result['objects'][0]['source_handle']
    assert path.read_bytes() == before


def test_repeated_rotated_uniform_inserts_have_distinct_world_envelopes(tmp_path):
    doc = ezdxf.new('R2010')
    tube = doc.blocks.new('TUBE')
    tube.add_3dsolid().sat = cylinder_sat()
    doc.modelspace().add_blockref('TUBE', (100, 200, 300), dxfattribs={
        'rotation': 90, 'xscale': 2, 'yscale': 2, 'zscale': 2})
    doc.modelspace().add_blockref('TUBE', (0, 0, 0), dxfattribs={
        'rotation': 180, 'xscale': 3, 'yscale': 3, 'zscale': 3})
    path = tmp_path / 'repeated.dxf'
    doc.saveas(path)
    first, second = inspect_cad(path)['solid_envelopes']
    assert first['eligible'] and second['eligible']
    assert first['source_handle'] == second['source_handle']
    assert first['instance_path'] != second['instance_path']
    a, b = first['capsules'][0], second['capsules'][0]
    assert a['start'] == pytest.approx([59.5, 220.25, 361])
    assert a['end'] == pytest.approx([59.5, 220.25, 381])
    assert a['radius'] == pytest.approx(4, abs=1e-6)
    assert b['start'] == pytest.approx([-30.375, -60.75, 91.5])
    assert b['end'] == pytest.approx([-30.375, -60.75, 121.5])
    assert b['radius'] == pytest.approx(6, abs=1e-6)


def test_inch_conversion_applies_to_capsules(tmp_path):
    path = make_cylinder_dxf(tmp_path / 'units.dxf')
    mm = inspect_cad(path, 'mm')['solid_envelopes'][0]['capsules'][0]
    inch = inspect_cad(path, 'in')['solid_envelopes'][0]['capsules'][0]
    assert inch['start'] == pytest.approx([v * 25.4 for v in mm['start']])
    assert inch['end'] == pytest.approx([v * 25.4 for v in mm['end']])
    assert inch['radius'] == pytest.approx(mm['radius'] * 25.4)


@pytest.mark.parametrize('matrix', [
    Matrix44.scale(2, 3, 2),
    Matrix44.chain(Matrix44.z_rotate(math.pi / 4), Matrix44.scale(2, 1, math.sqrt(2.5))),
])
def test_nonuniform_or_sheared_transform_never_eligible(matrix):
    envelope = envelope_for(builder_for(cylinder_sat()), matrix)
    assert not envelope['eligible']
    assert envelope['capsules'] == []
    assert envelope['reason'] == 'nonuniform_or_sheared_transform'
    assert envelope['supported_face_count'] == 0


def test_torus_full_circle_and_chord_sag_cover_beyond_short_preview(tmp_path):
    result = inspect_sat(tmp_path, torus_sat())
    envelope, = result['solid_envelopes']
    assert envelope['eligible']
    capsules = envelope['capsules']
    count = len(capsules)
    assert count >= 48
    assert capsules[-1]['end'] == pytest.approx(capsules[0]['start'])
    assert all(capsules[i]['end'] == pytest.approx(capsules[(i + 1) % count]['start']) for i in range(count))
    assert capsules[0]['radius'] > 2 + 10 * (1 - math.cos(math.pi / count))
    # The visual inference shows only the first quadrant. The envelope also
    # covers the rest of the analytic torus including maximum chord deviation.
    assert min(p[0] for p in result['axis_candidates'][0]['points']) >= -1e-8
    assert min(p['start'][0] for p in capsules) < -9.9
    for index in range(count * 2):
        angle = math.tau * index / (count * 2)
        for tube_angle in (0, math.pi / 4, math.pi / 2, math.pi, 3 * math.pi / 2):
            radial = 10 + 2 * math.cos(tube_angle)
            point = [radial * math.cos(angle), radial * math.sin(angle), 2 * math.sin(tube_angle)]
            assert min(point_capsule_distance(point, cap) - cap['radius'] for cap in capsules) <= 1e-9


def test_full_torus_envelope_does_not_require_supported_preview_trims(tmp_path):
    data = torus_sat()
    # A half-circle trim is unsupported by the simple display-patch extractor.
    data[3 + 10] = data[3 + 10].replace('6.283185307179586', '3.141592653589793')
    result = inspect_sat(tmp_path, data)
    assert result['counts'].get('surface_meshes', 0) == 0
    assert result['solid_envelopes'][0]['eligible']
    assert len(result['solid_envelopes'][0]['capsules']) == 48


def test_torus_rotated_scaled_transformed_samples_are_contained():
    matrix = Matrix44.chain(Matrix44.x_rotate(0.63), Matrix44.z_rotate(0.48),
                            Matrix44.scale(2.75), Matrix44.translate(123, -456, 789))
    envelope = envelope_for(builder_for(torus_sat()), matrix)
    assert envelope['eligible']
    for index in range(97):
        angle = math.tau * index / 97
        point = matrix.transform(Vec3(12 * math.cos(angle), 12 * math.sin(angle), 0))
        assert min(point_capsule_distance(point, cap) - cap['radius'] for cap in envelope['capsules']) <= 1e-8


def test_annulus_sphere_contains_outer_disk(tmp_path):
    result = inspect_sat(tmp_path, annulus_sat())
    envelope, = result['solid_envelopes']
    assert envelope['eligible']
    capsule, = envelope['capsules']
    assert capsule['start'] == capsule['end'] == [0, 0, 0]
    assert 3 < capsule['radius'] < 3.000001
    for i in range(64):
        point = [3 * math.cos(math.tau * i / 64), 3 * math.sin(math.tau * i / 64), 0]
        assert point_capsule_distance(point, capsule) <= capsule['radius']


def test_unsupported_face_in_another_body_rejects_whole_source_solid():
    first = builder_for(cylinder_sat())
    second_data = cylinder_sat()
    second_data[3 + 6] = 'sphere-surface $-1 -1 $-1 0 0 0 3 0 0 1 1 0 0 forward_v I I I I #'
    second = builder_for(second_data)
    # The importer must account for every body in one source entity.
    envelope = envelope_for(SimpleNamespace(entities=[*first.entities, *second.entities]))
    assert not envelope['eligible']
    assert envelope['face_count'] == 2
    assert envelope['supported_face_count'] == 1
    assert envelope['capsules'] == []
    assert 'unsupported_face:sphere-surface' in envelope['reason']


def test_partial_extraction_discards_previously_supported_capsules(tmp_path, monkeypatch):
    def fail_edge(_edge):
        raise ValueError('Injected failed boundary extraction')
    monkeypatch.setattr(cad_import, '_edge_points', fail_edge)
    result = inspect_cad(make_cylinder_dxf(tmp_path / 'partial.dxf'))
    envelope, = result['solid_envelopes']
    assert envelope['supported_face_count'] == 1
    assert not envelope['eligible']
    assert envelope['capsules'] == []
    assert envelope['reason'] == 'partial_or_failed_solid_extraction'


def test_empty_and_invalid_solids_keep_ineligible_records(tmp_path):
    doc = ezdxf.new('R2010')
    doc.modelspace().add_3dsolid()
    doc.modelspace().add_3dsolid().sat = ['invalid', 'data', 'more', 'body #']
    path = tmp_path / 'invalid.dxf'
    doc.saveas(path)
    records = inspect_cad(path)['solid_envelopes']
    # ezdxf omits the wholly blank entity when serializing a DXF.
    assert len(records) == 1
    assert all(not record['eligible'] and not record['capsules'] for record in records)
    assert all(record['face_count'] == 0 for record in records)
    empty = envelope_for(SimpleNamespace(entities=[]))
    assert not empty['eligible']
    assert empty['reason'] == 'no_source_faces'


def test_identical_ring_faces_are_compacted_without_truncating_coverage():
    builder = builder_for(torus_sat())
    face = next(entity for entity in builder.entities if entity.name == 'face')
    # These six faces describe the very same ring. Keep one complete covering
    # envelope instead of reverting to a box solely due to repeated surfaces.
    envelope = envelope_for(SimpleNamespace(entities=[face] * 6))
    assert envelope['face_count'] == envelope['supported_face_count'] == 6
    assert envelope['eligible']
    assert len(envelope['capsules']) == 48
    assert envelope['source_capsule_count'] == 288
    assert envelope['redundant_capsules_removed'] == 240


def test_capsule_limit_still_falls_back_for_distinct_rings():
    entities = []
    for index in range(6):
        data = torus_sat()
        data[3 + 6] = data[3 + 6].replace('0 0 0 0 0 1 10 2', f'{100 * index} 0 0 0 0 1 10 2')
        entities.extend(builder_for(data).entities)
    envelope = envelope_for(SimpleNamespace(entities=entities))
    assert not envelope['eligible']
    assert envelope['capsules'] == []
    assert envelope['reason'] == 'capsule_limit_exceeded'
    assert envelope['retained_capsule_count'] == 288


def test_capsule_containment_requires_both_axis_and_clip_coverage():
    outer = {'start': [0, 0, 0], 'end': [10, 0, 0], 'radius': 3,
             'clip_min': [0, -3, -3], 'clip_max': [10, 3, 3]}
    inner = {'start': [1, 0, 0], 'end': [9, 0, 0], 'radius': 2,
             'clip_min': [1, -2, -2], 'clip_max': [9, 2, 2]}
    assert cad_import._capsule_contains(outer, inner)
    beyond_clip = {**inner, 'clip_min': [-1, -2, -2]}
    assert not cad_import._capsule_contains(outer, beyond_clip)
    beyond_radius = {**inner, 'start': [1, 2, 0], 'end': [9, 2, 0]}
    assert not cad_import._capsule_contains(outer, beyond_radius)
    # An unclipped capsule includes its round ends and cannot be discarded by
    # a finite cylinder whose clipping planes cut those ends off.
    assert not cad_import._capsule_contains(outer, {key: value for key, value in inner.items() if not key.startswith('clip_')})


def test_capsule_containment_does_not_treat_nearly_equal_as_equal():
    outer = {'start': [0, 0, 0], 'end': [10, 0, 0], 'radius': 2}
    inner = {'start': [0, 0.00000001, 0], 'end': [10, 0.00000001, 0], 'radius': 2}
    assert not cad_import._capsule_contains(outer, inner)
    assert cad_import._capsule_contains(outer, {**outer, 'start': outer['end'], 'end': outer['start']})


def test_compaction_keeps_largest_exact_capsule_and_all_distinct_clips():
    small = {'start': [0, 0, 0], 'end': [10, 0, 0], 'radius': 1}
    large = {**small, 'radius': 2}
    clipped = {**large, 'clip_min': [0, -2, -2], 'clip_max': [10, 2, 2]}
    capsules = [small, large, {**large, 'start': large['end'], 'end': large['start']}, clipped]
    before = deepcopy(capsules)
    result = cad_import._compact_envelope_capsules(capsules)
    assert capsules == before
    assert result == [large, clipped]


def test_over_limit_capsules_can_compact_only_when_complete_envelopes_are_contained():
    outer = {'start': [0, 0, 0], 'end': [1000, 0, 0], 'radius': 5}
    inner = [{'start': [index + 1, 0, 0], 'end': [index + 1, 0, 0], 'radius': 2}
             for index in range(300)]
    assert cad_import._compact_envelope_capsules([*inner, outer]) == [outer]
    separate = [{'start': [index * 20, 0, 0], 'end': [index * 20, 0, 0], 'radius': 2}
                for index in range(300)]
    assert len(cad_import._compact_envelope_capsules(separate)) == 300


@pytest.mark.parametrize('clearance', [0, 0.1, 50.8, 250])
def test_proven_containment_still_covers_capsule_after_common_inflation(clearance):
    from pipe_router import core
    outer = {'start': [1000, -10, 30], 'end': [1040, 30, 30], 'radius': 5,
             'clip_min': [995, -15, 25], 'clip_max': [1045, 35, 35]}
    inner = {'start': [1004, -5, 30], 'end': [1034, 25, 30], 'radius': 3,
             'clip_min': [1000, -9, 27], 'clip_max': [1038, 29, 33]}
    assert cad_import._capsule_contains(outer, inner)
    shape = core._prepare_obstacle({'id': 'outer', 'min': [900, -100, -100], 'max': [1200, 100, 100],
                                    'capsules': [outer]}, 'coverage test', clearance)
    # Check both end spheres and intermediate cross sections. Points clipped
    # from the inner envelope need not be contained, so apply its same clip.
    for step in range(9):
        center = [a + (b - a) * step / 8 for a, b in zip(inner['start'], inner['end'])]
        for azimuth in range(32):
            theta = math.tau * azimuth / 32
            for elevation in (-math.pi / 2, -math.pi / 4, 0, math.pi / 4, math.pi / 2):
                r = inner['radius'] + clearance
                point = (center[0] + r * math.cos(elevation) * math.cos(theta),
                         center[1] + r * math.cos(elevation) * math.sin(theta),
                         center[2] + r * math.sin(elevation))
                if all(a - clearance <= v <= b + clearance for v, a, b in zip(point, inner['clip_min'], inner['clip_max'])):
                    assert core._segment_intersects_box(point, point, shape)


def test_preview_reduction_preserves_full_envelope_data(tmp_path):
    result = inspect_sat(tmp_path, torus_sat())
    before = deepcopy(result['solid_envelopes'])
    result['objects'] = result['objects'] * 1000
    preview = cad_import.lightweight_preview(result)
    assert preview['solid_envelopes'] == before
    assert result['solid_envelopes'] == before


def test_malformed_trim_link_rejects_supported_preview_geometry(tmp_path):
    data = cylinder_sat()
    # The display traversal stops after these two known loops. The envelope
    # must notice that the final loop points at a vertex instead of null.
    data[3 + 7] = 'loop $-1 -1 $-1 $12 $9 $4 #'
    result = inspect_sat(tmp_path, data)
    assert result['counts']['surface_meshes'] == 1
    envelope, = result['solid_envelopes']
    assert not envelope['eligible']
    assert envelope['capsules'] == []
    assert envelope['reason'] == 'incomplete_face_trims'


def test_almost_cylinder_cone_angle_is_not_promoted_to_eligible(tmp_path):
    data = cylinder_sat()
    data[3 + 6] = data[3 + 6].replace('I I 0 1 2', 'I I 0.000000001 1 2')
    result = inspect_sat(tmp_path, data)
    assert result['counts']['surface_meshes'] == 1
    envelope, = result['solid_envelopes']
    assert not envelope['eligible']
    assert envelope['reason'] == 'nonzero_cone_angle'


def test_cylinder_clip_keeps_flat_ends_without_rounded_cap_length(tmp_path):
    result = inspect_cad(make_cylinder_dxf(tmp_path / 'flat-cylinder.dxf'))
    capsule, = result['solid_envelopes'][0]['capsules']
    assert capsule['clip_min'] == pytest.approx([8.125, 18.25, 30.5], abs=1e-6)
    assert capsule['clip_max'] == pytest.approx([12.125, 22.25, 40.5], abs=1e-6)
    # The clip retains tiny numeric tolerance but not a two millimeter cap.
    assert 0 < capsule['start'][2] - capsule['clip_min'][2] < 1e-6
    assert 0 < capsule['clip_max'][2] - capsule['end'][2] < 1e-6


def test_annulus_clip_is_a_thin_disk_box(tmp_path):
    capsule, = inspect_sat(tmp_path, annulus_sat())['solid_envelopes'][0]['capsules']
    assert capsule['clip_min'] == pytest.approx([-3, -3, 0], abs=1e-6)
    assert capsule['clip_max'] == pytest.approx([3, 3, 0], abs=1e-6)
    assert 0 < capsule['clip_max'][2] - capsule['clip_min'][2] < 1e-6


@pytest.mark.parametrize('fixture', [cylinder_sat, annulus_sat])
def test_rotated_scaled_clip_contains_all_analytic_face_samples(fixture):
    matrix = Matrix44.chain(Matrix44.x_rotate(0.63), Matrix44.z_rotate(0.48),
                            Matrix44.scale(2.75), Matrix44.translate(123, -456, 789))
    data = fixture()
    if fixture is cylinder_sat:
        data = cylinder_sat(translation=(0, 0, 0))
    envelope = envelope_for(builder_for(data), matrix)
    capsule, = envelope['capsules']
    for angle_index in range(257):
        angle = math.tau * angle_index / 257
        if fixture is cylinder_sat:
            source_points = [Vec3(2 * math.cos(angle), 2 * math.sin(angle), z) for z in (0, 5, 10)]
        else:
            source_points = [Vec3(r * math.cos(angle), r * math.sin(angle), 0) for r in (2, 2.5, 3)]
        for point in source_points:
            world = matrix.transform(point)
            assert all(capsule['clip_min'][i] <= world[i] <= capsule['clip_max'][i] for i in range(3))
    axis = matrix.transform_direction(Vec3(0, 0, 1)).normalize()
    radial_extents = [capsule['radius'] * math.sqrt(max(0, 1 - axis[i] ** 2)) for i in range(3)]
    for i in range(3):
        assert capsule['clip_min'][i] == pytest.approx(min(capsule['start'][i], capsule['end'][i]) - radial_extents[i], abs=1e-6)
        assert capsule['clip_max'][i] == pytest.approx(max(capsule['start'][i], capsule['end'][i]) + radial_extents[i], abs=1e-6)


def test_clip_bounds_are_already_world_millimeters(tmp_path):
    path = make_cylinder_dxf(tmp_path / 'scaled-clips.dxf', in_block=True)
    millimeters = inspect_cad(path, 'mm')['solid_envelopes'][0]['capsules'][0]
    inches = inspect_cad(path, 'in')['solid_envelopes'][0]['capsules'][0]
    for key in ('clip_min', 'clip_max'):
        assert inches[key] == pytest.approx([25.4 * value for value in millimeters[key]])


def test_torus_capsules_remain_unclipped(tmp_path):
    capsules = inspect_sat(tmp_path, torus_sat())['solid_envelopes'][0]['capsules']
    assert all('clip_min' not in capsule and 'clip_max' not in capsule for capsule in capsules)


def test_annulus_clip_includes_small_axial_center_offset(tmp_path):
    data = annulus_sat()
    data[3 + 15] = data[3 + 15].replace('0 0 0 0 0 1 3', '0 0 0.0000005 0 0 1 3')
    envelope, = inspect_sat(tmp_path, data)['solid_envelopes']
    assert envelope['eligible']
    capsule, = envelope['capsules']
    assert capsule['clip_min'][2] < 0
    assert capsule['clip_max'][2] > 0.0000005
