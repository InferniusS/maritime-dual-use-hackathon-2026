"""Sampled source curves are geometry evidence, not tiny fabricated elbows."""
from copy import deepcopy
import math

import pytest

from pipe_router.assembly import plan_assembly, reconstruct_sampled_arcs
from pipe_router.workflow import run_workflow


def quarter_curve(radius=100, count=12):
    points = [[0., 0., 0.], [100., 0., 0.]]
    points += [[400 + radius * math.cos(-math.pi/2 + math.pi/2 * i/count),
                radius + radius * math.sin(-math.pi/2 + math.pi/2 * i/count), 0.]
               for i in range(count + 1)]
    # Snap exact tangent ends; preserve these actual endpoint records verbatim.
    points[2] = [400., 0., 0.]
    points[-1] = [400. + radius, radius, 0.]
    points += [[400. + radius, 800., 0.], [400. + radius, 900., 0.]]
    return points


def source(points, radius=200):
    return {"name": "sampled circular geometry test", "units": "mm",
            "provenance": "cad-derived", "system_type": "pressurized",
            "rule_profile": "prototype-unverified",
            "bounds": {"min": [-2000, -2000, -2000], "max": [4000, 4000, 4000]},
            "pipe": {"start": points[0], "end": points[-1], "diameter": 20,
                     "original_route": points},
            "obstacles": [], "rules": {"clearance": 20, "grid_step": 250, "bend_penalty": 300},
            "proposal": {"mode": "rounded_pipe", "bend_radius_mm": radius}}


def rotate(points, angles):
    """A genuine 3D rigid transform, independent of the recognition algorithm."""
    result = []
    for point in points:
        x, y, z = point
        a, b, c = angles
        y, z = y*math.cos(a)-z*math.sin(a), y*math.sin(a)+z*math.cos(a)
        x, z = x*math.cos(b)+z*math.sin(b), -x*math.sin(b)+z*math.cos(b)
        x, y = x*math.cos(c)-y*math.sin(c), x*math.sin(c)+y*math.cos(c)
        result.append([x+1200, y+700, z+500])
    return result


@pytest.mark.parametrize('angles', [(0,0,0), (.37,.61,-.43), (-.92,.24,1.12)])
def test_sampled_quarter_bend_has_one_geometric_seed_with_fixed_endpoints(angles):
    points = rotate(quarter_curve(), angles)
    identified = reconstruct_sampled_arcs(points)
    assert len(identified['arcs']) == 1
    arc = identified['arcs'][0]
    assert arc['sample_count'] == 13
    assert arc['observed_radius_mm'] == pytest.approx(100)
    assert arc['observed_angle_deg'] == pytest.approx(90)
    assert arc['identity_verified'] is False
    assert identified['points'][0] == points[0]
    assert identified['points'][-1] == points[-1]
    expected = rotate([[500,0,0]], angles)[0]
    assert arc['construction_vertex'] == pytest.approx(expected)


@pytest.mark.parametrize('angles', [(0,0,0), (.37,.61,-.43), (-.92,.24,1.12)])
def test_requested_radius_is_used_on_rotated_sampled_seed_and_stubs_are_exact(monkeypatch, angles):
    # One candidate forces this regression to exercise the reconstructed seed,
    # rather than accidentally succeeding via a later world-axis alternative.
    monkeypatch.setattr('pipe_router.proposal.MAX_ALTERNATIVES', 1)
    points = rotate(quarter_curve(), angles)
    layout = source(points)
    layout['protected_endpoints'] = [
        {'endpoint':'start', 'points':deepcopy(points[:2]), 'obstacle_ids':[], 'max_distance':100.01},
        {'endpoint':'end', 'points':deepcopy(points[-2:]), 'obstacle_ids':[], 'max_distance':100.01},
    ]
    result = run_workflow(layout)
    assembly = result['assembly']
    assert assembly['status'] == 'ready'
    assert assembly['metrics']['bend_count'] == 1
    assert assembly['bend_radius_mm'] == 200
    assert next(c for c in assembly['components'] if c['kind']=='bend')['radius_mm'] == 200
    assert result['candidate']['points'][:2] == points[:2]
    assert result['candidate']['points'][-2:] == points[-2:]
    assert assembly['centerline_points'][:2] == points[:2]
    assert assembly['centerline_points'][-2:] == points[-2:]
    assert assembly['baseline_status'] == 'needs_review'
    assert assembly['baseline_metrics'] is None
    assert assembly['catalog_verified'] is False
    assert assembly['sampled_curve_review']['arcs'][0]['observed_radius_mm'] == pytest.approx(100)
    assert {f['code'] for f in assembly['baseline_findings']} == {'baseline_sampled_arcs'}


@pytest.mark.parametrize('bad_point', ['off_circle', 'out_of_plane', 'non_tangent'])
def test_ambiguous_curve_is_not_silently_reconstructed(bad_point):
    points = quarter_curve()
    if bad_point == 'off_circle':
        points[8][0] += .02
    elif bad_point == 'out_of_plane':
        points[8][2] += .02
    else:
        points[1][1] += 1
    identified = reconstruct_sampled_arcs(points)
    assert identified['points'] is None
    assert identified['arcs'] == []


def test_true_short_leg_between_two_bends_is_still_rejected_at_requested_radius():
    first = quarter_curve()[2:15]
    second = [[600 + 100*math.cos(math.pi-math.pi/2*i/12),
               110 + 100*math.sin(math.pi-math.pi/2*i/12), 0.] for i in range(13)]
    second[0], second[-1] = [500.,110.,0.], [600.,210.,0.]
    points = [[0.,0.,0.]] + first + second + [[1200.,210.,0.]]
    identified = reconstruct_sampled_arcs(points)
    assert len(identified['arcs']) == 2
    proposal = plan_assembly(source(points), identified['points'], bend_radius_mm=200)
    assert proposal['status'] == 'blocked'
    assert proposal['meshes'] == []
    issue = next(f for f in proposal['findings'] if f['code']=='assembly_insufficient_tangent_length')
    assert issue['available_length_mm'] == pytest.approx(210)
    assert issue['required_length_mm'] == pytest.approx(400)
    assert proposal['bend_radius_mm'] == 200


def test_protected_curve_samples_cannot_be_removed_by_reconstruction():
    points = quarter_curve()
    fixed = [{'endpoint':'start', 'points':deepcopy(points[:5]), 'obstacle_ids':[], 'max_distance':600}]
    original = deepcopy(points)
    identified = reconstruct_sampled_arcs(points, fixed)
    assert len(identified['arcs']) == 1
    assert identified['arcs'][0]['protected_samples'] is True
    assert identified['points'] is None
    assert points == original


def test_protected_tangent_boundary_stays_exact_and_larger_radius_cannot_consume_it():
    points = quarter_curve()
    fixed = [{'endpoint':'start', 'points':deepcopy(points[:3]), 'obstacle_ids':[], 'max_distance':400}]
    identified = reconstruct_sampled_arcs(points, fixed)
    assert identified['points'][:3] == fixed[0]['points']
    layout = source(points)
    layout['protected_endpoints'] = fixed
    assembly = plan_assembly(layout, identified['points'], bend_radius_mm=200)
    assert assembly['status'] == 'blocked'
    assert any(f['code']=='assembly_bend_enters_fixed_stub' for f in assembly['findings'])


def test_reconstruction_does_not_waive_unverified_endpoint_contacts():
    layout = source(quarter_curve())
    layout['obstacles'] = [{'id':'unknown-end-contact','min':[-30,-30,-30], 'max':[30,30,30]}]
    result = run_workflow(layout)
    assert result['status'] == 'no_route'
    assert result['candidate'] is None
    assert result['assembly']['meshes'] == []
    assert result['centerline_search']['expanded_states'] == 0
    assert 'unknown-end-contact' in result['search']['reason']
    assert result['assembly']['sampled_curve_review']['points']
    assert not result['normalized_layout'].get('protected_endpoints')
    assert not any(f['code']=='assembly_insufficient_tangent_length'
                   for f in result['assembly']['baseline_findings'])


def test_sampled_elbow_clearance_repair_uses_requested_radius_without_waiving_obstacle(monkeypatch):
    monkeypatch.setattr('pipe_router.proposal.MAX_ALTERNATIVES', 1)
    points = quarter_curve(radius=200)
    layout = source(points, radius=400)
    layout['protected_endpoints'] = [
        {'endpoint':'start', 'points':deepcopy(points[:2]), 'obstacle_ids':[], 'max_distance':100.01},
        {'endpoint':'end', 'points':deepcopy(points[-2:]), 'obstacle_ids':[], 'max_distance':100.01},
    ]
    # A small obstruction touches the old 200 mm circular elbow. The explicitly
    # requested 400 mm elbow moves away; all obstacle geometry remains active.
    touch = [400 + 200/math.sqrt(2), 200 - 200/math.sqrt(2), 0]
    layout['obstacles'] = [{'id':'fixed-obstruction',
                            'min':[p-5 for p in touch], 'max':[p+5 for p in touch]}]
    original_layout = deepcopy(layout)
    result = run_workflow(layout)
    assert result['original']['valid'] is False
    assert result['status'] == 'improved'
    assert result['assembly']['status'] == 'ready'
    assert result['assembly']['change_type'] == 'clearance_repair'
    assert result['assembly']['bend_radius_mm'] == 400
    assert result['assembly']['sampled_curve_review']['arcs'][0]['observed_radius_mm'] == pytest.approx(200)
    assert result['normalized_layout']['obstacles'] == original_layout['obstacles']
    assert all(stub['obstacle_ids'] == [] for stub in result['normalized_layout']['protected_endpoints'])
    assert result['candidate']['points'][:2] == points[:2]
    assert result['candidate']['points'][-2:] == points[-2:]
    assert result['assembly']['baseline_metrics'] is None
    assert layout == original_layout


def test_blocked_contact_message_uses_traceable_source_handle():
    layout = source(quarter_curve())
    layout['obstacles'] = [{'id':'cad-solid-test','min':[-30,-30,-30], 'max':[30,30,30]}]
    layout['cad_context'] = {'obstacle_sources':[{'id':'cad-solid-test','source_handle':'TEST_HANDLE'}]}
    result = run_workflow(layout)
    assert 'start: TEST_HANDLE (cad-solid-test)' in result['search']['reason']
    assert result['status'] == 'no_route'


def test_duplicate_original_sample_does_not_abort_independent_repair_search():
    points = [[0,0,0], [1000,0,0], [1000,0,0], [2000,0,0]]
    layout = source(points)
    original = deepcopy(layout)
    result = run_workflow(layout)
    assert result['status'] == 'improved'
    assert result['assembly']['status'] == 'ready'
    assert result['candidate']['valid'] is True
    assert result['candidate']['points'] == [[0,0,0], [2000,0,0]]
    assert result['assembly']['sampled_curve_review']['status'] == 'needs_review'
    assert any(f['code']=='sampled_curve_recognition_unavailable' and f['status']=='needs_review'
               for f in result['assembly']['findings'])
    assert result['normalized_layout']['pipe']['original_route'] == points
    assert layout == original


def test_oversized_sampled_reference_remains_bounded_and_can_have_separate_short_proposal():
    # More than the 512 non-collinear assembly-point limit, but within the
    # supported input size. This is not a circular arc and is not simplified
    # into the original baseline or a claimed original fitting inventory.
    points = [[float(i*3), float(i%2), 0.] for i in range(600)]
    layout = source(points)
    original = deepcopy(layout)
    result = run_workflow(layout)
    assert result['assembly']['baseline_status'] == 'blocked'
    assert any(f['code']=='assembly_size_limit' for f in result['assembly']['baseline_findings'])
    assert result['assembly']['search']['evaluated_candidates'] <= result['assembly']['search']['max_candidates']
    assert result['status'] == 'improved'
    assert result['assembly']['status'] == 'ready'
    assert result['candidate']['points'][0] == points[0]
    assert result['candidate']['points'][-1] == points[-1]
    assert any(f['code']=='reference_unavailable' for f in result['assembly']['findings'])
    assert result['assembly']['baseline_metrics'] is None
    assert result['normalized_layout']['pipe']['original_route'] == points
    assert layout == original
