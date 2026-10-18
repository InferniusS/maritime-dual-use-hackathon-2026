"""Continuous obstacle-envelope checks used by both routing and connections."""

from copy import deepcopy
import math

import pytest

from pipe_router import core
from pipe_router.collision import Capsule, merge_intervals, segment_capsule_intervals, segment_intersects_capsule


@pytest.mark.parametrize("start,end,expected", [
    ((5, -3, 0), (5, 3, 0), True),
    ((5, -3, 1), (5, 3, 1), True),
    ((5, -3, 1.001), (5, 3, 1.001), False),
    ((-3, 0, 0), (-0.5, 0, 0), True),
    ((-3, 0, 0), (-1.001, 0, 0), False),
    ((11, -2, 0), (11, 2, 0), True),
    ((11.001, -2, 0), (11.001, 2, 0), False),
    ((-3, 1, 0), (13, 1, 0), True),
    ((-3, 1.001, 0), (13, 1.001, 0), False),
    ((5, 0, 0), (5, 0, 0), True),
    ((5, 1, 0), (5, 1, 0), True),
    ((5, 1.001, 0), (5, 1.001, 0), False),
])
def test_capsule_closed_sides_and_end_caps(start, end, expected):
    capsule = Capsule((0, 0, 0), (10, 0, 0), 1)
    assert segment_intersects_capsule(start, end, capsule) is expected
    assert bool(segment_capsule_intervals(start, end, capsule)) is expected


def test_sphere_and_degenerate_moving_point():
    sphere = Capsule((0, 0, 0), (0, 0, 0), 2)
    intervals = segment_capsule_intervals((-4, 0, 0), (4, 0, 0), sphere, tolerance=0)
    assert intervals[0] == pytest.approx((0.25, 0.75))
    assert segment_capsule_intervals((2, 0, 0), (2, 0, 0), sphere, tolerance=0) == [(0, 1)]
    assert segment_capsule_intervals((2.001, 0, 0), (2.001, 0, 0), sphere) == []


def test_parallel_contact_interval_covers_both_rounded_ends():
    capsule = Capsule((0, 0, 0), (10, 0, 0), 1)
    intervals = segment_capsule_intervals((-2, 0.6, 0), (12, 0.6, 0), capsule, tolerance=0)
    assert len(intervals) == 1
    assert intervals[0] == pytest.approx((1.2 / 14, 12.8 / 14))


def test_exact_tangent_has_a_closed_single_point_interval():
    capsule = Capsule((0, 0, 0), (10, 0, 0), 1)
    intervals = segment_capsule_intervals((5, -2, 1), (5, 2, 1), capsule, tolerance=0)
    assert len(intervals) == 1
    assert intervals[0] == pytest.approx((0.5, 0.5), abs=1e-8)


def test_thin_crossing_between_any_regular_samples_is_found():
    capsule = Capsule((123.4567, 0, -1), (123.4567, 0, 1), 0.0001)
    intervals = segment_capsule_intervals((0, 0, 0), (1_000_000, 0, 0), capsule, tolerance=0)
    assert len(intervals) == 1
    assert intervals[0] == pytest.approx(((123.4567 - 0.0001) / 1_000_000, (123.4567 + 0.0001) / 1_000_000), abs=1e-15)


def test_skew_segments_clear_and_cross_without_shared_endpoints():
    capsule = Capsule((-2, -2, 0), (2, 2, 0), 0.5)
    assert segment_intersects_capsule((-2, 2, 0.49), (2, -2, 0.49), capsule)
    assert not segment_intersects_capsule((-2, 2, 0.51), (2, -2, 0.51), capsule)


def test_contact_intervals_reverse_and_translate_consistently():
    start, end = (-4, 0.5, 0.25), (14, 0.5, 0.25)
    capsule = Capsule((0, 0, 0), (10, 0, 0), 1)
    base = segment_capsule_intervals(start, end, capsule)
    reversed_intervals = segment_capsule_intervals(end, start, Capsule(capsule.end, capsule.start, 1))
    assert reversed_intervals[0] == pytest.approx((1 - base[0][1], 1 - base[0][0]))
    shift = (29251, 2715, 3071)
    translated = lambda point: tuple(x + y for x, y in zip(point, shift))
    shifted = segment_capsule_intervals(translated(start), translated(end), Capsule(translated(capsule.start), translated(capsule.end), 1))
    assert shifted[0] == pytest.approx(base[0], abs=1e-10)


def test_oblique_capsule_interval_is_rigid_rotation_invariant():
    cosine, sine = math.cos(0.731), math.sin(0.731)

    def rotate(point):
        x, y, z = point
        x, y = cosine * x - sine * y, sine * x + cosine * y
        y, z = cosine * y - sine * z, sine * y + cosine * z
        return (x + 29251, y + 2715, z + 3071)

    capsule = Capsule(rotate((0, 0, 0)), rotate((10, 0, 0)), 1)
    intervals = segment_capsule_intervals(rotate((-2, 0.6, 0)), rotate((12, 0.6, 0)), capsule, tolerance=0)
    assert len(intervals) == 1
    assert intervals[0] == pytest.approx((1.2 / 14, 12.8 / 14), abs=1e-10)


def test_interval_union_preserves_a_real_free_gap():
    intervals = merge_intervals([(0.1, 0.2), (0.2, 0.3), (0.31, 0.4), (0.11, 0.25)])
    assert intervals == [(0.1, 0.3), (0.31, 0.4)]


def capsule_record():
    return {"id": "pipe", "min": [-1, -1, -1], "max": [11, 1, 1], "capsules": [
        {"start": [0, 0, 0], "end": [10, 0, 0], "radius": 1},
    ]}


def test_preparation_inflates_source_radius_once_and_keeps_broad_bounds():
    obstacle = core._prepare_obstacle(capsule_record(), "obstacle", 2)
    assert obstacle.minimum == (-3, -3, -3)
    assert obstacle.maximum == (13, 3, 3)
    assert obstacle.capsules[0].radius == 3
    assert core._segment_intersects_box((5, 3, 0), (5, 3, 0), obstacle)
    assert not core._segment_intersects_box((5, 3.001, 0), (5, 3.001, 0), obstacle)


def test_capsule_removes_empty_box_corner_without_removing_obstacle():
    record = capsule_record()
    capsule = core._prepare_obstacle(record, "obstacle", 0)
    fallback_record = {key: value for key, value in record.items() if key != "capsules"}
    fallback = core._prepare_obstacle(fallback_record, "obstacle", 0)
    start, end = (1, 0.9, 0.9), (9, 0.9, 0.9)
    assert core._segment_intersects_box(start, end, fallback)
    assert not core._segment_intersects_box(start, end, capsule)
    assert core._segment_intersects_box((5, -2, 0), (5, 2, 0), capsule)


def test_obstacle_contact_intervals_follow_union_not_whole_box():
    obstacle = core._Box("two", (-2, -1, -1), (12, 1, 1), (
        Capsule((-1, 0, 0), (-1, 0, 0), 1),
        Capsule((11, 0, 0), (11, 0, 0), 1),
    ))
    intervals = core._segment_obstacle_intervals((-4, 0, 0), (14, 0, 0), obstacle)
    assert len(intervals) == 2
    assert intervals[0] == pytest.approx((2 / 18, 4 / 18))
    assert intervals[1] == pytest.approx((14 / 18, 16 / 18))
    assert core._segment_obstacle_intervals((5, 0, 0), (5, 0, 0), obstacle) == []


@pytest.mark.parametrize("mutate", [
    lambda record: record.update(capsules=[]),
    lambda record: record.update(capsules=None),
    lambda record: record.update(capsules=[1]),
    lambda record: record["capsules"][0].update(radius=0),
    lambda record: record["capsules"][0].update(radius=-1),
    lambda record: record["capsules"][0].update(radius=math.inf),
    lambda record: record["capsules"][0].update(radius=True),
    lambda record: record["capsules"][0].update(start=[0, 0]),
    lambda record: record["capsules"][0].update(end=[math.nan, 0, 0]),
    lambda record: record["capsules"][0].update(radius=2),
    lambda record: record["capsules"][0].update(end=[11, 0, 0]),
])
def test_bad_capsule_geometry_is_rejected_before_broadphase_can_hide_it(mutate):
    record = capsule_record()
    mutate(record)
    with pytest.raises(ValueError):
        core._prepare_obstacle(record, "obstacle", 0)


def test_capsule_count_limit_is_enforced():
    record = capsule_record()
    record["capsules"] *= core.MAX_CAPSULES_PER_OBSTACLE + 1
    with pytest.raises(ValueError, match="capsule records"):
        core._prepare_obstacle(record, "obstacle", 0)


def layout_record():
    return {
        "provenance": "synthetic", "units": "mm", "system_type": "pressurized",
        "bounds": {"min": [-10, -10, -10], "max": [20, 10, 10]},
        "pipe": {"start": [-5, 0, 0], "end": [15, 0, 0], "diameter": 1,
                 "original_route": [[-5, 0, 0], [15, 0, 0]]},
        "rules": {"clearance": 0.5, "grid_step": 2, "bend_penalty": 0},
        "obstacles": [capsule_record()],
    }


def test_routing_avoids_capsule_and_independent_validation_rechecks_it():
    layout = layout_record()
    result = core.solve_layout(layout)
    assert result["status"] == "improved"
    assert not result["original"]["valid"]
    assert result["candidate"]["valid"]
    assert core.validate_route(layout, result["candidate"]["points"])["valid"]
    assert not core.validate_route(layout, [[-5, 0, 0], [15, 0, 0]])["valid"]


def test_blocked_endpoint_diagnostics_identify_original_obstacle_and_point():
    layout = layout_record()
    layout["pipe"].update(start=[5, 0, 0], original_route=[[5, 0, 0], [15, 0, 0]])
    result = core.solve_layout(layout)
    assert result["status"] == "no_route"
    assert result["search"]["expanded_states"] == 0
    assert result["search"]["blocked_endpoints"] == [{"endpoint": "start", "point": [5.0, 0.0, 0.0], "obstacle": "pipe"}]


def test_straight_route_through_empty_capsule_box_corner_is_retained():
    layout = layout_record()
    layout["pipe"].update(start=[1, 1.8, 1.8], end=[9, 1.8, 1.8], original_route=[[1, 1.8, 1.8], [9, 1.8, 1.8]])
    result = core.solve_layout(layout)
    assert result["status"] == "unchanged"
    assert result["original"]["valid"]
    fallback = deepcopy(layout)
    fallback["obstacles"][0].pop("capsules")
    assert not core.validate_route(fallback, fallback["pipe"]["original_route"])["valid"]


def flat_cylinder_record():
    return {"id": "flat-end-cylinder", "min": [-2, -2, 0], "max": [2, 2, 10], "capsules": [{
        "start": [0, 0, 0], "end": [0, 0, 10], "radius": 2,
        "clip_min": [-2, -2, 0], "clip_max": [2, 2, 10],
    }]}


def test_clipped_cylinder_preserves_flat_end_contacts_and_excludes_far_caps():
    obstacle = core._prepare_obstacle(flat_cylinder_record(), "cylinder", 0)
    assert core._segment_intersects_box((0, 0, 10), (0, 0, 10), obstacle)
    assert not core._segment_intersects_box((0, 0, 10.001), (0, 0, 11), obstacle)
    assert core._segment_intersects_box((-3, 0, 5), (3, 0, 5), obstacle)
    assert not core._segment_intersects_box((-3, 0, 11), (3, 0, 11), obstacle)


def test_clip_and_capsule_clearance_are_inflated_once_with_same_margin():
    obstacle = core._prepare_obstacle(flat_cylinder_record(), "cylinder", 1)
    capsule = obstacle.capsules[0]
    assert capsule.radius == 3
    assert capsule.clip_min == (-3, -3, -1)
    assert capsule.clip_max == (3, 3, 11)
    assert core._segment_intersects_box((0, 0, 11), (0, 0, 11), obstacle)
    assert not core._segment_intersects_box((0, 0, 11.001), (0, 0, 11.001), obstacle)
    intervals = core._segment_obstacle_intervals((0, 0, -5), (0, 0, 15), obstacle)
    assert len(intervals) == 1
    assert intervals[0] == pytest.approx((0.2, 0.8), abs=1e-8)


def test_capsule_and_clip_must_intersect_at_the_same_segment_parameter():
    unclipped = Capsule((0, 0, 0), (0, 0, 0), 2)
    clipped = Capsule((0, 0, 0), (0, 0, 0), 2, (1, -5, -5), (2, 5, 5))
    start, end = (-4, 0, 0), (4, 4, 0)
    assert segment_intersects_capsule(start, end, unclipped)
    # The query reaches the clip box only after leaving the sphere.
    assert not segment_intersects_capsule(start, end, clipped)
    assert segment_capsule_intervals(start, end, clipped) == []


def test_planar_disk_clip_with_degenerate_axis_and_zero_clip_thickness():
    record = {"id": "disk", "min": [-5, -5, -0.001], "max": [5, 5, 0.001], "capsules": [{
        "start": [0, 0, 0], "end": [0, 0, 0], "radius": 5,
        "clip_min": [-5, -5, 0], "clip_max": [5, 5, 0],
    }]}
    disk = core._prepare_obstacle(record, "disk", 0)
    assert core._segment_intersects_box((0, 0, 0), (0, 0, 0), disk)
    assert not core._segment_intersects_box((0, 0, 1), (0, 0, 1), disk)
    inflated = core._prepare_obstacle(record, "disk", 2)
    intervals = core._segment_obstacle_intervals((0, 0, -10), (0, 0, 10), inflated)
    assert len(intervals) == 1
    assert intervals[0] == pytest.approx((0.4, 0.6), abs=1e-8)


def test_clipped_envelope_bounds_accept_effective_shape_instead_of_unused_caps():
    record = flat_cylinder_record()
    assert core._prepare_obstacle(record, "cylinder", 0).maximum == (2, 2, 10)
    record["capsules"][0].pop("clip_min")
    record["capsules"][0].pop("clip_max")
    with pytest.raises(ValueError, match="envelope must fit"):
        core._prepare_obstacle(record, "cylinder", 0)


@pytest.mark.parametrize("mutate", [
    lambda capsule: capsule.pop("clip_min"),
    lambda capsule: capsule.pop("clip_max"),
    lambda capsule: capsule.update(clip_min=[0, 0]),
    lambda capsule: capsule.update(clip_max=[0, 0, math.inf]),
    lambda capsule: capsule.update(clip_min=[3, -2, 0]),
    lambda capsule: capsule.update(clip_min=[-2, -2, 20], clip_max=[2, 2, 30]),
    lambda capsule: capsule.update(clip_max=[2, 2, 11]),
])
def test_bad_clipping_is_rejected(mutate):
    record = flat_cylinder_record()
    mutate(record["capsules"][0])
    with pytest.raises(ValueError):
        core._prepare_obstacle(record, "cylinder", 0)
